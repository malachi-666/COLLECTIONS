import sys
import time
import asyncio
import threading
from typing import Dict, Any

from rich.console import Console
from rich.layout import Layout
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.prompt import Prompt
from rich.text import Text

from scapy.interfaces import get_if_list

from src.config import get_config, AppConfig
from src.utils.logger import AuditLogger
from src.core.scanner import TransparencyAuditor
from src.core.validator import SignalValidator

console = Console()

class DashboardUI:
    def __init__(self, config: AppConfig, logger: AuditLogger):
        self.config = config
        self.logger = logger
        self.devices: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()

        self.auditor = TransparencyAuditor(self.config, self.logger, on_device_seen=self.on_device_seen)
        self.validator = SignalValidator()
        self.active_interface = None

        # State for UI rendering
        self.is_validating = False
        self.validating_target = None

    def on_device_seen(self, device_data: Dict[str, Any]):
        with self.lock:
            mac = device_data["mac"]
            if mac not in self.devices:
                self.devices[mac] = device_data
            else:
                # Update existing
                self.devices[mac]["ssid"] = device_data["ssid"]
                self.devices[mac]["rssi"] = device_data["rssi"]
                # Upgrade anomaly status if previously missed
                if device_data["is_anomaly"]:
                    self.devices[mac]["is_anomaly"] = True
                    self.devices[mac]["device_class"] = device_data["device_class"]

    def _get_proximity_text(self, rssi: int) -> Text:
        if rssi is None:
            return Text("Unknown", style="dim")
        if rssi >= -60:
            return Text("Immediate", style="bold red")
        elif rssi >= -80:
            return Text("Nearby", style="bold yellow")
        else:
            return Text("Distant", style="bold green")

    def generate_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main")
        )
        layout["main"].split_row(
            Layout(name="grid", ratio=2),
            Layout(name="anomalies", ratio=1)
        )

        # Header
        header_text = Text("Aether Auditor - Live Structural RF Map", style="bold white on blue", justify="center")
        if self.is_validating:
             header_text = Text(f"ACTIVE VALIDATION: {self.validating_target}", style="bold black on yellow", justify="center")
        layout["header"].update(Panel(header_text))

        with self.lock:
            # Grid Table
            grid_table = Table(expand=True)
            grid_table.add_column("MAC / BSSID", style="cyan")
            grid_table.add_column("SSID", style="magenta")
            grid_table.add_column("Proximity", justify="center")
            grid_table.add_column("Vendor / Class", style="blue")

            # Anomalies Table
            anom_table = Table(expand=True)
            anom_table.add_column("Priority Anomalies", style="bold red")
            anom_table.add_column("RSSI", justify="right")

            for mac, data in self.devices.items():
                prox_text = self._get_proximity_text(data["rssi"])
                dev_class = data["device_class"]

                # Add to main grid
                grid_table.add_row(
                    mac,
                    data["ssid"],
                    prox_text,
                    dev_class
                )

                # Add to anomalies
                if data["is_anomaly"]:
                    anom_table.add_row(
                        f"{mac}\n{dev_class}",
                        str(data["rssi"]) if data["rssi"] is not None else "N/A"
                    )

        border_style = "yellow" if self.is_validating else "blue"
        layout["grid"].update(Panel(grid_table, title="Environmental Map", border_style=border_style))
        layout["anomalies"].update(Panel(anom_table, title="Detected Signatures", border_style="red" if not self.is_validating else "yellow"))

        return layout

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

    def run_validation_sequence(self):
        """Interactive sequence to trigger validation"""
        with self.lock:
            anomalies = {mac: data for mac, data in self.devices.items() if data["is_anomaly"]}

        if not anomalies:
            console.print("\n[yellow]No anomalies detected yet to validate.[/yellow]")
            time.sleep(2)
            return

        console.print("\n[bold red]Select Anomaly to Validate:[/bold red]")
        mac_list = list(anomalies.keys())
        for idx, mac in enumerate(mac_list):
            data = anomalies[mac]
            console.print(f"  [{idx}] {mac} - {data['device_class']} (SSID: {data['ssid']})")

        selection = Prompt.ask("Enter index to validate (or 'q' to cancel)")
        if selection.lower() == 'q':
            return

        try:
            target_mac = mac_list[int(selection)]
            target_data = anomalies[target_mac]
        except (ValueError, IndexError):
            console.print("[red]Invalid selection.[/red]")
            time.sleep(1)
            return

        self.is_validating = True
        self.validating_target = f"{target_data['device_class']} ({target_mac})"

        # Start Validator
        try:
            console.print(f"[bold yellow]Initiating validation beacon for {target_data['ssid']}...[/bold yellow]")
            self.validator.start_audit_beacon(self.active_interface, target_data['ssid'])
            time.sleep(5) # Simulate active validation time
        except Exception as e:
            console.print(f"[bold red]Validation Error: {e}[/bold red]")
            time.sleep(2)
        finally:
            self.validator.stop_audit()
            self.is_validating = False

    async def _async_run(self):
        self.select_interface()

        try:
            self.auditor.start_audit(self.active_interface)
        except Exception as e:
            console.print(f"[bold red]Failed to start auditor: {e}[/bold red]")
            sys.exit(1)

        console.clear()

        # We use a Live block. Since prompt breaks live block rendering,
        # we will run it in a loop catching inputs asynchronously, but for a rich TUI,
        # catching keyboard input elegantly across OSes without curses is tricky.
        # We'll use a polling thread or try-except on KeyboardInterrupt.

        with Live(self.generate_layout(), refresh_per_second=4, screen=True) as live:
            try:
                while True:
                    live.update(self.generate_layout())
                    await asyncio.sleep(0.25)
            except KeyboardInterrupt:
                # Catch interrupt to pause live layout and show interactive prompt
                pass

        # Outside live block, prompt
        while True:
            console.clear()
            console.print(self.generate_layout())
            console.print("\n[bold cyan]Auditor Paused.[/bold cyan]")
            console.print("Commands: [bold yellow]v[/bold yellow] = Validate Anomaly, [bold yellow]r[/bold yellow] = Resume live map, [bold yellow]q[/bold yellow] = Quit")

            cmd = Prompt.ask("Enter command", choices=["v", "r", "q"], default="r")

            if cmd == 'v':
                self.run_validation_sequence()
            elif cmd == 'r':
                # Resume live loop
                try:
                    with Live(self.generate_layout(), refresh_per_second=4, screen=True) as live:
                        while True:
                            live.update(self.generate_layout())
                            await asyncio.sleep(0.25)
                except KeyboardInterrupt:
                    continue
            elif cmd == 'q':
                break

        self.auditor.stop_audit()

    def run(self):
        try:
            asyncio.run(self._async_run())
        except KeyboardInterrupt:
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
