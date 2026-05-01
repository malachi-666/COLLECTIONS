import threading
import time
from typing import Optional, Any, Callable, Dict

from scapy.all import sniff, Dot11, Dot11Elt, RadioTap, Dot11ProbeReq, sendp

from src.config import AppConfig
from src.utils.logger import AuditLogger

class TransparencyAuditor:
    """
    Audits the local RF environment for structural transparency.
    Supports Tactical Hybridity: Stealth (Passive) and Diagnostic (Active Probes).
    """

    def __init__(self, config: AppConfig, logger: AuditLogger, on_device_seen: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self.config = config
        self.logger = logger
        self.on_device_seen = on_device_seen
        self._stop_event = threading.Event()
        self._audit_thread: Optional[threading.Thread] = None
        self._probe_thread: Optional[threading.Thread] = None
        self.interface: Optional[str] = None

        self.active_mode = False # Toggle for Option C (Stealth by default)
        self._beacon_timestamps: Dict[str, float] = {}

    def toggle_active_mode(self, enabled: bool):
        """Switches between passive stealth monitoring and active diagnostic probing."""
        self.active_mode = enabled
        if enabled and self.interface and not (self._probe_thread and self._probe_thread.is_alive()):
            self._probe_thread = threading.Thread(target=self._active_probe_loop, daemon=True)
            self._probe_thread.start()

    def _active_probe_loop(self):
        """Option B: Transmits active probe requests intermittently to elicit hidden nodes."""
        while not self._stop_event.is_set() and self.active_mode:
            try:
                if self.interface:
                    # Broadcast generic Probe Request
                    probe = RadioTap() / Dot11(type=0, subtype=4, addr1="ff:ff:ff:ff:ff:ff", addr2="00:11:22:33:44:55", addr3="ff:ff:ff:ff:ff:ff") / Dot11ProbeReq() / Dot11Elt(ID="SSID", info="")
                    sendp(probe, iface=self.interface, verbose=0)
            except Exception:
                pass
            time.sleep(5) # Delay to minimize RF footprint even when active

    def _packet_handler(self, packet: Any) -> None:
        if self._stop_event.is_set():
            return

        if not packet.haslayer(Dot11):
            return

        if packet.type == 0 and packet.subtype in (8, 4, 5): # Beacon, ProbeReq, ProbeResp
            mac_address = packet.addr2
            if not mac_address:
                return

            mac_address = mac_address.upper()

            ssid = None
            is_hidden = False
            if packet.haslayer(Dot11Elt):
                try:
                    p = packet[Dot11Elt]
                    while isinstance(p, Dot11Elt):
                        if p.ID == 0:
                            # Detect hidden SSID (null bytes or empty)
                            raw_info = p.info
                            if not raw_info or all(b == 0 for b in raw_info):
                                is_hidden = True
                                ssid = "<HIDDEN>"
                            else:
                                ssid = raw_info.decode('utf-8', errors='ignore')
                            break
                        p = p.payload
                except Exception:
                    pass

            rssi = None
            if packet.haslayer(RadioTap):
                try:
                    if hasattr(packet[RadioTap], 'dBm_AntSignal'):
                         rssi = packet[RadioTap].dBm_AntSignal
                except Exception:
                    pass

            # Detect rapid beacon anomaly (e.g. interval < 0.05s)
            is_rapid = False
            current_time = time.time()
            if packet.subtype == 8: # Only track beacon intervals
                last_time = self._beacon_timestamps.get(mac_address)
                if last_time and (current_time - last_time) < 0.05:
                    is_rapid = True
                self._beacon_timestamps[mac_address] = current_time

            self._analyze_signature(mac_address, ssid, rssi, is_hidden, is_rapid)

    def _analyze_signature(self, mac_address: str, ssid: Optional[str], rssi: Optional[int], is_hidden: bool, is_rapid: bool) -> None:
        is_anomaly = False
        device_class = "Unknown"

        for sig in self.config.signatures:
            if mac_address.startswith(sig.mac_oui.upper()):
                if sig.expected_ssid and sig.expected_ssid != ssid and not is_hidden:
                    continue

                if rssi is not None and rssi < sig.signal_threshold_dbm:
                    continue

                is_anomaly = True
                device_class = sig.device_class

                event_data = {
                    "event_type": "hardware_signature_match",
                    "device_class": device_class,
                    "intercepted_mac": mac_address,
                    "intercepted_ssid": ssid,
                    "intercepted_rssi": rssi,
                    "is_hidden": is_hidden,
                    "is_rapid": is_rapid
                }
                self.logger.log_event(event_data)
                break

        if self.on_device_seen:
            self.on_device_seen({
                "mac": mac_address,
                "ssid": ssid or "<unknown>",
                "rssi": rssi,
                "is_anomaly": is_anomaly,
                "device_class": device_class,
                "is_hidden": is_hidden,
                "is_rapid": is_rapid
            })

    def _sniff_loop(self) -> None:
        def stop_filter(p: Any) -> bool:
            return self._stop_event.is_set()

        try:
            sniff(iface=self.interface, prn=self._packet_handler, stop_filter=stop_filter, store=0)
        except Exception as e:
            self.logger.log_event({"event_type": "auditor_error", "error": str(e)})

    def start_audit(self, interface: str) -> None:
        if self._audit_thread and self._audit_thread.is_alive():
            raise RuntimeError("Audit is already running.")

        self.interface = interface
        self._stop_event.clear()

        self._audit_thread = threading.Thread(target=self._sniff_loop, daemon=True)
        self._audit_thread.start()

        if self.active_mode:
             self._probe_thread = threading.Thread(target=self._active_probe_loop, daemon=True)
             self._probe_thread.start()

    def stop_audit(self) -> None:
        self._stop_event.set()
        if self._audit_thread:
            self._audit_thread.join(timeout=2.0)
            self._audit_thread = None
        if self._probe_thread:
            self._probe_thread.join(timeout=2.0)
            self._probe_thread = None
        self.interface = None
