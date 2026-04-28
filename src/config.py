import dataclasses
import yaml
from pathlib import Path
from typing import Optional, List

# Ensure absolute paths based on project root.
# src/config.py -> src/ -> project_root -> config
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
SIGNATURES_FILE = CONFIG_DIR / "signatures.yaml"

@dataclasses.dataclass
class HardwareSignature:
    device_class: str
    mac_oui: str
    signal_threshold_dbm: int
    expected_ssid: Optional[str] = None

@dataclasses.dataclass
class AppConfig:
    signatures: List[HardwareSignature]

def load_signatures(filepath: Path = SIGNATURES_FILE) -> List[HardwareSignature]:
    if not filepath.exists():
        raise FileNotFoundError(f"Signatures file not found at: {filepath}")

    with open(filepath, 'r') as f:
        data = yaml.safe_load(f)

    signatures = []
    for sig_data in data.get('signatures', []):
        signatures.append(HardwareSignature(**sig_data))

    return signatures

def get_config() -> AppConfig:
    return AppConfig(signatures=load_signatures())
