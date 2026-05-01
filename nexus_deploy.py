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
            parsed[tag_hex] = toHexString(value)

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
        return [("Run Default", False)]

    def run(self, action, user_input, q):
        raise NotImplementedError()

class CCIDModule(BaseModule):
    def __init__(self):
        super().__init__(
            "OMNIKEY PC/SC Mastery",
            "ISO-7816-4 selection flow, EMV Extraction.",
            "Chimeric",
            "Advanced PC/SC suite. Supports full EMV flows."
        )

    def validate(self):
        if not readers():
            return False, "No PC/SC readers found."
        return True, "Reader available."

    def get_actions(self):
        return [
            ("EMV Data Extraction (PPSE -> GPO -> Read Rec)", False),
            ("Memory Card Probing (OmniKey Sync)", False),
            ("Raw APDU Terminal", True)
        ]

    def run(self, action, user_input, q):
        try:
            r = readers()[0]
            q.put(("log", {"level": "info", "msg": f"Connecting to {r}"}))
            conn = r.createConnection()
            conn.connect()
            atr = conn.getATR()
            q.put(("result", {"ATR": toHexString(atr)}))

            if action == "EMV Data Extraction (PPSE -> GPO -> Read Rec)":
                self._emv_extraction(conn, q)
            elif action == "Memory Card Probing (OmniKey Sync)":
                self._memory_probing(conn, q)
            elif action == "Raw APDU Terminal":
                self._raw_apdu(conn, user_input, q)

        except Exception as e:
            q.put(("log", {"level": "error", "msg": f"CCID Error: {str(e)}"}))

    def _emv_extraction(self, conn, q):
        q.put(("log", {"level": "info", "msg": "Starting EMV Flow..."}))

        # 1. Select PPSE
        apdu_ppse = [0x00, 0xA4, 0x04, 0x00, 0x0E, 0x32, 0x50, 0x41, 0x59, 0x2E, 0x53, 0x59, 0x53, 0x2E, 0x44, 0x44, 0x46, 0x30, 0x31, 0x00]
        data, sw1, sw2 = conn.transmit(apdu_ppse)
        q.put(("log", {"level": "logic", "msg": f"PPSE Select: {hex(sw1)} {hex(sw2)}"}))

        if sw1 != 0x90:
            q.put(("log", {"level": "alert", "msg": "Failed to select PPSE."}))
            return

        tlv = parse_ber_tlv(data)
        q.put(("result", {"PPSE_TLV": tlv}))

        aid_hex = None
        try:
            fci_prop = tlv.get("6F", {}).get("A5", {})
            bf0c = fci_prop.get("BF0C", {})
            app_template = bf0c.get("61", {})
            if isinstance(app_template, dict):
                aid_hex = app_template.get("4F")
        except:
            pass

        common_aids = {
            "Visa": [0xA0, 0x00, 0x00, 0x00, 0x03, 0x10, 0x10],
            "Mastercard": [0xA0, 0x00, 0x00, 0x00, 0x04, 0x10, 0x10]
        }

        aid_selected = False
        if aid_hex:
            q.put(("log", {"level": "info", "msg": f"Extracted AID: {aid_hex}"}))
            aid_bytes = toBytes(aid_hex)
            apdu_aid = [0x00, 0xA4, 0x04, 0x00, len(aid_bytes)] + aid_bytes + [0x00]
            data, sw1, sw2 = conn.transmit(apdu_aid)
            if sw1 == 0x90:
                aid_selected = True
                tlv = parse_ber_tlv(data)
                q.put(("result", {"AID_TLV": tlv}))

        if not aid_selected:
            q.put(("log", {"level": "logic", "msg": "Trying fallback AIDs..."}))
            for name, aid_bytes in common_aids.items():
                apdu_aid = [0x00, 0xA4, 0x04, 0x00, len(aid_bytes)] + aid_bytes + [0x00]
                data, sw1, sw2 = conn.transmit(apdu_aid)
                if sw1 == 0x90:
                    q.put(("log", {"level": "success", "msg": f"Fallback selected {name} AID."}))
                    aid_selected = True
                    break

        if not aid_selected:
            q.put(("log", {"level": "alert", "msg": "Could not select any application AID."}))
            return

        # 3. GET PROCESSING OPTIONS
        q.put(("log", {"level": "info", "msg": "Issuing GET PROCESSING OPTIONS..."}))
        apdu_gpo = [0x80, 0xA8, 0x00, 0x00, 0x02, 0x83, 0x00, 0x00]
        data, sw1, sw2 = conn.transmit(apdu_gpo)
        if sw1 != 0x90:
            q.put(("log", {"level": "alert", "msg": f"GPO Failed: {hex(sw1)} {hex(sw2)}"}))
            return

        q.put(("result", {"GPO_Response": toHexString(data)}))

        # 4. READ RECORD Brute Force (SFI 1-3, Records 1-5)
        q.put(("log", {"level": "info", "msg": "Brute forcing READ RECORD..."}))
        found_data = {}
        for sfi in range(1, 4):
            for rec in range(1, 6):
                p2 = (sfi << 3) | 0x04
                apdu_read = [0x00, 0xB2, rec, p2, 0x00]
                data, sw1, sw2 = conn.transmit(apdu_read)
                if sw1 == 0x90:
                    parsed = parse_ber_tlv(data)
                    found_data[f"SFI_{sfi}_Rec_{rec}"] = parsed
                    template = parsed.get("70", {})
                    if "5A" in template:
                        q.put(("log", {"level": "success", "msg": f"Found PAN (5A): {template['5A']}"}))
                    if "57" in template:
                        q.put(("log", {"level": "success", "msg": f"Found Track 2 Equivalent (57): {template['57']}"}))

        if found_data:
            q.put(("result", {"Records": found_data}))

    def _memory_probing(self, conn, q):
        q.put(("log", {"level": "info", "msg": "Probing Memory Cards via OmniKey Synchronous API..."}))
        targets = {
            "SLE4442": [0xFF, 0x20, 0x00, 0x00, 0x02, 0x01, 0x00],
            "SLE4428": [0xFF, 0x20, 0x00, 0x00, 0x02, 0x02, 0x00],
            "I2C (FM24C)": [0xFF, 0x20, 0x00, 0x00, 0x02, 0x06, 0x00]
        }
        for name, apdu in targets.items():
            try:
                data, sw1, sw2 = conn.transmit(apdu)
                if sw1 == 0x90:
                    q.put(("log", {"level": "success", "msg": f"{name} Memory Card Detected! (sw1=90)"}))
                else:
                    q.put(("log", {"level": "logic", "msg": f"{name} probe returned {hex(sw1)}{hex(sw2)}"}))
            except Exception as e:
                q.put(("log", {"level": "alert", "msg": f"{name} probe failed: {e}"}))

    def _raw_apdu(self, conn, user_input, q):
        user_input = user_input.replace(" ", "")
        try:
            apdu = toBytes(user_input)
            q.put(("log", {"level": "info", "msg": f"Sending Raw APDU: {toHexString(apdu)}"}))
            data, sw1, sw2 = conn.transmit(apdu)
            sw_code = f"{sw1:02X}{sw2:02X}"
            desc = ISO_7816_ERRORS.get(sw_code, "Unknown status code.")
            q.put(("result", {"APDU_Response": toHexString(data), "Status": f"{sw_code} ({desc})"}))
        except Exception as e:
            q.put(("log", {"level": "alert", "msg": f"Invalid APDU format or transmission error: {e}"}))


class MSRModule(BaseModule):
    def __init__(self):
        super().__init__(
            "MSR605X Controller",
            "Bit-Level Control for MSR605X hardware.",
            "Chimeric",
            "MSR605X Serial Protocol Implementation. Supports Read, Write, Erase and Interactive Listen modes."
        )

    def validate(self):
        ports = serial.tools.list_ports.comports()
        if not ports:
            return False, "No serial ports found."
        self.port = ports[0].device
        return True, f"Port {self.port} available."

    def get_actions(self):
        return [
            ("Read All Tracks (ISO 7813)", False),
            ("Interactive Swipe (Raw Listener)", False),
            ("Erase All Tracks", False),
            ("Write Tracks (Interactive)", True)
        ]

    def run(self, action, user_input, q):
        try:
            q.put(("log", {"level": "info", "msg": f"Opening {self.port} at 9600 8N1"}))
            with serial.Serial(self.port, 9600, timeout=5) as ser:

                if action == "Read All Tracks (ISO 7813)":
                    self._set_led(ser, '1') # Amber
                    q.put(("log", {"level": "info", "msg": "Issuing READ ALL command (Waiting for swipe)..."}))
                    ser.write(b"\x1b\x72")
                    data = ser.read_until(b"?\x1c")
                    if not data: data = ser.read(200)

                    if data:
                        self._set_led(ser, '2') # Green
                        q.put(("result", {"Raw_Hex": hex_dump(data)}))
                        try:
                            decoded = data.decode('ascii', errors='ignore')
                            self._parse_iso_7813(decoded, q)
                        except Exception as e:
                            q.put(("log", {"level": "alert", "msg": f"Failed to ASCII decode: {e}"}))
                    else:
                        self._set_led(ser, '3') # Red
                        q.put(("log", {"level": "alert", "msg": "Read timeout or empty buffer."}))

                elif action == "Interactive Swipe (Raw Listener)":
                    self._set_led(ser, '1') # Amber
                    q.put(("log", {"level": "info", "msg": "Interactive mode active. Swipe card now..."}))
                    ser.write(b"\x1b\x72")
                    data = ser.read(500)
                    if data:
                        self._set_led(ser, '2') # Green
                        q.put(("log", {"level": "success", "msg": "Card swiped. Raw bitstream captured."}))
                        q.put(("result", {"Raw_Bitstream_Hex": hex_dump(data)}))
                    else:
                        self._set_led(ser, '3') # Red
                        q.put(("log", {"level": "alert", "msg": "No raw data received within timeout."}))

                elif action == "Erase All Tracks":
                    self._set_led(ser, '1')
                    q.put(("log", {"level": "info", "msg": "Issuing ERASE ALL (Swipe to confirm)..."}))
                    ser.write(b"\x1b\x63\x07")
                    data = ser.read(10)
                    self._set_led(ser, '2')
                    q.put(("log", {"level": "success", "msg": f"Erase complete. Device returned: {hex_dump(data)}"}))

                elif action == "Write Tracks (Interactive)":
                    self._set_led(ser, '1')
                    tracks = user_input.split('|')
                    t1 = tracks[0] if len(tracks) > 0 else ""
                    t2 = tracks[1] if len(tracks) > 1 else ""
                    t3 = tracks[2] if len(tracks) > 2 else ""

                    q.put(("log", {"level": "info", "msg": f"Issuing WRITE command (Swipe to write)..."}))
                    cmd = b"\x1b\x77" + t1.encode() + b"\x1b" + t2.encode() + b"\x1b" + t3.encode() + b"?"
                    ser.write(cmd)
                    data = ser.read(10)
                    self._set_led(ser, '2')
                    q.put(("log", {"level": "success", "msg": f"Write complete. Device returned: {hex_dump(data)}"}))

        except serial.SerialException as e:
            q.put(("log", {"level": "alert", "msg": f"Serial I/O Error: {str(e)}"}))
        except Exception as e:
            q.put(("log", {"level": "alert", "msg": f"MSR Error: {str(e)}"}))

    def _set_led(self, ser, color_code):
        try:
            ser.write(b"\x1b\x28" + color_code.encode())
        except:
            pass

    def _parse_iso_7813(self, data, q):
        t1_match = re.search(r'%(.*?\?)', data)
        t2_match = re.search(r';(.*?\?)', data)
        t3_match = re.search(r'[+!](.*?\?)', data)

        parsed = {}
        if t1_match:
            t1 = t1_match.group(1)[:-1]
            parsed['Track1'] = {"Raw": t1}
        if t2_match:
            t2 = t2_match.group(1)[:-1]
            parsed['Track2'] = {"Raw": t2}
        if t3_match:
            parsed['Track3'] = {"Raw": t3_match.group(1)[:-1]}

        q.put(("result", {"ISO_Parsing": parsed}))
        q.put(("log", {"level": "success", "msg": "ISO 7813 Parsing completed."}))


class LuhnModule(BaseModule):
    def __init__(self):
        super().__init__(
            "Luhn Generator Tool",
            "Generate Luhn check digits for PAN prefixes.",
            "Chimeric",
            "Interactive tool. Provide a PAN prefix and it will calculate the missing check digit."
        )

    def get_actions(self):
        return [("Generate Check Digit", True)]

    def run(self, action, user_input, q):
        prefix = user_input.strip()
        result = luhn_generate(prefix)
        if result:
            q.put(("result", {"Input_Prefix": prefix, "Generated_PAN": result}))
            q.put(("log", {"level": "success", "msg": "Luhn generation complete."}))
        else:
            q.put(("log", {"level": "alert", "msg": "Invalid input. PAN prefix must be numeric."}))


# --- TUI ---

XD_SKULL = """
     .ok0KXXKK0ko.
    .c0WMMMMMMMMMMW0c.
   .dWMMMMMMMMMMMMMMWd.
   oWMMMWX0kkkk0XWMMMWo
  .xMMMXc..    ..cXMMMx.
  .xMMWd.  >  <  .dWMMx.
   oWMWo.  ====  .oWMWo
   .dWMXl.      .lXMWd.
    .c0WMXxo::oxXMW0c.
      .ok0KXXKK0ko.
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
                stamp = datetime.datetime.now().strftime("%H:%M:%S")
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
                    else:
                        try:
                            self.input_text += chr(key)
                        except:
                            pass

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
