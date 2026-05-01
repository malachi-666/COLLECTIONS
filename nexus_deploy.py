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

# --- Niche Tools ---

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
    """Raw Hex/Binary Dumper for inspecting non-standard data."""
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

        # Read Value
        value = data[i:i+length]
        i += length

        # If Constructed Data Object, parse recursively
        if (tag & 0x20) == 0x20:
            parsed[tag_hex] = parse_ber_tlv(value)
        else:
            parsed[tag_hex] = toHexString(value)

    return parsed

# --- Architecture ---

class BaseModule:
    """Modular Extension Template for all hardware tools."""
    def __init__(self, name, desc, author, help_text=""):
        self.metadata = {
            "name": name,
            "description": desc,
            "author": author
        }
        self.help_text = help_text

    def validate(self):
        """Pre-run hardware check."""
        return True, "Valid"

    def get_actions(self):
        """Returns a list of tuples: (ActionName, RequiresInputFlag)"""
        return [("Run Default", False)]

    def run(self, action, user_input, q):
        """Primary asynchronous execution logic."""
        raise NotImplementedError("Modules must implement run().")

class CCIDModule(BaseModule):
    def __init__(self):
        super().__init__(
            "OmniKey PC/SC Mastery",
            "EMV Extraction, Memory Card Probing, Raw APDU.",
            "Chimeric",
            "Advanced PC/SC suite. Supports EMV flows and OmniKey Synchronous APIs."
        )

    def validate(self):
        if not readers():
            return False, "No PC/SC readers found."
        return True, "Reader available."

    def get_actions(self):
        return [
            ("EMV Data Extraction", False),
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

            if action == "EMV Data Extraction":
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
        q.put(("log", {"level": "debug", "msg": f"PPSE Select: {hex(sw1)} {hex(sw2)}"}))

        if sw1 != 0x90:
            q.put(("log", {"level": "warning", "msg": "Failed to select PPSE. Card may not be EMV."}))
            return

        tlv = parse_ber_tlv(data)
        q.put(("result", {"PPSE_TLV": tlv}))

        # 2. Extract AID (Assuming standard 4F tag inside 61 or A5 inside BF0C... brute forcing a common structure for simplicity)
        aid_hex = None
        try:
            # Simplified path extraction for typical PPSE
            fci_prop = tlv.get("6F", {}).get("A5", {})
            bf0c = fci_prop.get("BF0C", {})
            app_template = bf0c.get("61", {})
            if isinstance(app_template, dict):
                aid_hex = app_template.get("4F")
            elif isinstance(app_template, list): # if multiple apps
                pass
        except:
            pass

        # Hard fallback to common AIDs if extraction fails
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
            q.put(("log", {"level": "warning", "msg": "AID extraction failed or not 9000. Trying fallbacks..."}))
            for name, aid_bytes in common_aids.items():
                apdu_aid = [0x00, 0xA4, 0x04, 0x00, len(aid_bytes)] + aid_bytes + [0x00]
                data, sw1, sw2 = conn.transmit(apdu_aid)
                if sw1 == 0x90:
                    q.put(("log", {"level": "success", "msg": f"Fallback selected {name} AID."}))
                    aid_selected = True
                    break

        if not aid_selected:
            q.put(("log", {"level": "error", "msg": "Could not select any application AID."}))
            return

        # 3. GET PROCESSING OPTIONS
        q.put(("log", {"level": "info", "msg": "Issuing GET PROCESSING OPTIONS..."}))
        apdu_gpo = [0x80, 0xA8, 0x00, 0x00, 0x02, 0x83, 0x00, 0x00]
        data, sw1, sw2 = conn.transmit(apdu_gpo)
        if sw1 != 0x90:
            q.put(("log", {"level": "error", "msg": f"GPO Failed: {hex(sw1)} {hex(sw2)}"}))
            return

        q.put(("result", {"GPO_Response": toHexString(data)}))

        # 4. READ RECORD Brute Force (SFI 1-3, Records 1-5)
        q.put(("log", {"level": "info", "msg": "Brute forcing READ RECORD for Track 2 Equivalent Data (57) and PAN (5A)..."}))
        found_data = {}
        for sfi in range(1, 4):
            for rec in range(1, 6):
                p2 = (sfi << 3) | 0x04
                apdu_read = [0x00, 0xB2, rec, p2, 0x00]
                data, sw1, sw2 = conn.transmit(apdu_read)
                if sw1 == 0x90:
                    parsed = parse_ber_tlv(data)
                    found_data[f"SFI_{sfi}_Rec_{rec}"] = parsed
                    # Look for Track 2 (57) or PAN (5A)
                    # Note: We must search recursively in 70 template
                    template = parsed.get("70", {})
                    if "5A" in template:
                        q.put(("log", {"level": "success", "msg": f"Found PAN (5A): {template['5A']}"}))
                    if "57" in template:
                        q.put(("log", {"level": "success", "msg": f"Found Track 2 Equivalent (57): {template['57']}"}))

        if found_data:
            q.put(("result", {"Records": found_data}))

    def _memory_probing(self, conn, q):
        q.put(("log", {"level": "info", "msg": "Probing Memory Cards via OmniKey Synchronous API..."}))
        # OmniKey Synchronous APDU format: FF 20 00 00 02 <CardType> 00
        targets = {
            "SLE4442": [0xFF, 0x20, 0x00, 0x00, 0x02, 0x01, 0x00],
            "SLE4428": [0xFF, 0x20, 0x00, 0x00, 0x02, 0x02, 0x00],
            "I2C (FM24C)": [0xFF, 0x20, 0x00, 0x00, 0x02, 0x06, 0x00] # General I2C fallback type
        }
        for name, apdu in targets.items():
            try:
                data, sw1, sw2 = conn.transmit(apdu)
                if sw1 == 0x90:
                    q.put(("log", {"level": "success", "msg": f"{name} Memory Card Detected! (sw1=90)"}))
                else:
                    q.put(("log", {"level": "debug", "msg": f"{name} probe returned {hex(sw1)}{hex(sw2)}"}))
            except Exception as e:
                q.put(("log", {"level": "error", "msg": f"{name} probe failed: {e}"}))

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
            q.put(("log", {"level": "error", "msg": f"Invalid APDU format or transmission error: {e}"}))


class MSRModule(BaseModule):
    def __init__(self):
        super().__init__(
            "MSR605X Controller",
            "Bit-Level Control for MSR605X hardware.",
            "Chimeric",
            "MSR605X Serial Protocol Implementation. Supports Read, Write, Erase and Raw Listen modes."
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
            ("Raw Data Listener", False),
            ("Erase All Tracks", False),
            ("Erase T2/T3 Only", False),
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
                    data = ser.read_until(b"?\x1c") # Typical end sentinel block
                    if not data: data = ser.read(200) # fallback

                    if data:
                        self._set_led(ser, '2') # Green
                        q.put(("result", {"Raw_Hex": hex_dump(data)}))
                        try:
                            decoded = data.decode('ascii', errors='ignore')
                            self._parse_iso_7813(decoded, q)
                        except Exception as e:
                            q.put(("log", {"level": "warning", "msg": f"Failed to ASCII decode: {e}"}))
                    else:
                        self._set_led(ser, '3') # Red
                        q.put(("log", {"level": "warning", "msg": "Read timeout or empty buffer."}))

                elif action == "Raw Data Listener":
                    self._set_led(ser, '1') # Amber
                    q.put(("log", {"level": "info", "msg": "Raw mode active. Swipe card now..."}))
                    # Some MSRs use a different command for raw byte stream, or we just listen
                    ser.write(b"\x1b\x72") # Try normal read, but don't ascii decode
                    data = ser.read(500)
                    if data:
                        self._set_led(ser, '2') # Green
                        q.put(("result", {"Raw_Bitstream_Hex": hex_dump(data)}))
                    else:
                        self._set_led(ser, '3') # Red
                        q.put(("log", {"level": "warning", "msg": "No raw data received."}))

                elif action == "Erase All Tracks":
                    self._set_led(ser, '1')
                    q.put(("log", {"level": "info", "msg": "Issuing ERASE ALL (Swipe to confirm)..."}))
                    # 0x07 = 00000111 (T1, T2, T3)
                    ser.write(b"\x1b\x63\x07")
                    data = ser.read(10)
                    self._set_led(ser, '2')
                    q.put(("log", {"level": "success", "msg": f"Erase complete. Device returned: {hex_dump(data)}"}))

                elif action == "Erase T2/T3 Only":
                    self._set_led(ser, '1')
                    q.put(("log", {"level": "info", "msg": "Issuing ERASE T2/T3 (Swipe to confirm)..."}))
                    # 0x06 = 00000110 (T2, T3)
                    ser.write(b"\x1b\x63\x06")
                    data = ser.read(10)
                    self._set_led(ser, '2')
                    q.put(("log", {"level": "success", "msg": f"Erase complete. Device returned: {hex_dump(data)}"}))

                elif action == "Write Tracks (Interactive)":
                    self._set_led(ser, '1')
                    # Expecting input format: "T1|T2|T3"
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

        except Exception as e:
            q.put(("log", {"level": "error", "msg": f"Serial Error: {str(e)}"}))

    def _set_led(self, ser, color_code):
        """1: Amber, 2: Green, 3: Red"""
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
            parts = t1.split('^')
            if len(parts) >= 3:
                parsed['Track1']['Format'] = parts[0][0]
                parsed['Track1']['PAN'] = parts[0][1:]
                parsed['Track1']['Name'] = parts[1]

        if t2_match:
            t2 = t2_match.group(1)[:-1]
            parsed['Track2'] = {"Raw": t2}
            parts = t2.split('=')
            if len(parts) == 2:
                parsed['Track2']['PAN'] = parts[0]
                parsed['Track2']['Expiration'] = parts[1][:4]
                parsed['Track2']['ServiceCode'] = parts[1][4:7]

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
            q.put(("log", {"level": "error", "msg": "Invalid input. PAN prefix must be numeric."}))

# --- TUI ---

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
        curses.curs_set(0)
        self.stdscr.nodelay(True)
        self.h, self.w = self.stdscr.getmaxyx()

        # Load Modules dynamically (Normally from a directory, but hardcoded list for single file)
        self.modules = [cls() for cls in BaseModule.__subclasses__()]
        self.state = "MENU" # MENU, SUBMENU, INPUT, RUNNING
        self.menu_idx = 0
        self.sub_idx = 0
        self.input_text = ""

        self.q = queue.Queue()
        self.logs = []

        # Setup colors
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_RED, -1)
        curses.init_pair(2, curses.COLOR_CYAN, -1)
        curses.init_pair(3, curses.COLOR_GREEN, -1)
        curses.init_pair(4, curses.COLOR_YELLOW, -1)

        self.log_pad = curses.newpad(5000, self.w)
        self.log_pad_pos = 0
        self.auto_scroll = True

    def get_border(self):
        return "+" + ("~v^" * (self.w // 3))[:self.w-2] + "+"

    def draw(self):
        self.stdscr.clear()

        # Top Border
        self.stdscr.addstr(0, 0, self.get_border(), curses.color_pair(2))

        # Skull
        for i, line in enumerate(ASCII_SKULL.strip().split('\n')):
            self.stdscr.addstr(i+1, 2, line, curses.color_pair(1))

        start_y = 13

        # --- DRAW STATE: MENU ---
        if self.state in ["MENU", "SUBMENU", "INPUT"]:
            self.stdscr.addstr(start_y, 2, "MODULE SELECTION (j/k: Navigate, Enter: Select, q: Quit):", curses.color_pair(2) | curses.A_BOLD)
            for idx, mod in enumerate(self.modules):
                prefix = "[*] " if idx == self.menu_idx and self.state == "MENU" else "[ ] "
                attr = curses.A_REVERSE if idx == self.menu_idx and self.state == "MENU" else curses.A_NORMAL
                self.stdscr.addstr(start_y + 2 + idx, 4, f"{prefix}{mod.metadata['name']} - {mod.metadata['description']}", attr | curses.color_pair(2))

            # Module Help Text
            mod = self.modules[self.menu_idx]
            self.stdscr.addstr(start_y + 2 + len(self.modules) + 1, 4, f"INFO: {mod.help_text}", curses.color_pair(3))

        # --- DRAW STATE: SUBMENU ---
        if self.state == "SUBMENU":
            mod = self.modules[self.menu_idx]
            actions = mod.get_actions()
            sub_start_y = start_y + 2 + len(self.modules) + 3
            self.stdscr.addstr(sub_start_y, 2, f"ACTIONS FOR {mod.metadata['name'].upper()} (j/k: Navigate, Enter: Execute, ESC: Back):", curses.color_pair(1) | curses.A_BOLD)
            for idx, act in enumerate(actions):
                prefix = "> " if idx == self.sub_idx else "  "
                attr = curses.A_REVERSE if idx == self.sub_idx else curses.A_NORMAL
                req_in = "[Req Input]" if act[1] else ""
                self.stdscr.addstr(sub_start_y + 2 + idx, 4, f"{prefix}{act[0]} {req_in}", attr | curses.color_pair(2))

        # --- DRAW STATE: INPUT ---
        if self.state == "INPUT":
            mod = self.modules[self.menu_idx]
            act = mod.get_actions()[self.sub_idx]
            in_y = start_y + 2 + len(self.modules) + 8
            self.stdscr.addstr(in_y, 2, f"INPUT REQUIRED FOR '{act[0]}':", curses.color_pair(4) | curses.A_BOLD)
            self.stdscr.addstr(in_y + 1, 4, f"> {self.input_text}_", curses.color_pair(3))

        self.stdscr.refresh()

        # --- DRAW LOG PAD ---
        log_h = max(5, self.h // 2 - 2)
        start_log_y = self.h - log_h - 1

        # Mid Border
        self.stdscr.addstr(start_log_y - 1, 0, self.get_border(), curses.color_pair(2))
        self.stdscr.addstr(start_log_y - 1, 2, "[ FORENSIC LOG / RESULTS (PgUp/PgDn to scroll) ]", curses.color_pair(1) | curses.A_REVERSE)

        self.log_pad.clear()
        for i, lg in enumerate(self.logs):
            cp = curses.color_pair(3)
            if "[ERROR]" in lg: cp = curses.color_pair(1)
            elif "[WARNING]" in lg: cp = curses.color_pair(4)
            elif "[RESULT]" in lg: cp = curses.color_pair(2) | curses.A_BOLD
            self.log_pad.addstr(i, 0, lg[:self.w-1], cp)

        max_scroll = max(0, len(self.logs) - log_h)
        if self.auto_scroll:
            self.log_pad_pos = max_scroll

        # Ensure bounds
        if self.log_pad_pos < 0: self.log_pad_pos = 0
        if self.log_pad_pos > max_scroll: self.log_pad_pos = max_scroll

        try:
            self.log_pad.refresh(self.log_pad_pos, 0, start_log_y, 1, self.h-1, self.w-1)
        except curses.error:
            pass # Ignore resize bounds temporarily

    def process_queue(self):
        dirty = False
        while not self.q.empty():
            try:
                msg_type, payload = self.q.get_nowait()
                stamp = datetime.datetime.now().strftime('%H:%M:%S')
                if msg_type == 'log':
                    self.logs.append(f'[{stamp}] [{payload["level"].upper()}] {payload["msg"]}')
                    dirty = True
                elif msg_type == 'result':
                    res_str = json.dumps(payload, indent=2)
                    for rline in res_str.split('\n'):
                        for chunk in [rline[i:i+self.w-20] for i in range(0, max(1, len(rline)), self.w-20)]:
                            self.logs.append(f'[{stamp}] [RESULT] {chunk}')
                    dirty = True
                elif msg_type == 'done':
                    self.state = 'SUBMENU'
                    dirty = True
            except Exception:
                pass
        if len(self.logs) > 4000:
            self.logs = self.logs[-4000:]
        return dirty
    def run(self):
        self.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [INFO] Chimeric OS Hardware Control Suite Online.")
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
                    elif key in [10, 13]: # Enter
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
                    elif key == 27: # ESC
                        self.state = "MENU"
                    elif key in [10, 13]: # Enter
                        act = actions[self.sub_idx]
                        if act[1]: # Requires Input
                            self.state = "INPUT"
                            self.input_text = ""
                            curses.curs_set(1)
                        else:
                            self._launch_module(mod, act[0], "")

                # State: INPUT
                elif self.state == "INPUT":
                    if key == 27: # ESC
                        self.state = "SUBMENU"
                        curses.curs_set(0)
                    elif key in [10, 13]: # Enter
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
            self.q.put(("log", {"level": "info", "msg": f"Initiating {action_name}..."}))
            def worker():
                mod.run(action_name, user_input, self.q)
                self.q.put(("done", None))
            threading.Thread(target=worker, daemon=True).start()
        else:
            self.q.put(("log", {"level": "error", "msg": f"Validation failed: {msg}"}))
            self.state = "SUBMENU"

if __name__ == "__main__":
    try:
        curses.wrapper(lambda stdscr: TUI(stdscr).run())
    except KeyboardInterrupt:
        print("\n[!] Exiting Chimeric OS Hardware Control Suite.")
