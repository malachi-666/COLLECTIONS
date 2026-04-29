import subprocess
import tempfile
import os
import signal
from typing import Optional

class SignalValidator:
    """
    A validator class for network environmental auditing and verifying the behavior of legacy IoT devices.
    """

    def __init__(self) -> None:
        """
        Initializes the SignalValidator.
        """
        self._process: Optional[subprocess.Popen] = None
        self._hostapd_conf_path: Optional[str] = None
        self._interface: Optional[str] = None

    def start_audit_beacon(self, interface: str, target_ssid: str) -> None:
        """
        Initiates a localized 802.11 management frame broadcast using hostapd.

        This method generates a temporary hostapd configuration file and starts a subprocess
        to emit the specified beacon, simulating a network state to audit legacy device behavior.

        Args:
            interface (str): The network interface to use (e.g., 'wlan0').
            target_ssid (str): The SSID to broadcast in the beacon.

        Raises:
            RuntimeError: If an audit beacon is already running.
        """
        if self._process is not None:
            raise RuntimeError("Audit beacon is already running. Call stop_audit() first.")

        self._interface = interface

        # Create a temporary hostapd configuration file
        fd, self._hostapd_conf_path = tempfile.mkstemp(suffix=".conf", prefix="audit_hostapd_")

        hostapd_config = f"""interface={interface}
driver=nl80211
ssid={target_ssid}
hw_mode=g
channel=6
auth_algs=1
wpa=0
"""
        with os.fdopen(fd, 'w') as f:
            f.write(hostapd_config)

        # Bring the interface up and set it to a compatible state (optional but good practice)
        subprocess.run(["ip", "link", "set", interface, "up"], capture_output=True, check=False)

        # Start hostapd to broadcast the beacon
        self._process = subprocess.Popen(
            ["hostapd", self._hostapd_conf_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid  # Create a new process group to cleanly kill it later
        )

    def stop_audit(self) -> None:
        """
        Cleanly terminates all subprocesses and restores the interface state.

        This method stops the hostapd process, removes the temporary configuration file,
        and optionally brings the interface down.
        """
        if self._process is not None:
            try:
                # Send SIGTERM to the process group to ensure hostapd and any children stop
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                self._process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                # Force kill if it doesn't respond or is already dead
                if self._process.poll() is None:
                    os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
            finally:
                self._process = None

        # Clean up the temporary hostapd configuration file
        if self._hostapd_conf_path and os.path.exists(self._hostapd_conf_path):
            os.remove(self._hostapd_conf_path)
            self._hostapd_conf_path = None

        # Restore the interface state (e.g., bring it down, or leave it up depending on environment)
        if self._interface:
            subprocess.run(["ip", "link", "set", self._interface, "down"], capture_output=True, check=False)
            self._interface = None
