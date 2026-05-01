# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyscard",
#     "pyserial",
# ]
# ///

import curses
import serial
import serial.tools.list_ports
from smartcard.System import readers
from smartcard.util import toHexString
from smartcard.Exceptions import NoCardException, CardConnectionException, CardConnectionObserver
import json
import logging
import datetime
import threading
import queue
import time
import os

# --- LOGGING SETUP ---
LOG_FILE = "hardware_audit.json"

def setup_logger():
    logger = logging.getLogger("ChimericAudit")
    logger.setLevel(logging.DEBUG)
    # File handler for JSON
    fh = logging.FileHandler(LOG_FILE)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)
    return logger

logger = setup_logger()

def log_event(event_type, level, data):
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "type": event_type,
        "level": level,
        "data": data
    }
    logger.info(json.dumps(entry))
    return entry

# --- UTILS ---
def luhn_checksum(card_number):
    def digits_of(n):
        return [int(d) for d in str(n)]
    digits = digits_of(card_number)
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    checksum = sum(odd_digits)
    for d in even_digits:
        checksum += sum(digits_of(d*2))
    return checksum % 10

def validate_pan(pan):
    if not pan.isdigit():
        return False, "PAN contains non-digits"
    is_valid = luhn_checksum(pan) == 0
    return is_valid, "Valid Luhn" if is_valid else "Invalid Luhn checksum"

# --- HARDWARE MODULES ---
COMMON_AIDS = {
    "PPSE": [0x32, 0x50, 0x41, 0x59, 0x2E, 0x53, 0x59, 0x53, 0x2E, 0x44, 0x44, 0x46, 0x30, 0x31],
    "Visa": [0xA0, 0x00, 0x00, 0x00, 0x03, 0x10, 0x10],
    "Mastercard": [0xA0, 0x00, 0x00, 0x00, 0x04, 0x10, 0x10],
    "Amex": [0xA0, 0x00, 0x00, 0x00, 0x25, 0x01],
    "Discover": [0xA0, 0x00, 0x00, 0x01, 0x52, 0x30, 0x10]
}

def auto_discover_serial():
    ports = serial.tools.list_ports.comports()
    # Prioritize USB serial adapters
    usb_ports = [p.device for p in ports if 'USB' in p.description or 'USB' in p.device]
    if usb_ports:
        return usb_ports[0]
    elif ports:
        return ports[0].device
    return None

def ccid_audit(q):
    q.put(("log", {"level": "info", "msg": "Starting CCID Auto-Discovery..."}))
    try:
        r = readers()
        if not r:
            msg = "No smart card readers found. Check hardware connection."
            log_event("ccid", "error", {"error": msg})
            q.put(("log", {"level": "error", "msg": msg}))
            q.put(("result", {"status": "error", "module": "CCID", "message": msg}))
            return

        reader = r[0]
        q.put(("log", {"level": "info", "msg": f"Found reader: {reader}"}))
        connection = reader.createConnection()
        connection.connect()

        atr = connection.getATR()
        atr_hex = toHexString(atr)
        q.put(("log", {"level": "info", "msg": f"ATR: {atr_hex}"}))

        results = {"status": "success", "module": "CCID", "reader": str(reader), "atr": atr_hex, "fuzz_results": {}}

        # Fuzzing mode
        q.put(("log", {"level": "info", "msg": "Initiating AID fuzzing..."}))
        for aid_name, aid_bytes in COMMON_AIDS.items():
            apdu = [0x00, 0xA4, 0x04, 0x00, len(aid_bytes)] + aid_bytes + [0x00]
            try:
                data, sw1, sw2 = connection.transmit(apdu)
                status_hex = f"{hex(sw1)} {hex(sw2)}"
                found = (sw1 == 0x90 and sw2 == 0x00)
                results["fuzz_results"][aid_name] = {"found": found, "sw": status_hex}
                if found:
                    q.put(("log", {"level": "success", "msg": f"Match found for AID: {aid_name}"}))
                else:
                    q.put(("log", {"level": "debug", "msg": f"No match for AID: {aid_name} ({status_hex})"}))
            except Exception as e:
                q.put(("log", {"level": "error", "msg": f"Error probing {aid_name}: {e}"}))
                results["fuzz_results"][aid_name] = {"error": str(e)}

        log_event("ccid", "info", results)
        q.put(("result", results))

    except NoCardException:
        msg = "No card inserted in reader."
        log_event("ccid", "warning", {"error": msg})
        q.put(("log", {"level": "warning", "msg": msg}))
        q.put(("result", {"status": "warning", "module": "CCID", "message": msg}))
    except CardConnectionException as e:
        msg = f"Card connection error (protocol mismatch or exclusive lock): {e}"
        log_event("ccid", "error", {"error": msg})
        q.put(("log", {"level": "error", "msg": msg}))
        q.put(("result", {"status": "error", "module": "CCID", "message": msg}))
    except Exception as e:
        msg = f"CCID Exception: {e}"
        log_event("ccid", "error", {"error": msg})
        q.put(("log", {"level": "error", "msg": msg}))
        q.put(("result", {"status": "error", "module": "CCID", "message": msg}))

def magstripe_audit(q):
    q.put(("log", {"level": "info", "msg": "Starting Serial Auto-Discovery..."}))
    port = auto_discover_serial()

    if not port:
        msg = "No serial ports discovered. Check USB connection."
        log_event("magstripe", "error", {"error": msg})
        q.put(("log", {"level": "error", "msg": msg}))
        q.put(("result", {"status": "error", "module": "Magstripe", "message": msg}))
        return

    q.put(("log", {"level": "info", "msg": f"Connecting to serial port: {port}"}))
    try:
        ser = serial.Serial(port, 9600, timeout=2)
        q.put(("log", {"level": "info", "msg": "Awaiting Track 2 data... (Timeout: 2s)"}))

        # Try to read line
        # Depending on the hardware it might need a command or just push data
        ser.write(b"READ\n")
        data = ser.readline().decode('utf-8', errors='ignore').strip()
        ser.close()

        if not data:
            msg = "No data received from serial device on read timeout."
            log_event("magstripe", "warning", {"error": msg})
            q.put(("log", {"level": "warning", "msg": msg}))
            q.put(("result", {"status": "warning", "module": "Magstripe", "message": msg}))
            return

        q.put(("log", {"level": "info", "msg": f"Raw data received: {data}"}))

        # Enhanced Track 2 Parsing
        results = {
            "status": "success",
            "module": "Magstripe",
            "port": port,
            "original_data": data,
            "parsed": False
        }

        if '=' in data:
            parts = data.split('=')
            pan = parts[0]
            if pan.startswith(';'):
                pan = pan[1:]

            rest = parts[1]
            if len(rest) >= 7:
                results["parsed"] = True
                exp_year = rest[0:2]
                exp_month = rest[2:4]
                service_code = rest[4:7]
                discretionary = rest[7:].split('?')[0] # Remove end sentinel

                valid_luhn, luhn_msg = validate_pan(pan)

                results["parsed_data"] = {
                    "pan": pan,
                    "pan_length": len(pan),
                    "luhn_valid": valid_luhn,
                    "expiration": f"20{exp_year}-{exp_month}",
                    "service_code": service_code,
                    "discretionary_data": discretionary
                }

                # Service Code modification for testing
                new_service_code = '101'
                modified_data = f";{pan}={exp_year}{exp_month}{new_service_code}{discretionary}?"
                results["modified_data"] = modified_data

                q.put(("log", {"level": "success", "msg": f"Track 2 successfully parsed. Luhn: {'Valid' if valid_luhn else 'Invalid'}"}))
                if not valid_luhn:
                    q.put(("log", {"level": "warning", "msg": f"Luhn validation failed: {luhn_msg}"}))

        if not results.get("parsed"):
            q.put(("log", {"level": "warning", "msg": "Data received but failed standard Track 2 parsing constraints."}))

        log_event("magstripe", "info", results)
        q.put(("result", results))

    except serial.SerialException as e:
        msg = f"Serial Port Error (Permission denied or device disconnected): {e}"
        log_event("magstripe", "error", {"error": msg})
        q.put(("log", {"level": "error", "msg": msg}))
        q.put(("result", {"status": "error", "module": "Magstripe", "message": msg}))
    except Exception as e:
        msg = f"Magstripe Exception: {e}"
        log_event("magstripe", "error", {"error": msg})
        q.put(("log", {"level": "error", "msg": msg}))
        q.put(("result", {"status": "error", "module": "Magstripe", "message": msg}))


# --- TUI IMPLEMENTATION ---

ASCII_SKULL = """
      .ok0KXXKK0ko.
    .c0WMMMMMMMMMMW0c.
   .dWMMMMMMMMMMMMMMWd.
   oWMMMWX0kkkk0XWMMMWo
  .xMMMXc..    ..cXMMMx.
  .xMMWd.  'XX'  .dWMMx.
   oWMWo.  'XX'  .oWMWo
   .dWMXl.      .lXMWd.
    .c0WMXxo::oxXMW0c.
      .ok0KXXKK0ko.
"""

class TUI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.h, self.w = self.stdscr.getmaxyx()

        # Colors
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_RED, -1)     # Danger/Title
        curses.init_pair(2, curses.COLOR_CYAN, -1)    # UI borders/Selections
        curses.init_pair(3, curses.COLOR_WHITE, -1)   # Normal text
        curses.init_pair(4, curses.COLOR_GREEN, -1)   # Success
        curses.init_pair(5, curses.COLOR_YELLOW, -1)  # Warning

        self.menu_items = ['1. CCID Discovery & Fuzzing', '2. Magstripe Parsing & Validation', '3. Verification Audit', '4. Exit']
        self.current_row = 0

        self.log_messages = []
        self.max_log_lines = 10

        self.current_result = None
        self.is_running = False

        self.q = queue.Queue()

    def draw_skull(self, start_y, start_x):
        lines = ASCII_SKULL.strip("\n").split("\n")
        for i, line in enumerate(lines):
            try:
                self.stdscr.addstr(start_y + i, start_x, line, curses.color_pair(1) | curses.A_BOLD)
            except curses.error:
                pass
        return len(lines)

    def draw_layout(self):
        self.stdscr.clear()
        try:
            self.h, self.w = self.stdscr.getmaxyx()

            # Title
            title = "CHIMERIC OS - HARDWARE DIAGNOSTIC UTILITY"
            self.stdscr.addstr(1, max(0, self.w//2 - len(title)//2), title, curses.color_pair(1) | curses.A_BOLD)

            # Header separator
            self.stdscr.addstr(2, 0, "-" * self.w, curses.color_pair(2))

            # Split screen vertically if wide enough, else horizontal stack
            left_pane_w = min(40, self.w - 2)

            # Menu
            menu_start_y = 4
            self.stdscr.addstr(menu_start_y, 2, "MODULE SELECTION:", curses.color_pair(2) | curses.A_BOLD)
            for idx, item in enumerate(self.menu_items):
                y = menu_start_y + 2 + idx
                if idx == self.current_row:
                    self.stdscr.addstr(y, 4, item, curses.color_pair(2) | curses.A_REVERSE)
                else:
                    self.stdscr.addstr(y, 4, item, curses.color_pair(3))

            # ASCII Art (bottom left)
            skull_y = self.h - 15
            if skull_y > menu_start_y + 2 + len(self.menu_items):
                self.draw_skull(skull_y, 2)

            # Vertical separator
            if self.w > 60:
                for y in range(3, self.h - 3):
                    self.stdscr.addstr(y, left_pane_w + 2, "|", curses.color_pair(2))

            # Data/Results Panel
            res_start_x = left_pane_w + 4 if self.w > 60 else 2
            res_start_y = 4
            self.stdscr.addstr(res_start_y, res_start_x, "AUDIT RESULTS:", curses.color_pair(2) | curses.A_BOLD)

            y = res_start_y + 2
            if self.current_result:
                for k, v in self.current_result.items():
                    if y >= self.h - max(12, self.max_log_lines + 4): # leave room for logs
                        break

                    if isinstance(v, dict):
                        self.stdscr.addstr(y, res_start_x, f"{k.upper()}:", curses.color_pair(3) | curses.A_BOLD)
                        y += 1
                        for sub_k, sub_v in v.items():
                            if y >= self.h - max(12, self.max_log_lines + 4): break
                            val_str = str(sub_v)
                            # Truncate long strings
                            max_len = self.w - res_start_x - len(sub_k) - 6
                            if len(val_str) > max_len and max_len > 0:
                                val_str = val_str[:max_len] + "..."

                            self.stdscr.addstr(y, res_start_x + 2, f"{sub_k}: ", curses.color_pair(2))
                            self.stdscr.addstr(val_str, curses.color_pair(3))
                            y += 1
                    else:
                        val_str = str(v)
                        max_len = self.w - res_start_x - len(k) - 4
                        if len(val_str) > max_len and max_len > 0:
                            val_str = val_str[:max_len] + "..."
                        self.stdscr.addstr(y, res_start_x, f"{k}: ", curses.color_pair(2))
                        self.stdscr.addstr(val_str, curses.color_pair(3))
                        y += 1
            else:
                self.stdscr.addstr(y, res_start_x, "Awaiting module execution...", curses.color_pair(3) | curses.A_DIM)

            # Logs Panel
            log_start_y = self.h - self.max_log_lines - 3
            self.stdscr.addstr(log_start_y, 0, "-" * self.w, curses.color_pair(2))
            self.stdscr.addstr(log_start_y+1, 2, "REAL-TIME LOGS:", curses.color_pair(2) | curses.A_BOLD)

            log_y = log_start_y + 2
            for log in self.log_messages[-self.max_log_lines:]:
                if log_y >= self.h - 1: break

                lvl = log['level'].lower()
                cp = curses.color_pair(3)
                if lvl == 'error': cp = curses.color_pair(1)
                elif lvl == 'warning': cp = curses.color_pair(5)
                elif lvl == 'success': cp = curses.color_pair(4)
                elif lvl == 'info': cp = curses.color_pair(2)

                prefix = f"[{lvl.upper()}] "
                msg = prefix + log['msg']
                if len(msg) > self.w - 4:
                    msg = msg[:self.w - 7] + "..."

                self.stdscr.addstr(log_y, 2, prefix, cp | curses.A_BOLD)
                self.stdscr.addstr(log['msg'][:self.w - 4 - len(prefix)], cp)
                log_y += 1

            # Status Bar
            self.stdscr.addstr(self.h - 1, 0, " " * self.w, curses.color_pair(2) | curses.A_REVERSE)
            status_text = " [UP/DOWN] Navigate | [ENTER] Execute | [Ctrl+C] Force Quit "
            if self.is_running:
                status_text = " EXECUTING... PLEASE WAIT | " + status_text
            self.stdscr.addstr(self.h - 1, 0, status_text[:self.w], curses.color_pair(2) | curses.A_REVERSE)

        except curses.error:
            pass # Handle window too small gracefully

        self.stdscr.refresh()

    def process_queue(self):
        dirty = False
        while not self.q.empty():
            dirty = True
            try:
                msg_type, payload = self.q.get_nowait()
                if msg_type == "log":
                    self.log_messages.append(payload)
                elif msg_type == "result":
                    self.current_result = payload
                    self.is_running = False
            except queue.Empty:
                break
        return dirty

    def run(self):
        curses.curs_set(0)
        self.stdscr.nodelay(True) # Non-blocking input

        self.log_messages.append({"level": "info", "msg": "TUI Initialized. Hardware modules ready."})
        self.draw_layout()

        while True:
            # Handle resizing and queue
            try:
                key = self.stdscr.getch()
            except curses.error:
                key = -1

            dirty = self.process_queue()

            if key != -1:
                dirty = True
                if key == curses.KEY_RESIZE:
                    self.h, self.w = self.stdscr.getmaxyx()
                    self.stdscr.clear()
                elif not self.is_running:
                    if key == curses.KEY_UP and self.current_row > 0:
                        self.current_row -= 1
                    elif key == curses.KEY_DOWN and self.current_row < len(self.menu_items) - 1:
                        self.current_row += 1
                    elif key == curses.KEY_ENTER or key in [10, 13]:
                        if self.current_row == 0:
                            self.is_running = True
                            self.current_result = None
                            self.log_messages.append({"level": "info", "msg": "-"*20})
                            threading.Thread(target=ccid_audit, args=(self.q,), daemon=True).start()
                        elif self.current_row == 1:
                            self.is_running = True
                            self.current_result = None
                            self.log_messages.append({"level": "info", "msg": "-"*20})
                            threading.Thread(target=magstripe_audit, args=(self.q,), daemon=True).start()
                        elif self.current_row == 2:
                            self.is_running = True
                            self.current_result = {"status": "verification_started"}
                            self.log_messages.append({"level": "info", "msg": "-"*20})
                            self.log_messages.append({"level": "info", "msg": "Running Verification Audit..."})

                            def verification_thread():
                                local_q = queue.Queue()
                                ccid_audit(local_q)
                                magstripe_audit(local_q)

                                results = {"CCID_Status": "Failed", "Magstripe_Status": "Failed"}

                                while not local_q.empty():
                                    msg_type, payload = local_q.get()
                                    if msg_type == "log":
                                        self.q.put(("log", payload))
                                    elif msg_type == "result":
                                        if payload.get("module") == "CCID":
                                            results["CCID_Status"] = payload.get("status")
                                            if payload.get("status") == "success":
                                                results["CCID_ATR"] = payload.get("atr")
                                        elif payload.get("module") == "Magstripe":
                                            results["Magstripe_Status"] = payload.get("status")
                                            if payload.get("status") == "success":
                                                results["Magstripe_PAN"] = payload.get("parsed_data", {}).get("pan", "Unknown")

                                if results["CCID_Status"] == "success" and results["Magstripe_Status"] == "success":
                                    self.q.put(("log", {"level": "success", "msg": "Verification Audit completed successfully."}))
                                else:
                                    self.q.put(("log", {"level": "warning", "msg": "Verification Audit completed with hardware failures."}))

                                self.q.put(("result", results))

                            threading.Thread(target=verification_thread, daemon=True).start()

                        elif self.current_row == 3:
                            break

            if dirty:
                self.draw_layout()

            time.sleep(0.05) # Prevent 100% CPU usage

def main(stdscr):
    app = TUI(stdscr)
    app.run()

if __name__ == '__main__':
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        print("\nExiting Chimeric OS Hardware Diagnostic Utility.")
