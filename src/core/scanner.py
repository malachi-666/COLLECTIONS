import threading
from typing import Optional, Any

from scapy.all import sniff, Dot11, Dot11Elt, RadioTap

from src.config import AppConfig
from src.utils.logger import AuditLogger

class TransparencyAuditor:
    """
    Audits the local RF environment for structural transparency by detecting unencrypted signal leakage.
    Cross-references intercepted metadata against defined hardware signatures.
    """

    def __init__(self, config: AppConfig, logger: AuditLogger) -> None:
        """
        Initializes the TransparencyAuditor.

        Args:
            config (AppConfig): The application configuration containing hardware signatures.
            logger (AuditLogger): The thread-safe logger for recording matches.
        """
        self.config = config
        self.logger = logger
        self._stop_event = threading.Event()
        self._audit_thread: Optional[threading.Thread] = None
        self.interface: Optional[str] = None

    def _packet_handler(self, packet: Any) -> None:
        """
        Callback for scapy.sniff. Processes 802.11 management frames.
        """
        if self._stop_event.is_set():
            return

        # Check if the packet is an 802.11 management frame
        if not packet.haslayer(Dot11):
            return

        # Type 0 is management frame. Subtype 8 is Beacon, 4 is Probe Request, 5 is Probe Response
        if packet.type == 0 and packet.subtype in (8, 4, 5):
            mac_address = packet.addr2  # Transmitter address
            if not mac_address:
                return

            mac_address = mac_address.upper()

            ssid = None
            if packet.haslayer(Dot11Elt):
                # ID 0 is the SSID parameter set
                try:
                    p = packet[Dot11Elt]
                    while isinstance(p, Dot11Elt):
                        if p.ID == 0:
                            ssid = p.info.decode('utf-8', errors='ignore')
                            break
                        p = p.payload
                except Exception:
                    pass

            rssi = None
            if packet.haslayer(RadioTap):
                # Depending on the driver, dBm_AntSignal might be present
                try:
                    # Accessing the dbm_antsignal field dynamically if it exists
                    if hasattr(packet[RadioTap], 'dBm_AntSignal'):
                         rssi = packet[RadioTap].dBm_AntSignal
                except Exception:
                    pass

            self._analyze_signature(mac_address, ssid, rssi)

    def _analyze_signature(self, mac_address: str, ssid: Optional[str], rssi: Optional[int]) -> None:
        """
        Cross-references intercepted metadata against defined hardware signatures.
        """
        for sig in self.config.signatures:
            # Check OUI prefix match
            if mac_address.startswith(sig.mac_oui.upper()):
                # If expected_ssid is defined, it must match
                if sig.expected_ssid and sig.expected_ssid != ssid:
                    continue

                # Check signal threshold if RSSI is available
                if rssi is not None and rssi < sig.signal_threshold_dbm:
                    continue

                # Match found, log the event
                event_data = {
                    "event_type": "hardware_signature_match",
                    "device_class": sig.device_class,
                    "intercepted_mac": mac_address,
                    "intercepted_ssid": ssid,
                    "intercepted_rssi": rssi,
                    "matched_oui": sig.mac_oui,
                    "threshold_dbm": sig.signal_threshold_dbm
                }
                self.logger.log_event(event_data)

    def _sniff_loop(self) -> None:
        """
        The continuous sniffing loop that runs in a daemon thread.
        """
        # stop_filter tells sniff when to stop
        def stop_filter(p: Any) -> bool:
            return self._stop_event.is_set()

        try:
            # We filter for wlan to be safe, but Dot11 checks inside handler do the heavy lifting
            sniff(iface=self.interface, prn=self._packet_handler, stop_filter=stop_filter, store=0)
        except Exception as e:
            self.logger.log_event({"event_type": "auditor_error", "error": str(e)})

    def start_audit(self, interface: str) -> None:
        """
        Starts the auditing loop in a background daemon thread.

        Args:
            interface (str): The wireless interface in monitor mode to sniff on (e.g., 'wlan0mon').
        """
        if self._audit_thread and self._audit_thread.is_alive():
            raise RuntimeError("Audit is already running.")

        self.interface = interface
        self._stop_event.clear()

        self._audit_thread = threading.Thread(target=self._sniff_loop, daemon=True)
        self._audit_thread.start()

    def stop_audit(self) -> None:
        """
        Stops the auditing loop.
        """
        self._stop_event.set()
        if self._audit_thread:
            # Wait a brief moment for the sniffer to exit cleanly
            self._audit_thread.join(timeout=2.0)
            self._audit_thread = None
        self.interface = None
