import json
import threading
from datetime import datetime, timezone
from pathlib import Path

# Ensure absolute paths based on project root.
# src/utils/logger.py -> src/utils/ -> src/ -> project_root -> logs
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

class AuditLogger:
    def __init__(self, log_filename: str = "audit.log", max_bytes: int = 10 * 1024 * 1024, backup_count: int = 5):
        self.max_bytes = max_bytes
        self.backup_count = backup_count

        self.log_dir = LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.log_file = self.log_dir / log_filename
        self.lock = threading.Lock()

    def _rotate(self):
        if not self.log_file.exists():
            return

        if self.log_file.stat().st_size >= self.max_bytes:
            # Rotate backwards from backup_count - 1 down to 1
            for i in range(self.backup_count - 1, 0, -1):
                source = self.log_dir / f"{self.log_file.name}.{i}"
                target = self.log_dir / f"{self.log_file.name}.{i + 1}"
                if source.exists():
                    if target.exists():
                        target.unlink()
                    source.rename(target)

            # Move the current log to .1
            target = self.log_dir / f"{self.log_file.name}.1"
            if target.exists():
                target.unlink()
            self.log_file.rename(target)

    def log_event(self, data: dict):
        """
        Logs a JSONL event. Injects ISO 8601 timestamp with microsecond precision.
        """
        # Get timestamp with microsecond precision
        timestamp = datetime.now(timezone.utc).isoformat()

        # Prepare payload
        payload = {"timestamp": timestamp}
        payload.update(data)

        try:
            jsonl_str = json.dumps(payload) + "\n"
        except (TypeError, ValueError) as e:
            # Fallback if data is not JSON serializable
            jsonl_str = json.dumps({"timestamp": timestamp, "error": f"Failed to serialize payload: {e}"}) + "\n"

        with self.lock:
            self._rotate()
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(jsonl_str)
