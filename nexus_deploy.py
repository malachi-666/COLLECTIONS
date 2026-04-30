# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "rich",
# ]
# ///

import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.theme import Theme

# Cyberpunk UI Theme
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "danger": "bold red",
    "success": "bold green",
})
console = Console(theme=custom_theme)

DB_PATH = Path("audit_log.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS audit_log
                 (timestamp TEXT, action TEXT, status TEXT)''')
    conn.commit()
    conn.close()

def log_action(action: str, status: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO audit_log VALUES (?, ?, ?)",
              (datetime.now().isoformat(), action, status))
    conn.commit()
    conn.close()

def run_cmd(cmd: list[str], check=True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(cmd, check=check, capture_output=True, text=True)
        return result
    except subprocess.CalledProcessError as e:
        console.print(f"[danger]Command failed:[/danger] {' '.join(cmd)}")
        console.print(f"[danger]Error:[/danger] {e.stderr}")
        log_action(f"CMD: {' '.join(cmd)}", "FAILED")
        raise

def check_os():
    console.print(Panel("[info]Initializing OS Check...[/info]", title="SYSTEM", border_style="cyan"))
    with open("/etc/os-release") as f:
        os_info = f.read().lower()
        if "arch" in os_info:
            console.print("[success]Arch Linux detected. Proceeding.[/success]")
            return "arch"
        elif "debian" in os_info or "ubuntu" in os_info:
            console.print("[info]Debian/Ubuntu/WSL2 detected. Proceeding with caution.[/info]")
            return "debian"
        else:
            console.print("[warning]Unknown OS detected. Mileage may vary.[/warning]")
            return "unknown"

def check_stow():
    try:
        run_cmd(["stow", "--version"])
        console.print("[success]GNU Stow is installed.[/success]")
    except FileNotFoundError:
        console.print("[danger]GNU Stow is not installed. Please install it first (e.g., pacman -S stow).[/danger]")
        sys.exit(1)

def deploy_dotfiles(force: bool):
    dotfiles_dir = Path("dotfiles")
    if not dotfiles_dir.exists():
        console.print("[danger]dotfiles directory not found.[/danger]")
        return

    packages = [d.name for d in dotfiles_dir.iterdir() if d.is_dir()]

    if not packages:
        console.print("[warning]No packages found in dotfiles directory.[/warning]")
        return

    console.print(f"[info]Found packages to stow:[/info] {', '.join(packages)}")

    for package in packages:
        action_msg = f"Stow package: {package}"
        if not force:
            if not Confirm.ask(f"Deploy configuration for [cyan]{package}[/cyan]?", default=False):
                console.print(f"[warning]Skipping {package}[/warning]")
                log_action(action_msg, "SKIPPED")
                continue

        console.print(f"[info]Deploying {package}...[/info]")
        try:
            # -t ~ targets the home directory, -R restows
            # We run from the directory containing the 'dotfiles' dir
            run_cmd(["stow", "-t", str(Path.home()), "-d", str(dotfiles_dir), "-R", package])
            console.print(f"[success]Successfully deployed {package}[/success]")
            log_action(action_msg, "SUCCESS")
        except subprocess.CalledProcessError:
            console.print(f"[danger]Failed to deploy {package}[/danger]")
            log_action(action_msg, "FAILED")

def main():
    parser = argparse.ArgumentParser(description="NEXUS OSINT Dotfile Deployment")
    parser.add_argument("-y", "--force", action="store_true", help="Bypass manual confirmation gates")
    args = parser.parse_args()

    console.print(Panel(r"""
[bold cyan]
 _   _ _______   ___    _  _____
| \ | |  ___\ \ / / |  | |/  ___|
|  \| | |__  \ V /| |  | |\ `--.
| . ` |  __| /   \| |/\| | `--. \
| |\  | |___/ /^\ \  /\  //\__/ /
\_| \_/\____\/   \/\/  \/ \____/

DEPLOYMENT SCRIPT v1.0[/bold cyan]
""", title="NEXUS CORE", border_style="cyan"))

    init_db()
    log_action("NEXUS DEPLOYMENT STARTED", "INFO")

    check_os()
    check_stow()

    deploy_dotfiles(args.force)

    log_action("NEXUS DEPLOYMENT COMPLETED", "INFO")
    console.print(Panel("[success]Deployment Complete.[/success]", border_style="green"))

if __name__ == "__main__":
    main()
