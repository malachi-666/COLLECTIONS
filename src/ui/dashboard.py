import sys
import time
import queue
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, List

from rich.console import Console
from rich.layout import Layout
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich.prompt import Prompt

from scapy.interfaces import get_if_list

from src.config import get_config, AppConfig
from src.utils.logger import AuditLogger
from src.core.scanner import TransparencyAuditor
from src.core.validator import SignalValidator
from src.ui.keyboard_listener import KeyboardListener

console = Console()

class DashboardUI:
    PAGE_SIZE = 15
    SORT_MODES = ["rssi", "anomaly", "class"]

    def __init__(self, config: AppConfig, logger: AuditLogger):
        self.config = config
        self.logger = logger
        self.devices: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()

        self.auditor = TransparencyAuditor(self.config, self.logger, on_device_seen=self.on_device_seen)
        self.validator = SignalValidator()
        self.active_interface = None

        # State Machine
        self.current_view = "MAIN" # MAIN, HELP, DETAIL, PROMPT
        self.prompt_mode = None # "VALIDATE" or "DETAIL"
        self.prompt_buffer = ""
        self.selected_detail_mac = None
        self.search_query = ""

        # Stats
        self.start_time = time.time()
        self.packet_count = 0
        self.last_packet_time = time.time()
        self.packets_per_sec = 0.0

        # Features State
        self.current_page = 0
        self.sort_idx = 0
        self.validation_queue = queue.Queue()
        self.is_validating = False
        self.current_validation_target = None
        self.feedback_message = ""
        self.feedback_timer = 0

        # Worker Thread
        self.running = True
        self.validation_thread = threading.Thread(target=self._validation_worker, daemon=True)
        self.validation_thread.start()

        self.cleanup_thread = threading.Thread(target=self._memory_cleanup_worker, daemon=True)
        self.cleanup_thread.start()

    def set_feedback(self, msg: str, duration: int = 3):
        self.feedback_message = msg
        self.feedback_timer = time.time() + duration

    def on_device_seen(self, device_data: Dict[str, Any]):
        with self.lock:
            mac = device_data["mac"]
            self.packet_count += 1

            # Packets per sec
            now = time.time()
            if now - self.last_packet_time >= 1.0:
                self.packets_per_sec = self.packet_count / (now - self.last_packet_time)
                self.packet_count = 0
                self.last_packet_time = now

            if mac not in self.devices:
                self.devices[mac] = device_data
                self.devices[mac]["history_rssi"] = []
                self.devices[mac]["first_seen"] = datetime.now().strftime("%H:%M:%S")

            # Update existing
            self.devices[mac]["ssid"] = device_data["ssid"]
            self.devices[mac]["rssi"] = device_data["rssi"]
            if device_data["rssi"] is not None:
                self.devices[mac]["history_rssi"].append(device_data["rssi"])
                # Keep last 10
                self.devices[mac]["history_rssi"] = self.devices[mac]["history_rssi"][-10:]

            self.devices[mac]["last_seen"] = datetime.now().strftime("%H:%M:%S")

            if device_data["is_anomaly"]:
                self.devices[mac]["is_anomaly"] = True
                self.devices[mac]["device_class"] = device_data["device_class"]
            if device_data["is_hidden"]:
                self.devices[mac]["is_hidden"] = True
            if device_data["is_rapid"]:
                self.devices[mac]["is_rapid"] = True

    def _validation_worker(self):
        while self.running:
            try:
                target_mac, target_ssid = self.validation_queue.get(timeout=1.0)
                self.is_validating = True
                self.current_validation_target = target_mac

                try:
                    self.validator.start_audit_beacon(self.active_interface, target_ssid)
                    time.sleep(5)
                except Exception:
                    pass # Silently fail in worker, but log in real implementation
                finally:
                    self.validator.stop_audit()
                    self.is_validating = False
                    self.current_validation_target = None

                self.validation_queue.task_done()
            except queue.Empty:
                continue


    def _memory_cleanup_worker(self):
        while self.running:
            time.sleep(30)
            now = datetime.now()
            cutoff = now - timedelta(minutes=5)
            with self.lock:
                stale_macs = []
                for mac, data in self.devices.items():
                    try:
                        last_seen_dt = datetime.strptime(data["last_seen"], "%H:%M:%S").replace(year=now.year, month=now.month, day=now.day)
                        if last_seen_dt < cutoff:
                            stale_macs.append(mac)
                    except Exception:
                        pass
                for mac in stale_macs:
                    del self.devices[mac]

    def handle_keypress(self, key: str):
        if self.current_view == "PROMPT":
            if key == '\n' or key == '\r':
                self._execute_prompt()
            elif key == '\x7f' or key == '\b': # Backspace
                self.prompt_buffer = self.prompt_buffer[:-1]
            elif key == 'q' and not self.prompt_buffer:
                self.current_view = "MAIN"
            elif key.isdigit() or (self.prompt_mode == "SEARCH" and key.isprintable()):
                 self.prompt_buffer += key
            return

        if key == 'q':
            self.running = False
            return
        elif key == 'h':
            self.current_view = "MAIN" if self.current_view == "HELP" else "HELP"
        elif key == 's':
            self.sort_idx = (self.sort_idx + 1) % len(self.SORT_MODES)
        elif key == 'n':
            with self.lock:
                max_pages = max(0, (len(self.devices) - 1) // self.PAGE_SIZE)
            if self.current_page < max_pages:
                self.current_page += 1
        elif key == 'p':
            if self.current_page > 0:
                self.current_page -= 1
        elif key == 'a':
            with self.lock:
                new_state = not self.auditor.active_mode
                self.auditor.toggle_active_mode(new_state)
                state_str = "ACTIVE" if new_state else "PASSIVE"
                self.set_feedback(f"Switched mode to {state_str}")
        elif key == 'e':
            try:
                export_path = self.logger.export_geojson()
                self.set_feedback(f"Exported GEOJSON to: {export_path}")
            except Exception as e:
                 self.set_feedback(f"Export Failed: {e}", duration=5)
        elif key == 'f':
            self.current_view = "PROMPT"
            self.prompt_mode = "SEARCH"
            self.prompt_buffer = ""
        elif key == 'c':
            self.search_query = ""
            self.set_feedback("Cleared search filter.")
        elif key == 'v':
            self.current_view = "PROMPT"
            self.prompt_mode = "VALIDATE"
            self.prompt_buffer = ""
        elif key == 'd':
            if self.current_view == "DETAIL":
                self.current_view = "MAIN"
            else:
                self.current_view = "PROMPT"
                self.prompt_mode = "DETAIL"
                self.prompt_buffer = ""

    def _execute_prompt(self):
        if not self.prompt_buffer:
            self.current_view = "MAIN"
            return

        if self.prompt_mode == "SEARCH":
            self.search_query = self.prompt_buffer.lower()
            self.current_view = "MAIN"
            self.set_feedback(f"Filtering by: {self.search_query}")
            return

        try:
            idx = int(self.prompt_buffer)
            with self.lock:
                sorted_devs = self._get_sorted_devices()
                if 0 <= idx < len(sorted_devs):
                    mac = sorted_devs[idx]["mac"]
                    ssid = sorted_devs[idx]["ssid"]

                    if self.prompt_mode == "VALIDATE":
                        self.validation_queue.put((mac, ssid))
                        self.set_feedback(f"Queued validation for {mac}")
                        self.current_view = "MAIN"
                    elif self.prompt_mode == "DETAIL":
                        self.selected_detail_mac = mac
                        self.current_view = "DETAIL"
                else:
                    self.set_feedback("Invalid index.", duration=2)
                    self.current_view = "MAIN"
        except ValueError:
            self.set_feedback("Invalid input.", duration=2)
            self.current_view = "MAIN"

    def _get_sorted_devices(self) -> List[Dict]:
        devs = list(self.devices.values())
        if self.search_query:
            q = self.search_query
            devs = [d for d in devs if q in d['mac'].lower() or q in d['ssid'].lower() or q in d.get('device_class', '').lower()]

        mode = self.SORT_MODES[self.sort_idx]
        if mode == "rssi":
            # Sort missing rssi to bottom
            devs.sort(key=lambda x: x["rssi"] if x["rssi"] is not None else -999, reverse=True)
        elif mode == "anomaly":
            devs.sort(key=lambda x: (not x.get("is_anomaly", False), x["rssi"] if x["rssi"] is not None else -999), reverse=False)
        elif mode == "class":
            devs.sort(key=lambda x: (x.get("device_class", ""), x["rssi"] if x["rssi"] is not None else -999), reverse=False)
        return devs

    def generate_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="stats", size=3),
            Layout(name="main")
        )

        # Header & Feedback
        header_text = "Aether Auditor - Tactical Dashboard"
        if self.is_validating:
            header_text = f"ACTIVE VALIDATION IN PROGRESS: {self.current_validation_target} | QTY IN QUEUE: {self.validation_queue.qsize()}"
            style = "bold black on yellow"
        elif time.time() < self.feedback_timer:
            header_text = self.feedback_message
            style = "bold white on green"
        else:
            style = "bold white on blue"

        layout["header"].update(Panel(Text(header_text, justify="center", style=style)))

        with self.lock:
            # Stats Panel
            elapsed = int(time.time() - self.start_time)
            mins, secs = divmod(elapsed, 60)
            hrs, mins = divmod(mins, 60)
            time_str = f"{hrs:02d}:{mins:02d}:{secs:02d}"

            total_devs = len(self.devices)
            total_anoms = sum(1 for d in self.devices.values() if d.get("is_anomaly"))
            mode_str = "ACTIVE" if self.auditor.active_mode else "PASSIVE"

            stats_text = f"Total Devices: {total_devs} | Anomalies: [bold red]{total_anoms}[/bold red] | "
            stats_text += f"Elapsed: {time_str} | Pkts/sec: {self.packets_per_sec:.1f} | Mode: [bold cyan]{mode_str}[/bold cyan]"
            layout["stats"].update(Panel(stats_text, title="Telemetry"))

            if self.current_view == "HELP":
                layout["main"].update(self._generate_help_panel())
            elif self.current_view == "DETAIL" and self.selected_detail_mac in self.devices:
                layout["main"].update(self._generate_detail_panel(self.devices[self.selected_detail_mac]))
            else:
                layout["main"].split_row(
                    Layout(name="grid", ratio=2),
                    Layout(name="anomalies", ratio=1)
                )
                grid_panel, anom_panel = self._generate_grid_panels()
                layout["grid"].update(grid_panel)
                layout["anomalies"].update(anom_panel)

        return layout

    def _generate_help_panel(self) -> Panel:
        t = Table(show_header=False, expand=True)
        t.add_column("Key", style="bold yellow")
        t.add_column("Action")
        t.add_row("h", "Toggle this Help Menu")
        t.add_row("s", "Cycle Sort Mode (RSSI -> Anomaly -> Class)")
        t.add_row("n / p", "Next / Previous Page")
        t.add_row("v", "Queue Validation Beacon (Prompt for ID)")
        t.add_row("d", "View Device Details (Prompt for ID)")
        t.add_row("f", "Filter/Search Map (Prompt)")
        t.add_row("c", "Clear Filter")
        t.add_row("a", "Toggle Active/Passive Probe Mode")
        t.add_row("e", "Export database to GeoJSON map")
        t.add_row("q", "Quit Aether Auditor")
        return Panel(t, title="Interactive Commands")

    def _generate_detail_panel(self, data: dict) -> Panel:
        t = Table(expand=True)
        t.add_column("Property", style="cyan")
        t.add_column("Value")
        t.add_row("MAC Address", data["mac"])

        ssid_style = "italic" if data.get("is_hidden") else ""
        t.add_row("SSID", f"[{ssid_style}]{data['ssid']}[/]")

        t.add_row("RSSI", f"{data['rssi']} dBm" if data["rssi"] is not None else "N/A")
        t.add_row("First Seen", data.get("first_seen", ""))
        t.add_row("Last Seen", data.get("last_seen", ""))

        anom_style = "bold red" if data.get("is_anomaly") else "green"
        t.add_row("Status", f"[{anom_style}]{'ANOMALY' if data.get('is_anomaly') else 'BENIGN'}[/]")
        t.add_row("Device Class", data.get("device_class", "Unknown"))

        hist = data.get("history_rssi", [])
        hist_str = " -> ".join([str(x) for x in hist])
        t.add_row("RSSI History", hist_str)

        return Panel(t, title=f"Device Details - {data['mac']} (Press 'd' or 'h' to close)")

    def _generate_grid_panels(self) -> tuple[Panel, Panel]:
        # Grid Table
        grid_table = Table(expand=True)
        grid_table.add_column("ID", style="dim", justify="right")
        grid_table.add_column("MAC / BSSID", style="cyan")
        grid_table.add_column("SSID", style="magenta")
        grid_table.add_column("RSSI", justify="right")
        grid_table.add_column("Vendor / Class", style="blue")

        # Anomalies Table
        anom_table = Table(expand=True)
        anom_table.add_column("Priority Anomalies", style="bold red")
        anom_table.add_column("RSSI", justify="right")

        sorted_devs = self._get_sorted_devices()

        # Add Anomalies
        for d in sorted_devs:
            if d.get("is_anomaly"):
                anom_table.add_row(
                    f"{d['mac']}\n{d.get('device_class', '')}",
                    str(d["rssi"]) if d["rssi"] is not None else "N/A"
                )

        # Pagination logic
        max_pages = max(0, (len(sorted_devs) - 1) // self.PAGE_SIZE)
        if self.current_page > max_pages:
            self.current_page = max_pages

        start_idx = self.current_page * self.PAGE_SIZE
        end_idx = start_idx + self.PAGE_SIZE
        page_devs = sorted_devs[start_idx:end_idx]

        for idx, data in enumerate(page_devs):
            real_idx = start_idx + idx

            # Row styling
            style = ""
            if data.get("is_anomaly"):
                style = "bold red"
            elif data.get("is_rapid"):
                style = "blink red"

            ssid_style = "italic" if data.get("is_hidden") else ""

            grid_table.add_row(
                str(real_idx),
                Text(data["mac"], style=style),
                Text(data["ssid"], style=ssid_style),
                Text(str(data["rssi"]) if data["rssi"] is not None else "N/A", style=style),
                Text(data.get("device_class", "Unknown"), style=style)
            )

        grid_title = f"Live Map (Page {self.current_page + 1}/{max_pages + 1}) | Sort: {self.SORT_MODES[self.sort_idx]}"
        if self.search_query:
            grid_title += f" | Filter: '{self.search_query}'"
        if self.current_view == "PROMPT":
            if self.prompt_mode == "SEARCH":
                action = "Search"
            else:
                action = "Validate" if self.prompt_mode == "VALIDATE" else "View Details"
            grid_title = f"[{action}] Enter: {self.prompt_buffer}_"


        return Panel(grid_table, title=grid_title), Panel(anom_table, title="Detected Signatures")


    def select_interface(self):
        console.clear()
        console.print("[bold cyan]Aether Auditor Initialization[/bold cyan]")
        console.print("Available Interfaces:")
        interfaces = get_if_list()
        for idx, iface in enumerate(interfaces):
            console.print(f"  [{idx}] {iface}")

        selection = Prompt.ask("Select interface to use for auditing", default="0")
        try:
            self.active_interface = interfaces[int(selection)]
        except (ValueError, IndexError):
            console.print("[bold red]Invalid selection. Defaulting to first interface.[/bold red]")
            self.active_interface = interfaces[0] if interfaces else "wlan0"

    def run(self):
        self.select_interface()

        try:
            self.auditor.start_audit(self.active_interface)
        except Exception as e:
            console.print(f"[bold red]Failed to start auditor: {e}[/bold red]")
            sys.exit(1)

        listener = KeyboardListener(self.handle_keypress)
        listener.start()

        console.clear()

        try:
            with Live(self.generate_layout(), refresh_per_second=10, screen=True) as live:
                while self.running:
                    live.update(self.generate_layout())
                    time.sleep(0.1)
        finally:
            self.running = False
            listener.stop()
            self.auditor.stop_audit()
            console.print("\n[bold red]Aether Auditor shut down safely.[/bold red]")


def main():
    try:
        config = get_config()
        logger = AuditLogger()
        ui = DashboardUI(config, logger)
        ui.run()
    except Exception as e:
        console.print(f"[bold red]Critical Error: {e}[/bold red]")
        sys.exit(1)

if __name__ == "__main__":
    main()
