import subprocess
import tempfile
import os
import signal
import shutil
from typing import Optional

class SignalValidator:
    """
    A validator class for network environmental auditing and verifying the behavior of legacy IoT devices.
    """

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._hostapd_conf_path: Optional[str] = None
        self._interface: Optional[str] = None

    def _check_dependencies(self):
        if not shutil.which("hostapd"):
            raise RuntimeError("hostapd is not installed or not in PATH.")
        if not shutil.which("ip"):
            raise RuntimeError("ip command is not installed or not in PATH.")

    def start_audit_beacon(self, interface: str, target_ssid: str) -> None:
        if self._process is not None:
            raise RuntimeError("Audit beacon is already running. Call stop_audit() first.")

        self._check_dependencies()
        self._interface = interface

        try:
            fd, self._hostapd_conf_path = tempfile.mkstemp(suffix=".conf", prefix="audit_hostapd_")
            hostapd_config = f"""interface={interface}\ndriver=nl80211\nssid={target_ssid}\nhw_mode=g\nchannel=6\nauth_algs=1\nwpa=0\n"""
            with os.fdopen(fd, 'w') as f:
                f.write(hostapd_config)

            subprocess.run(["ip", "link", "set", interface, "up"], capture_output=True, check=False)

            self._process = subprocess.Popen(
                ["hostapd", self._hostapd_conf_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid
            )
        except Exception as e:
            self.stop_audit() # Clean up any partial state
            raise RuntimeError(f"Failed to start validation beacon: {e}")

    def stop_audit(self) -> None:
        if self._process is not None:
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                self._process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired, PermissionError):
                if self._process.poll() is None:
                    try:
                        os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
            finally:
                self._process = None

        if self._hostapd_conf_path and os.path.exists(self._hostapd_conf_path):
            try:
                os.remove(self._hostapd_conf_path)
            except OSError:
                pass
            self._hostapd_conf_path = None

        if self._interface:
            subprocess.run(["ip", "link", "set", self._interface, "down"], capture_output=True, check=False)
            self._interface = None
