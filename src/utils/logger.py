import sqlite3
import threading
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
import geojson
import math

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

class AuditLogger:
    def __init__(self, db_filename: str = "audit_retention.db", retention_days: int = 30):
        self.retention_days = retention_days
        self.log_dir = LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.log_dir / db_filename
        self.lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        hashed_mac TEXT NOT NULL,
                        ssid TEXT,
                        rssi INTEGER,
                        device_class TEXT,
                        is_hidden BOOLEAN,
                        is_rapid BOOLEAN
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_logs(timestamp)')
                conn.commit()
            except sqlite3.Error as e:
                print(f"[Logger Error] Failed to initialize DB: {e}")
            finally:
                if 'conn' in locals():
                    conn.close()

    def _hash_mac(self, mac_address: str) -> str:
        if not mac_address:
            return ""
        return hashlib.sha256(mac_address.encode('utf-8')).hexdigest()

    def log_event(self, data: dict):
        timestamp = datetime.now(timezone.utc).isoformat()

        event_type = data.get("event_type", "unknown")
        raw_mac = data.get("intercepted_mac", "")
        hashed_mac = self._hash_mac(raw_mac)
        ssid = data.get("intercepted_ssid")
        rssi = data.get("intercepted_rssi")
        device_class = data.get("device_class", "Unknown")
        is_hidden = data.get("is_hidden", False)
        is_rapid = data.get("is_rapid", False)

        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO audit_logs (timestamp, event_type, hashed_mac, ssid, rssi, device_class, is_hidden, is_rapid)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (timestamp, event_type, hashed_mac, ssid, rssi, device_class, is_hidden, is_rapid))
                conn.commit()
            except sqlite3.Error as e:
                 print(f"[Logger Error] Failed to log event: {e}")
            finally:
                if 'conn' in locals():
                    conn.close()

            # Non-blocking retention sweep
            threading.Thread(target=self._enforce_retention, daemon=True).start()

    def _enforce_retention(self):
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('DELETE FROM audit_logs WHERE timestamp < ?', (cutoff_date,))
                conn.commit()
            except sqlite3.Error:
                pass
            finally:
                if 'conn' in locals():
                    conn.close()

    def export_geojson(self, output_path: str = "map_export.geojson") -> Path:
        BASE_LAT = 38.8977
        BASE_LON = -77.0365
        features = []

        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT hashed_mac, MAX(rssi) as max_rssi, MAX(ssid) as ssid, MAX(device_class) as device_class,
                           MAX(is_hidden) as is_hidden, MAX(is_rapid) as is_rapid
                    FROM audit_logs
                    WHERE event_type = 'hardware_signature_match' AND rssi IS NOT NULL
                    GROUP BY hashed_mac
                ''')
                rows = cursor.fetchall()
            except sqlite3.Error as e:
                raise RuntimeError(f"Database error during export: {e}")
            finally:
                if 'conn' in locals():
                    conn.close()

        for row in rows:
            hashed_mac, rssi, ssid, device_class, is_hidden, is_rapid = row

            clamped_rssi = max(-100, min(-30, rssi))
            radius_meters = ((abs(clamped_rssi) - 30) / 70.0) * 490 + 10
            radius_deg = radius_meters / 111000.0

            hash_val = int(hashed_mac[:8], 16)
            angle_rad = (hash_val % 360) * (math.pi / 180.0)

            lat = BASE_LAT + (radius_deg * math.cos(angle_rad))
            lon = BASE_LON + (radius_deg * math.sin(angle_rad))

            heatmap_weight = 1.0 - ((abs(clamped_rssi) - 30) / 70.0)

            point = geojson.Point((lon, lat))
            properties = {
                "hashed_mac": hashed_mac,
                "ssid": ssid,
                "rssi": rssi,
                "device_class": device_class,
                "is_hidden": bool(is_hidden),
                "is_rapid": bool(is_rapid),
                "heatmap_weight": round(heatmap_weight, 2)
            }
            features.append(geojson.Feature(geometry=point, properties=properties))

        feature_collection = geojson.FeatureCollection(features)

        export_file = self.log_dir / output_path
        try:
            with open(export_file, 'w', encoding='utf-8') as f:
                geojson.dump(feature_collection, f, indent=2)
        except IOError as e:
             raise RuntimeError(f"Failed to write GeoJSON file: {e}")

        return export_file
