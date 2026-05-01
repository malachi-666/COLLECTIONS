# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyscard",
#     "pyserial",
# ]
# ///

import curses
import curses.textpad
import serial
import serial.tools.list_ports
from smartcard.System import readers
from smartcard.util import toHexString, toBytes
import json
import logging
import datetime
import threading
import queue
import time
import sys
import re

# --- LOGGING SETUP ---
LOG_FILE = "hardware_audit.json"

def setup_logger():
    logger = logging.getLogger("ChimericAudit")
    logger.setLevel(logging.DEBUG)
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


def luhn_check(pan):
    """Luhn Check Digit Validation."""
    if not pan.isdigit(): return False
    digits = [int(d) for d in str(pan)]
    checksum = sum(digits[-1::-2])
    for d in digits[-2::-2]:
        checksum += sum([int(x) for x in str(d*2)])
    return checksum % 10 == 0

def luhn_generate(pan_prefix):
    """Calculates missing digit of a PAN."""
    if not pan_prefix.isdigit(): return None
    digits = [int(d) for d in str(pan_prefix)]
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 0:
            checksum += sum([int(x) for x in str(d * 2)])
        else:
            checksum += d
    check_digit = (10 - (checksum % 10)) % 10
    return str(pan_prefix) + str(check_digit)

def hex_dump(data_bytes):
    if not data_bytes: return ""
    return " ".join([f"{b:02X}" for b in data_bytes])

ISO_7816_ERRORS = {
    "9000": "Normal processing.",
    "6A82": "File not found.",
    "6E00": "Class not supported.",
    "6D00": "Instruction code not supported or invalid.",
    "6700": "Wrong length.",
    "6982": "Security status not satisfied.",
    "6985": "Conditions of use not satisfied.",
    "6A81": "Function not supported.",
}


EMV_TAGS = {
    "5A": "Application Primary Account Number (PAN)",
    "57": "Track 2 Equivalent Data",
    "5F20": "Cardholder Name",
    "5F24": "Application Expiration Date",
    "5F25": "Application Effective Date",
    "5F28": "Issuer Country Code",
    "5F30": "Service Code",
    "9F0B": "Cardholder Name Extended",
    "9F1F": "Track 1 Discretionary Data",
    "9F20": "Track 2 Discretionary Data",
    "4F": "Application Identifier (AID)",
    "50": "Application Label",
    "84": "Dedicated File (DF) Name",
    "9F06": "Application Identifier (AID) - terminal",
    "9F08": "Application Version Number",
    "9F12": "Application Preferred Name"
}

# --- BER-TLV Parser ---
def parse_ber_tlv(data):

    """Robust BER-TLV Parser."""
    parsed = {}
    i = 0
    while i < len(data):
        # Read Tag
        tag = data[i]
        tag_hex = f"{tag:02X}"
        i += 1
        if (tag & 0x1F) == 0x1F:
            tag2 = data[i]
            tag_hex += f"{tag2:02X}"
            i += 1
            while (tag2 & 0x80) == 0x80:
                tag2 = data[i]
                tag_hex += f"{tag2:02X}"
                i += 1

        if i >= len(data): break

        # Read Length
        length = data[i]
        i += 1
        if length > 127:
            num_len_bytes = length & 0x7F
            length = 0
            for _ in range(num_len_bytes):
                if i >= len(data): break
                length = (length << 8) + data[i]
                i += 1

        if i + length > len(data):
            break

        value = data[i:i+length]
        i += length


        # Constructed Data Object check
        if (tag & 0x20) == 0x20:
            parsed[tag_hex] = parse_ber_tlv(value)
        else:
            val_hex = toHexString(value)
            desc = EMV_TAGS.get(tag_hex, f"Tag {tag_hex}")
            parsed[f"{tag_hex} ({desc})"] = val_hex

    return parsed


# --- Architecture ---

class BaseModule:
    """Modular Extension Template."""
    def __init__(self, name, desc, author, help_text=""):
        self.metadata = {"name": name, "description": desc, "author": author}
        self.help_text = help_text

    def validate(self):
        return True, "Valid"

    def get_actions(self):
        return [("Generate Check Digit", True), ("Validate PAN", True)]

    def run(self, action, user_input, q):
        val = user_input.strip()
        if action == "Generate Check Digit":
            result = luhn_generate(val)
            if result:
                q.put(("result", {"Input_Prefix": val, "Generated_PAN": result}))
                q.put(("log", {"level": "success", "msg": "Luhn generation complete."}))
            else:
                q.put(("log", {"level": "alert", "msg": "Invalid input. PAN prefix must be numeric."}))
        elif action == "Validate PAN":
            is_valid = luhn_check(val)
            q.put(("result", {"PAN": val, "Luhn_Valid": is_valid}))
            q.put(("log", {"level": "success" if is_valid else "alert", "msg": f"Validation: {'Pass' if is_valid else 'Fail'}"}))


# --- TUI ---

XD_SKULL = """
      .oO@@@@@@@@Oo.
    .o@@@@@@@@@@@@@@o.
   .@@@@@@@@@@@@@@@@@@.
   o@@@@@@@@@@@@@@@@@@o
  .@@@@O:.. __ ..:O@@@@.
  .@@@O.  X    X  .O@@@.
   o@@O.  ======  .O@@o
   .O@@o.        .o@@O.
    .:O@@Oo::::oO@@O:.
      .oO@@@@@@@@Oo.
"""

class TUI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        curses.curs_set(0)
        self.stdscr.nodelay(True)
        self.h, self.w = self.stdscr.getmaxyx()

        self.modules = [cls() for cls in BaseModule.__subclasses__()]
        self.state = "MENU"
        self.menu_idx = 0
        self.sub_idx = 0
        self.input_text = ""

        self.q = queue.Queue()
        self.logs = []

        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_RED, -1)     # Alerts
        curses.init_pair(2, curses.COLOR_CYAN, -1)    # Logic
        curses.init_pair(3, curses.COLOR_GREEN, -1)   # Success

        self.log_pad = curses.newpad(5000, self.w)
        self.log_pad_pos = 0
        self.auto_scroll = True

        # Hardware Status Threading
        self.hw_status = {"OMNIKEY": "SCANNING", "MSR": "SCANNING"}
        threading.Thread(target=self._status_poller, daemon=True).start()

    def _status_poller(self):
        while True:
            # Poll CCID
            try:
                r = readers()
                self.hw_status["OMNIKEY"] = "ONLINE" if r else "OFFLINE"
            except:
                self.hw_status["OMNIKEY"] = "ERROR"

            # Poll Serial
            try:
                ports = serial.tools.list_ports.comports()
                self.hw_status["MSR"] = "ONLINE" if ports else "OFFLINE"
            except:
                self.hw_status["MSR"] = "ERROR"
            time.sleep(2)

    def get_border(self):
        return "+" + ("~v^" * (self.w // 3))[:self.w-2] + "+"

    def draw(self):
        self.stdscr.clear()

        # Double-Border Layout
        border = self.get_border()
        self.stdscr.addstr(0, 0, border, curses.color_pair(2))

        for i, line in enumerate(XD_SKULL.strip().split('\n')):
            self.stdscr.addstr(i+1, 2, line, curses.color_pair(1))

        start_y = 13

        if self.state in ["MENU", "SUBMENU", "INPUT"]:
            self.stdscr.addstr(start_y, 2, "MODULE SELECTION (j/k: Navigate, Enter: Select, q: Quit):", curses.color_pair(2) | curses.A_BOLD)
            for idx, mod in enumerate(self.modules):
                prefix = "[*] " if idx == self.menu_idx and self.state == "MENU" else "[ ] "
                attr = curses.A_REVERSE if idx == self.menu_idx and self.state == "MENU" else curses.A_NORMAL
                self.stdscr.addstr(start_y + 2 + idx, 4, f"{prefix}{mod.metadata['name']} - {mod.metadata['description']}", attr | curses.color_pair(2))

            mod = self.modules[self.menu_idx]
            self.stdscr.addstr(start_y + 2 + len(self.modules) + 1, 4, f"INFO: {mod.help_text}", curses.color_pair(2))

        if self.state == "SUBMENU":
            mod = self.modules[self.menu_idx]
            actions = mod.get_actions()
            sub_start_y = start_y + 2 + len(self.modules) + 3
            self.stdscr.addstr(sub_start_y, 2, f"ACTIONS FOR {mod.metadata['name'].upper()} (j/k: Navigate, Enter: Execute, ESC: Back):", curses.color_pair(2) | curses.A_BOLD)
            for idx, act in enumerate(actions):
                prefix = "> " if idx == self.sub_idx else "  "
                attr = curses.A_REVERSE if idx == self.sub_idx else curses.A_NORMAL
                req_in = "[Req Input]" if act[1] else ""
                self.stdscr.addstr(sub_start_y + 2 + idx, 4, f"{prefix}{act[0]} {req_in}", attr | curses.color_pair(2))

        if self.state == "INPUT":
            mod = self.modules[self.menu_idx]
            act = mod.get_actions()[self.sub_idx]
            in_y = start_y + 2 + len(self.modules) + 8
            self.stdscr.addstr(in_y, 2, f"INPUT REQUIRED FOR '{act[0]}':", curses.color_pair(2) | curses.A_BOLD)
            self.stdscr.addstr(in_y + 1, 4, f"> {self.input_text}_", curses.color_pair(2))

        # Bottom Status Bar
        stat_y = self.h - 1
        stat_str = f" CHIMERIC OS | OMNIKEY: {self.hw_status['OMNIKEY']} | MSR: {self.hw_status['MSR']} "
        self.stdscr.addstr(stat_y, 0, stat_str.ljust(self.w), curses.color_pair(1) | curses.A_REVERSE)

        # Log Pad Border
        log_h = max(5, self.h // 2 - 2)
        start_log_y = stat_y - log_h - 1
        self.stdscr.addstr(start_log_y - 1, 0, border, curses.color_pair(2))
        self.stdscr.addstr(start_log_y - 1, 2, "[ FORENSIC LOG / RESULTS (PgUp/PgDn to scroll) ]", curses.color_pair(2) | curses.A_REVERSE)

        self.stdscr.refresh()

        # Draw Log Pad
        self.log_pad.clear()
        for i, lg in enumerate(self.logs):
            cp = curses.color_pair(2) # Default Logic Cyan
            if "[ALERT]" in lg or "[ERROR]" in lg: cp = curses.color_pair(1)
            elif "[SUCCESS]" in lg or "[RESULT]" in lg: cp = curses.color_pair(3)
            self.log_pad.addstr(i, 0, lg[:self.w-1], cp)

        max_scroll = max(0, len(self.logs) - log_h)
        if self.auto_scroll:
            self.log_pad_pos = max_scroll

        if self.log_pad_pos < 0: self.log_pad_pos = 0
        if self.log_pad_pos > max_scroll: self.log_pad_pos = max_scroll

        try:
            self.log_pad.refresh(self.log_pad_pos, 0, start_log_y, 1, stat_y - 1, self.w-1)
        except curses.error:
            pass

    def process_queue(self):
        dirty = False
        while not self.q.empty():
            try:
                msg_type, payload = self.q.get_nowait()
                stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                if msg_type == "log":
                    entry_str = f"[{stamp}] [{payload['level'].upper()}] {payload['msg']}"
                    self.logs.append(entry_str)
                    log_event("hw_event", payload['level'], payload['msg'])
                    dirty = True
                elif msg_type == "result":
                    res_str = json.dumps(payload, indent=2)
                    for line in res_str.split('\n'):
                        for chunk in [line[i:i+self.w-20] for i in range(0, max(1, len(line)), self.w-20)]:
                            self.logs.append(f"[{stamp}] [RESULT] {chunk}")
                    log_event("hw_result", "info", payload)
                    dirty = True
                elif msg_type == "done":
                    self.state = "SUBMENU"
                    dirty = True
            except Exception:
                pass

        if len(self.logs) > 4000:
            self.logs = self.logs[-4000:]
        return dirty

    def run(self):
        self.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [LOGIC] Chimeric OS Hardware Control Suite Online.")
        self.draw()

        while True:
            dirty = self.process_queue()
            try:
                key = self.stdscr.getch()
            except curses.error:
                key = -1

            if key != -1:
                dirty = True

                # Global Scrolling
                if key == curses.KEY_NPAGE:
                    self.auto_scroll = False
                    self.log_pad_pos += 5
                elif key == curses.KEY_PPAGE:
                    self.auto_scroll = False
                    self.log_pad_pos -= 5
                elif key == curses.KEY_END:
                    self.auto_scroll = True
                elif key == curses.KEY_RESIZE:
                    self.h, self.w = self.stdscr.getmaxyx()
                    self.log_pad = curses.newpad(5000, max(1, self.w))
                    self.stdscr.clear()

                # State: MENU
                elif self.state == "MENU":
                    if key in [curses.KEY_UP, ord('k')] and self.menu_idx > 0:
                        self.menu_idx -= 1
                    elif key in [curses.KEY_DOWN, ord('j')] and self.menu_idx < len(self.modules) - 1:
                        self.menu_idx += 1
                    elif key in [10, 13]:
                        self.state = "SUBMENU"
                        self.sub_idx = 0
                    elif key == ord('q'):
                        break

                # State: SUBMENU
                elif self.state == "SUBMENU":
                    mod = self.modules[self.menu_idx]
                    actions = mod.get_actions()
                    if key in [curses.KEY_UP, ord('k')] and self.sub_idx > 0:
                        self.sub_idx -= 1
                    elif key in [curses.KEY_DOWN, ord('j')] and self.sub_idx < len(actions) - 1:
                        self.sub_idx += 1
                    elif key == 27:
                        self.state = "MENU"
                    elif key in [10, 13]:
                        act = actions[self.sub_idx]
                        if act[1]:
                            self.state = "INPUT"
                            self.input_text = ""
                            curses.curs_set(1)
                        else:
                            self._launch_module(mod, act[0], "")

                # State: INPUT
                elif self.state == "INPUT":
                    if key == 27:
                        self.state = "SUBMENU"
                        curses.curs_set(0)
                    elif key in [10, 13]:
                        curses.curs_set(0)
                        mod = self.modules[self.menu_idx]
                        act = mod.get_actions()[self.sub_idx]
                        self._launch_module(mod, act[0], self.input_text)
                    elif key in [curses.KEY_BACKSPACE, 127, 8]:
                        self.input_text = self.input_text[:-1]
                    elif 32 <= key <= 126:  # Only printable ASCII
                        self.input_text += chr(key)

            if dirty:
                self.draw()
            time.sleep(0.05)

    def _launch_module(self, mod, action_name, user_input):
        self.auto_scroll = True
        self.state = "RUNNING"
        self.draw()
        valid, msg = mod.validate()
        if valid:
            self.q.put(("log", {"level": "logic", "msg": f"Initiating {action_name}..."}))
            def worker():
                mod.run(action_name, user_input, self.q)
                self.q.put(("done", None))
            threading.Thread(target=worker, daemon=True).start()
        else:
            self.q.put(("log", {"level": "alert", "msg": f"Validation failed: {msg}"}))
            self.state = "SUBMENU"

if __name__ == "__main__":
    try:
        curses.wrapper(lambda stdscr: TUI(stdscr).run())
    except KeyboardInterrupt:
        print("\n[!] Exiting Chimeric OS Hardware Control Suite.")
