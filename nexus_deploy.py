# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "questionary",
#     "rich",
#     "psutil",
# ]
# ///
import os
import sys
import shutil
import subprocess
import time
import tarfile
import stat
from datetime import datetime
from pathlib import Path

# Try to import dependencies
try:
    import questionary
    import psutil
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.prompt import Prompt
    from rich.table import Table
    from rich.progress import track
    from rich.layout import Layout
except ImportError:
    print("Dependencies not met. Please run this script using uv:")
    print("uv run nexus_deploy.py")
    sys.exit(1)

console = Console()

# ==============================================================================
# CONFIGURATION AND METADATA
# ==============================================================================

VAULT_DIR = Path.home() / ".nexus_vault"
STOW_TARGET = Path.home()
NEXUS_PRESETS_DIR = Path.home() / ".nexus_presets"
DOTFILES_DIR = Path.home() / ".nexus_dotfiles"
CONFIG_DIR = Path.home() / ".config"

# The 6 high-tier GitHub dotfile repositories
PROFILES = {
    "The Minimalist": {
        "repo": "https://github.com/mathiasbynens/dotfiles.git",
        "pros": ["Extremely lightweight", "Sensible macOS defaults", "Highly vetted"],
        "cons": ["macOS focused", "Requires manual tweaking for Linux"],
        "persona": "Developers who want a clean, fast, and no-nonsense terminal experience."
    },
    "The Pentester": {
        "repo": "https://github.com/g0tmi1k/os-scripts.git",
        "pros": ["Security tools pre-configured", "Aggressive aliases", "Hardened"],
        "cons": ["Overwhelming for daily driving", "Can break standard workflows"],
        "persona": "Offensive security engineers and red teamers."
    },
    "The Wayland Specialist": {
        "repo": "https://github.com/prasanthrangan/hyprdots.git",
        "pros": ["Stunning UI/UX", "Hyprland optimized", "Comprehensive Wayland tools"],
        "cons": ["High resource usage", "Complex installation dependencies"],
        "persona": "Linux ricing enthusiasts who want a flawless Wayland experience."
    },
    "The Emacs Orchestrator": {
        "repo": "https://github.com/purcell/emacs.d.git",
        "pros": ["Battle-tested Emacs config", "Insane productivity potential", "Lisp-heavy"],
        "cons": ["Steep learning curve", "Vim users will suffer"],
        "persona": "Keyboard-driven power users who live entirely inside Emacs."
    },
    "The Performance Junkie": {
        "repo": "https://github.com/LukeSmithxyz/voidrice.git",
        "pros": ["Ultra-minimal", "Suckless tools focused", "Zero bloat"],
        "cons": ["Very opinionated", "Requires compiling from source"],
        "persona": "Minimalists who count milliseconds and despise modern bloatware."
    },
    "The Aesthetic Hacker": {
        "repo": "https://github.com/rxyhn/dotfiles.git",
        "pros": ["Beautiful Catppuccin themes", "Consistent UI across apps", "Modern tooling"],
        "cons": ["Form over function sometimes", "Requires specific terminal emulators"],
        "persona": "Developers who need their setup to look as good as their code."
    }
}

# The Global Standard Presets
PRESET_FILES = {
    "zsh": {
        "path": ".zshrc",
        "content": """# Nexus Sanitized ZSH Config
export ZSH=$HOME/.oh-my-zsh
ZSH_THEME="robbyrussell"
plugins=(git zsh-autosuggestions zsh-syntax-highlighting)
if [ -f $ZSH/oh-my-zsh.sh ]; then
    source $ZSH/oh-my-zsh.sh
fi

# Aliases
alias ls='ls --color=auto'
alias ll='ls -lah'
alias grep='grep --color=auto'
alias update='sudo apt update && sudo apt upgrade -y'

# Environment
export EDITOR=vim
export VISUAL=vim
"""
    },
    "tmux": {
        "path": ".tmux.conf",
        "content": """# Nexus Productivity Tmux Config
set -g default-terminal "screen-256color"
set -g history-limit 10000

# Remap prefix from 'C-b' to 'C-a'
unbind C-b
set-option -g prefix C-a
bind-key C-a send-prefix

# Split panes using | and -
bind | split-window -h
bind - split-window -v
unbind '"'
unbind %

# Enable mouse mode
set -g mouse on
"""
    },
    "fastfetch": {
        "path": ".config/fastfetch/config.jsonc",
        "content": """{
  "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
  "modules": [
    "title",
    "separator",
    "os",
    "host",
    "kernel",
    "uptime",
    "packages",
    "shell",
    "display",
    "de",
    "wm",
    "terminal",
    "cpu",
    "gpu",
    "memory",
    "disk",
    "battery",
    "poweradapter",
    "locale",
    "break",
    "colors"
  ]
}"""
    },
    "ssh": {
        "path": ".ssh/config",
        "content": """# Nexus Hardened SSH Config
Host *
    # Cryptography Policy
    Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com,aes256-ctr,aes192-ctr,aes128-ctr
    KexAlgorithms curve25519-sha256@libssh.org,diffie-hellman-group-exchange-sha256
    MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com,umac-128-etm@openssh.com

    # Security enhancements
    ServerAliveInterval 300
    ServerAliveCountMax 2
    HashKnownHosts yes
    StrictHostKeyChecking ask
    VerifyHostKeyDNS yes
    ForwardAgent no
    ForwardX11 no
"""
    },
    "starship": {
        "path": ".config/starship.toml",
        "content": """# Nexus Starship Config
format = \"\"\"
[╭─](#9A348E)$os$username$hostname$directory$git_branch$git_status
[╰─](#9A348E)$character
\"\"\"

[os]
disabled = false

[username]
show_always = true
style_user = "bg:#9A348E"
style_root = "bg:#9A348E"
format = '[$user ]($style)'

[directory]
style = "fg:#e3e5e5 bg:#769ff0"
format = "[ $path ]($style)"
truncation_length = 3
truncation_symbol = "…/"

[git_branch]
symbol = ""
style = "bg:#394260"
format = '[[ $symbol $branch ](fg:#769ff0 bg:#394260)]($style)'
"""
    },
    "Local AI": {
        "path": ".config/nexus_ai",
        "content": {
            "vram_alias": "alias vram='nvidia-smi -l 1'",
            "systemd": """[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=root
Group=root
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
""",
            "config.yaml": """# Nexus Local LLM Configuration
network:
  listen: "0.0.0.0"
  port: 11434
models:
  default: "llama3"
  directory: "/var/lib/ollama/models"
ui:
  theme: "cyberpunk"
  access: "internal"
"""
        }
    }
}

# ==============================================================================
# HARDWARE & SYSTEM HEALTH
# ==============================================================================

def detect_hardware():
    has_nvidia = False
    gpu_info = "Not Detected / Unknown"

    try:
        result = subprocess.run(["lspci"], capture_output=True, text=True)
        if "NVIDIA" in result.stdout:
            has_nvidia = True
            for line in result.stdout.split('\n'):
                if "VGA compatible controller" in line and "NVIDIA" in line:
                    gpu_info = line.split(":")[-1].strip()
                    break
            if gpu_info == "Not Detected / Unknown":
                gpu_info = "NVIDIA GPU Detected"
    except FileNotFoundError:
        pass # lspci not found

    return has_nvidia, gpu_info

def get_system_health():
    cpu_percent = psutil.cpu_percent(interval=0.1)

    mem = psutil.virtual_memory()
    mem_total_gb = mem.total / (1024**3)
    mem_used_gb = mem.used / (1024**3)
    mem_percent = mem.percent

    _, gpu_info = detect_hardware()

    return {
        "cpu": cpu_percent,
        "mem_total": f"{mem_total_gb:.1f}GB",
        "mem_used": f"{mem_used_gb:.1f}GB",
        "mem_percent": mem_percent,
        "gpu": gpu_info
    }

# ==============================================================================
# UI & DASHBOARD
# ==============================================================================

def display_dashboard():
    console.clear()
    title = Text("N E X U S   D E P L O Y", style="bold cyan on black", justify="center")
    subtitle = Text("Automated Dotfile Architecture & Symlink Provisioning [PHASE 2]", style="magenta", justify="center")

    health = get_system_health()

    health_text = f"[bold white]CPU Usage:[/bold white] {health['cpu']}%  |  "

    mem_color = "green"
    if health['mem_percent'] > 80: mem_color = "red"
    elif health['mem_percent'] > 60: mem_color = "yellow"

    health_text += f"[bold white]RAM:[/bold white] [{mem_color}]{health['mem_used']}/{health['mem_total']} ({health['mem_percent']}%)[/{mem_color}]  |  "
    health_text += f"[bold white]GPU:[/bold white] {health['gpu']}"

    panel = Panel(
        Text.assemble(title, "\n", subtitle, "\n\n", Text.from_markup(health_text, justify="center")),
        border_style="cyan",
        expand=False
    )
    console.print(panel, justify="center")
    console.print()

def check_security():
    if os.geteuid() == 0:
        warning_text = "[bold red]WARNING: ROOT PRIVILEGES DETECTED[/bold red]\n"
        warning_text += "Running third-party configurations as root (especially on Kali Linux) is highly discouraged.\n"
        warning_text += "This can lead to system instability or security vulnerabilities."
        console.print(Panel(warning_text, title="Security Protocol", border_style="red"))

        proceed = questionary.confirm("Do you wish to bypass the security protocol and proceed?", default=False).ask()
        if not proceed:
            console.print("[red]Execution aborted by user.[/red]")
            sys.exit(1)
        console.print("[yellow]Proceeding with elevated privileges...[/yellow]\n")

def check_environment():
    console.print("[cyan]Initiating Environment Audit...[/cyan]")
    deps = ["git", "stow", "uv", "tar"]
    missing = []

    for dep in deps:
        if shutil.which(dep):
            console.print(f"  [green]✓ {dep} found[/green]")
        else:
            console.print(f"  [red]✗ {dep} missing[/red]")
            missing.append(dep)

    if missing:
        console.print(f"\n[red]FATAL: Missing required binaries: {', '.join(missing)}[/red]")
        console.print("Please install them before proceeding.")
        sys.exit(1)
    console.print("[green]Environment Audit Passed.[/green]\n")

def display_profile_analysis(profile_name):
    profile = PROFILES[profile_name]

    pros = "\n".join([f"[green]+[/green] {p}" for p in profile['pros']])
    cons = "\n".join([f"[red]-[/red] {c}" for c in profile['cons']])

    content = f"[bold cyan]Repository:[/bold cyan] {profile['repo']}\n\n"
    content += f"[bold green]Pros:[/bold green]\n{pros}\n\n"
    content += f"[bold red]Cons:[/bold red]\n{cons}\n\n"
    content += f"[bold yellow]Target User Persona:[/bold yellow]\n{profile['persona']}"

    console.print(Panel(content, title=f"Strategic Analysis: {profile_name}", border_style="cyan"))

# ==============================================================================
# VAULT & BACKUP LOGIC (NEXUS VAULT)
# ==============================================================================

def create_vault_archive():
    if not VAULT_DIR.exists():
        VAULT_DIR.mkdir(parents=True, exist_ok=True)

    if not CONFIG_DIR.exists():
        console.print("[yellow]~/.config does not exist. Skipping vault archive.[/yellow]")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"deploy_{timestamp}.tar.gz"
    archive_path = VAULT_DIR / archive_name

    console.print(f"[cyan]Creating Nexus Vault Archive: {archive_name}[/cyan]")
    try:
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(CONFIG_DIR, arcname=CONFIG_DIR.name)
        console.print(f"[green]Successfully archived ~/.config to {archive_path}[/green]")
    except Exception as e:
        console.print(f"[red]Failed to create vault archive: {e}[/red]")
        sys.exit(1)

def get_vault_archives():
    if not VAULT_DIR.exists():
        return []
    archives = list(VAULT_DIR.glob("deploy_*.tar.gz"))
    archives.sort(key=os.path.getmtime, reverse=True)
    return archives[:3]

def rollback():
    archives = get_vault_archives()
    if not archives:
        console.print("[yellow]No vault archives found for rollback.[/yellow]")
        return

    choices = [f"{a.name} (Modified: {datetime.fromtimestamp(a.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')})" for a in archives]
    choices.append("Cancel")

    choice = questionary.select(
        "Select Vault Archive to Restore:",
        choices=choices
    ).ask()

    if choice == "Cancel" or not choice:
        return

    idx = choices.index(choice)
    selected_archive = archives[idx]

    confirm = questionary.confirm(f"WARNING: This will overwrite ~/.config with the contents of {selected_archive.name}. Proceed?").ask()
    if not confirm:
        return

    console.print(f"[cyan]Restoring from {selected_archive.name}...[/cyan]")

    # Backup current state before restoring? Just to be safe, yes.
    create_vault_archive()

    if CONFIG_DIR.exists():
        shutil.rmtree(CONFIG_DIR)

    try:
        with tarfile.open(selected_archive, "r:gz") as tar:
            # We tarred .config directly, so it extracts to .config within current dir.
            # We should extract to home.
            tar.extractall(path=Path.home())
        console.print("[green]Rollback completed successfully.[/green]")
    except Exception as e:
        console.print(f"[red]Rollback failed: {e}[/red]")


def safe_remove_conflicts(source_dir):
    """
    Recursively find conflicts and remove them to allow stow to work properly,
    instead of aggressively removing entire directories like ~/.config.
    """
    for root, dirs, files in os.walk(source_dir):
        # Skip .git
        if '.git' in dirs:
            dirs.remove('.git')

        rel_root = Path(root).relative_to(source_dir)

        for file in files:
            target_path = STOW_TARGET / rel_root / file
            if target_path.exists() or target_path.is_symlink():
                console.print(f"  [yellow]Removing conflicting file/symlink:[/yellow] {target_path}")
                try:
                    if target_path.is_symlink():
                        target_path.unlink()
                    else:
                        target_path.unlink()
                except Exception as e:
                    console.print(f"[red]Error removing {target_path}: {e}[/red]")

# ==============================================================================
# DEPLOYMENT & STOW LOGIC
# ==============================================================================

def stow_directory(source_dir, package_name):
    console.print(f"[cyan]Executing GNU stow for {package_name}...[/cyan]")
    try:
        subprocess.run(
            ["stow", "-v", "-t", str(STOW_TARGET), "-d", str(source_dir), package_name],
            check=True,
            capture_output=True,
            text=True
        )
        console.print(f"[green]Successfully stowed {package_name}[/green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Stow failed: {e.stderr}[/red]")

def clone_and_stow_profile(profile_name):
    profile = PROFILES[profile_name]
    repo_url = profile['repo']

    target_dir = DOTFILES_DIR / profile_name.replace(" ", "_").lower()

    if target_dir.exists():
        console.print(f"[yellow]Directory {target_dir} already exists. Removing...[/yellow]")
        shutil.rmtree(target_dir)

    console.print(f"[cyan]Cloning {repo_url} into {target_dir}...[/cyan]")
    try:
        subprocess.run(["git", "clone", repo_url, str(target_dir)], check=True)
        console.print("[green]Clone successful.[/green]")
    except subprocess.CalledProcessError:
        console.print("[red]Failed to clone repository.[/red]")
        return

    create_vault_archive()
    console.print(f"[yellow]Conflict Protection: Resolving file conflicts...[/yellow]")
    safe_remove_conflicts(target_dir)

    stow_directory(DOTFILES_DIR, target_dir.name)

def inject_preset(preset_key):
    preset = PRESET_FILES[preset_key]

    pkg_dir = NEXUS_PRESETS_DIR / preset_key

    # Handle Local AI preset which has multiple files
    if preset_key == "Local AI":
        base_path = pkg_dir / preset['path']
        base_path.mkdir(parents=True, exist_ok=True)

        with open(base_path / "vram_alias.sh", "w") as f:
            f.write(preset['content']['vram_alias'] + "\n")

        with open(base_path / "ollama.service", "w") as f:
            f.write(preset['content']['systemd'])

        with open(base_path / "config.yaml", "w") as f:
            f.write(preset['content']['config.yaml'])

    else:
        rel_path = preset['path']
        content = preset['content']
        file_path = pkg_dir / rel_path

        has_nvidia, _ = detect_hardware()
        if has_nvidia and preset_key in ["zsh", "tmux"]: # Injecting env vars into shell configs
            console.print("[cyan]NVIDIA GPU Detected: Injecting Wayland Hardware Cursor fixes...[/cyan]")
            env_vars = """
# Nexus Auto-Injected NVIDIA Wayland Fixes
export WLR_NO_HARDWARE_CURSORS=1
export LIBVA_DRIVER_NAME=nvidia
export __GLX_VENDOR_LIBRARY_NAME=nvidia
"""
            content += env_vars

        file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, 'w') as f:
            f.write(content)

    console.print(f"[green]Created preset structure for {preset_key} at {pkg_dir}[/green]")

    create_vault_archive()
    console.print(f"[yellow]Conflict Protection: Resolving file conflicts...[/yellow]")
    safe_remove_conflicts(pkg_dir)

    stow_directory(NEXUS_PRESETS_DIR, preset_key)


# ==============================================================================
# POST-DEPLOYMENT AUDIT & OPSEC
# ==============================================================================

def verify_deployment():
    console.print("\n[cyan]Executing Post-Deployment Audit...[/cyan]")

    # Check for broken symlinks in home dir
    broken_links = []
    for root, dirs, files in os.walk(str(STOW_TARGET)):
        # Don't recurse into hidden dirs too deeply for performance, but check .config
        if '.nexus' in root or '.git' in root:
            continue

        for file in files + dirs:
            p = Path(root) / file
            if p.is_symlink() and not p.exists():
                broken_links.append(p)

    if broken_links:
        console.print("[red]Broken symlinks detected:[/red]")
        for link in broken_links[:10]: # show top 10
            console.print(f"  {link}")
        if len(broken_links) > 10:
            console.print(f"  ... and {len(broken_links) - 10} more.")
    else:
        console.print("[green]✓ No broken symlinks detected.[/green]")

    # Check SSH permissions
    ssh_dir = STOW_TARGET / ".ssh"
    if ssh_dir.exists():
        dir_stat = os.stat(ssh_dir)
        dir_perms = stat.S_IMODE(dir_stat.st_mode)

        if dir_perms != 0o700:
            console.print(f"[yellow]! Correcting {ssh_dir} permissions to 700 (was {oct(dir_perms)})[/yellow]")
            ssh_dir.chmod(0o700)
        else:
            console.print("[green]✓ .ssh/ directory permissions correct (700).[/green]")

        ssh_config = ssh_dir / "config"
        if ssh_config.exists():
            conf_stat = os.stat(ssh_config)
            conf_perms = stat.S_IMODE(conf_stat.st_mode)
            if conf_perms != 0o600:
                console.print(f"[yellow]! Correcting {ssh_config} permissions to 600 (was {oct(conf_perms)})[/yellow]")
                ssh_config.chmod(0o600)
            else:
                console.print("[green]✓ .ssh/config permissions correct (600).[/green]")

    # Ask for OPSEC hardening
    if shutil.which("ufw") or shutil.which("macchanger"):
        console.print("\n[cyan]OPSEC Hardening Module Available[/cyan]")
        harden = questionary.confirm("Trigger OPSEC Hardening routine? (UFW local-only + MAC Spoofing)").ask()
        if harden:
            apply_opsec_hardening()

def apply_opsec_hardening():
    console.print("[cyan]Applying OPSEC Hardening...[/cyan]")

    # UFW Local Only
    if shutil.which("ufw"):
        try:
            subprocess.run(["sudo", "ufw", "reset"], check=True, capture_output=True)
            subprocess.run(["sudo", "ufw", "default", "deny", "incoming"], check=True, capture_output=True)
            subprocess.run(["sudo", "ufw", "default", "deny", "outgoing"], check=True, capture_output=True)
            # Allow local traffic (example typical subnets)
            subprocess.run(["sudo", "ufw", "allow", "out", "to", "192.168.0.0/16"], check=True, capture_output=True)
            subprocess.run(["sudo", "ufw", "allow", "out", "to", "10.0.0.0/8"], check=True, capture_output=True)
            subprocess.run(["sudo", "ufw", "enable"], check=True, capture_output=True)
            console.print("[green]✓ UFW rules applied: Deny all, allow local out.[/green]")
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to configure UFW: {e}[/red]")

    # MAC Spoofing
    if shutil.which("macchanger"):
        interfaces = []
        try:
            ip_link = subprocess.run(["ip", "link"], capture_output=True, text=True, check=True)
            for line in ip_link.stdout.split('\n'):
                if ":" in line and not "lo:" in line:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        iface = parts[1].strip()
                        # simple filter for eth and wlan
                        if iface.startswith("eth") or iface.startswith("wlan") or iface.startswith("en") or iface.startswith("wl"):
                            interfaces.append(iface)
        except Exception:
            pass

        for iface in interfaces:
            console.print(f"[yellow]Spoofing MAC for {iface}...[/yellow]")
            try:
                subprocess.run(["sudo", "ip", "link", "set", "dev", iface, "down"], check=True, capture_output=True)
                subprocess.run(["sudo", "macchanger", "-r", iface], check=True, capture_output=True)
                subprocess.run(["sudo", "ip", "link", "set", "dev", iface, "up"], check=True, capture_output=True)
                console.print(f"[green]✓ MAC Spoofed for {iface}[/green]")
            except subprocess.CalledProcessError as e:
                console.print(f"[red]Failed to spoof MAC for {iface}: {e}[/red]")


# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

def main():
    display_dashboard()
    check_security()
    check_environment()

    action = questionary.select(
        "Select Deployment Mode:",
        choices=["Clone Full Profile", "Inject a Preset", "Rollback (Nexus Vault)", "Exit"]
    ).ask()

    if action == "Clone Full Profile":
        profile_choice = questionary.select(
            "Select Dotfile Architecture:",
            choices=list(PROFILES.keys())
        ).ask()

        display_profile_analysis(profile_choice)

        confirm = questionary.confirm(f"Deploy {profile_choice}?").ask()
        if confirm:
            clone_and_stow_profile(profile_choice)
            verify_deployment()
        else:
            console.print("[yellow]Deployment cancelled.[/yellow]")

    elif action == "Inject a Preset":
        preset_choice = questionary.select(
            "Select Global Standard Preset:",
            choices=list(PRESET_FILES.keys())
        ).ask()

        confirm = questionary.confirm(f"Inject {preset_choice} preset?").ask()
        if confirm:
            inject_preset(preset_choice)
            verify_deployment()
        else:
            console.print("[yellow]Injection cancelled.[/yellow]")

    elif action == "Rollback (Nexus Vault)":
        rollback()

    else:
        console.print("[cyan]Exiting Nexus Deploy.[/cyan]")
        sys.exit(0)

if __name__ == "__main__":
    main()
