# Aether Auditor

A high-fidelity environmental RF scanning and tactical structural transparency tool. Designed for legacy IoT environments, rogue AP detection, and unadvertised infrastructure backdoor discovery.

## Architecture Highlights
1. **Transparency Auditor (`src/core/scanner.py`)**: A multi-threaded daemon leveraging `scapy` to silently ingest 802.11 management frames. Decodes RSSI, hashes OUI logic, and flags rapid beacon emissions or hidden SSIDs.
2. **Tactical Hybridity**: Switch seamlessly from Passive Stealth mapping to Active Probe emissions.
3. **Audit Logger (`src/utils/logger.py`)**: SQLite-backed logging module tracking structural discoveries. Handles dynamic MAC anonymization (SHA256) and autonomous retention sweeping.
4. **Interactive Dashboard (`src/ui/dashboard.py`)**: A purely non-blocking tactical CLI interface engineered with `rich`. It features pagination, validation queuing, and dynamic search/filtering.
5. **Geo-Intelligence Mapping (`src/ui/map_view.py`)**: A standalone CustomTkinter map translating the structural SQLite logs to a live GeoJSON radius overlay utilizing pseudo-coordinate derivations.

## Setup Requirements

Aether Auditor relies on standard Linux utilities and a monitor-mode capable wireless card.

```bash
# Ubuntu / Kali Linux Dependencies
sudo apt update
sudo apt install -y aircrack-ng hostapd iproute2

# Python Environment Setup via `uv`
uv pip install -e .
```

## Quick Start

### 1. Configure Hardware Signatures
Ensure `config/signatures.yaml` contains your high-priority hardware targets.
```yaml
signatures:
  - device_class: "Legacy_Surveillance_Node"
    mac_oui: "00:1A:2B"
    expected_ssid: "FAILOVER_NET_1"
    signal_threshold_dbm: -80
```

### 2. Launch the Auditor CLI
Initialize your wireless adapter into monitor mode (e.g., `airmon-ng start wlan0`), then run:
```bash
aether-audit
```

**CLI Keyboard Controls:**
- `h`: Open Interactive Help Menu
- `s`: Cycle Sort Modes (RSSI -> Anomaly -> Class)
- `n` / `p`: Paginate live environment map
- `v`: Enter Validation Mode (prompts for device ID to trigger active beacon)
- `d`: Open detailed Device Analytics panel
- `f`: Filter/Search live map (prompts for query)
- `c`: Clear Search Filter
- `a`: Toggle Active Probe Mode (Warning: Breaks Stealth operation)
- `e`: Export SQLite Database to GeoJSON map file.
- `q`: Graceful Shutdown

### 3. Launch Geo-Mapping Dashboard
Once you have collected data, visualize it structurally:
```bash
aether-map
```
