import sys
from rich.console import Console
from rich.table import Table

from src.config import get_config, AppConfig

def display_signatures(config: AppConfig, console: Console):
    table = Table(title="Hardware Signatures Loaded")

    table.add_column("Device Class", style="cyan")
    table.add_column("MAC OUI", style="magenta")
    table.add_column("Signal Threshold (dBm)", justify="right", style="green")
    table.add_column("Expected SSID", style="yellow")

    for sig in config.signatures:
        table.add_row(
            sig.device_class,
            sig.mac_oui,
            str(sig.signal_threshold_dbm),
            sig.expected_ssid if sig.expected_ssid else "N/A"
        )

    console.print(table)


def main():
    console = Console()
    try:
        config = get_config()
        console.print("[bold green]Aether Auditor initialized successfully.[/bold green]")
        display_signatures(config, console)
    except Exception as e:
        console.print(f"[bold red]Error initializing configuration: {e}[/bold red]")
        sys.exit(1)

if __name__ == "__main__":
    main()
