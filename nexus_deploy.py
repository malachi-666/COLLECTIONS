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
from smartcard.util import toHexString, toBytes
import json
import logging
import datetime
import threading
import queue
import time
import sys
import re

# --- Niche Tools ---

def luhn_generate(pan_prefix):
    """Luhn Check Digit Generator: Calculates missing digit of a PAN."""
    if not pan_prefix.isdigit(): return None
    digits = [int(d) for d in str(pan_prefix)]
    # Multiply odd-positioned digits from the right by 2
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

# --- Architecture ---

class BaseModule:
    """Modular Extension Template for all hardware tools."""
    def __init__(self, name, desc, author):
        self.metadata = {
            "name": name,
            "description": desc,
            "author": author
        }

    def validate(self):
        """Pre-run hardware check."""
        return True, "Valid"

    def run(self, q):
        """Primary asynchronous execution logic."""
        raise NotImplementedError("Modules must implement run().")

class CCIDModule(BaseModule):
    def __init__(self):
        super().__init__("OmniKey PC/SC Mastery", "Raw APDU, Memory Card Probing, EMV Extraction.", "Chimeric")

    def validate(self):
        if not readers():
            return False, "No PC/SC readers found."
        return True, "Reader available."

    def run(self, q):
        try:
            r = readers()[0]
            q.put(("log", {"level": "info", "msg": f"Connecting to {r}"}))
            conn = r.createConnection()
            conn.connect()
            atr = conn.getATR()
            q.put(("result", {"ATR": toHexString(atr)}))

            # --- EMV Data Extraction ---
            q.put(("log", {"level": "info", "msg": "Attempting EMV Extraction..."}))
            # Select PPSE
            apdu_ppse = [0x00, 0xA4, 0x04, 0x00, 0x0E, 0x32, 0x50, 0x41, 0x59, 0x2E, 0x53, 0x59, 0x53, 0x2E, 0x44, 0x44, 0x46, 0x30, 0x31, 0x00]
            data, sw1, sw2 = conn.transmit(apdu_ppse)
            q.put(("log", {"level": "debug", "msg": f"PPSE Select: {hex(sw1)} {hex(sw2)}"}))

            if sw1 == 0x90:
                # Basic EMV extraction logic (simplified for demonstration)
                # In a real scenario, we'd parse the FCI template to find the AID, then select it.
                q.put(("log", {"level": "success", "msg": "PPSE Selected. (Full EMV parsing requires specific AID selection)"}))

            # --- Memory Card Probing (OmniKey Synchronous API) ---
            q.put(("log", {"level": "info", "msg": "Probing for SLE4442/4428 Memory Cards..."}))
            # OmniKey specific APDU for synchronous card connection: FF 20 00 00 02 <CardType> 00
            # SLE4442 = 0x01
            apdu_sync = [0xFF, 0x20, 0x00, 0x00, 0x02, 0x01, 0x00]
            try:
                s_data, s_sw1, s_sw2 = conn.transmit(apdu_sync)
                if s_sw1 == 0x90:
                    q.put(("log", {"level": "success", "msg": "SLE4442 Memory Card Detected."}))
                else:
                    q.put(("log", {"level": "warning", "msg": "No SLE4442 detected or command rejected."}))
            except Exception:
                pass

            # --- Raw APDU Terminal (Simulated automated ping) ---
            q.put(("log", {"level": "info", "msg": "Sending ping APDU (00 84 00 00 08 - Get Challenge)..."}))
            apdu_ping = [0x00, 0x84, 0x00, 0x00, 0x08]
            data, sw1, sw2 = conn.transmit(apdu_ping)
            sw_code = f"{sw1:02X}{sw2:02X}"
            desc = ISO_7816_ERRORS.get(sw_code, "Unknown status code.")
            q.put(("result", {"APDU_Response": toHexString(data), "Status": f"{sw_code} ({desc})"}))

        except Exception as e:
            q.put(("log", {"level": "error", "msg": f"CCID Error: {str(e)}"}))


class MSRModule(BaseModule):
    def __init__(self):
        super().__init__("MSR605X Controller", "Read/Write/Erase & ISO 7813 Track Parsing.", "Chimeric")

    def validate(self):
        ports = serial.tools.list_ports.comports()
        if not ports:
            return False, "No serial ports found."
        self.port = ports[0].device
        return True, f"Port {self.port} available."

    def run(self, q):
        try:
            q.put(("log", {"level": "info", "msg": f"Opening {self.port} at 9600 8N1"}))
            with serial.Serial(self.port, 9600, timeout=2) as ser:

                # --- Set LED Colors (\x1b\x28) ---
                q.put(("log", {"level": "info", "msg": "Setting MSR LED to Green..."}))
                # Typical MSR605X LED command (e.g., ESC ( <color> ). Green = '2'
                ser.write(b"\x1b\x28\x32")

                # --- Read All Tracks (\x1b\x72) ---
                q.put(("log", {"level": "info", "msg": "Issuing READ ALL command..."}))
                ser.write(b"\x1b\x72")
                data = ser.read(200)

                if data:
                    q.put(("result", {"Raw_Hex": hex_dump(data)}))
                    try:
                        decoded = data.decode('ascii', errors='ignore')
                        self._parse_iso_7813(decoded, q)
                    except Exception as e:
                        q.put(("log", {"level": "warning", "msg": f"Failed to ASCII decode: {e}"}))
                else:
                    q.put(("log", {"level": "warning", "msg": "Read timeout or empty buffer."}))

                # Note: Write (\x1b\x77) and Erase (\x1b\x63) commands are implemented
                # as methods below but not actively fired in the default diagnostic run
                # to prevent accidental data destruction.

        except Exception as e:
            q.put(("log", {"level": "error", "msg": f"Serial Error: {str(e)}"}))

    def _parse_iso_7813(self, data, q):
        """Parses Tracks 1, 2, and 3 according to ISO/IEC 7813."""
        # T1 starts with %, T2 with ;, T3 with + or !
        t1_match = re.search(r'%(.*?\?)', data)
        t2_match = re.search(r';(.*?\?)', data)
        t3_match = re.search(r'[+!](.*?\?)', data)

        parsed = {}
        if t1_match:
            t1 = t1_match.group(1)[:-1] # Remove sentinel
            parsed['Track1'] = {"Raw": t1}
            # Format B: %B[PAN]^[Name]^[ExpYear][ExpMonth][ServiceCode][Discretionary]?
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

    def execute_write(self, ser, t1, t2, t3):
        """Write All Tracks (\x1b\x77)"""
        # Format: ESC w <T1> ESC <T2> ESC <T3> ?
        cmd = b"\x1b\x77" + t1.encode() + b"\x1b" + t2.encode() + b"\x1b" + t3.encode() + b"?"
        ser.write(cmd)

    def execute_erase(self, ser):
        """Erase All Tracks (\x1b\x63)"""
        # Erase specific tracks or all depending on mask
        ser.write(b"\x1b\x63\x07") # 07 = all three tracks

class LuhnModule(BaseModule):
    def __init__(self):
        super().__init__("Luhn Generator Tool", "Generate Luhn check digits for PAN prefixes.", "Chimeric")

    def run(self, q):
        # Simulated input for demonstration
        prefix = "400000000000000"
        result = luhn_generate(prefix)
        q.put(("result", {"Input": prefix, "Generated PAN": result}))
        q.put(("log", {"level": "success", "msg": "Luhn generation complete."}))

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

        # Dynamic module loading list
        self.modules = [CCIDModule(), MSRModule(), LuhnModule()]
        self.current_row = 0
        self.q = queue.Queue()
        self.logs = []

        # Setup colors
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_RED, -1)
        curses.init_pair(2, curses.COLOR_CYAN, -1)
        curses.init_pair(3, curses.COLOR_GREEN, -1)

        # Pad for asynchronous scrolling logs
        self.log_pad = curses.newpad(2000, self.w)
        self.log_pad_pos = 0

    def draw(self):
        self.stdscr.clear()

        # High-density ASCII oscilloscope borders
        top_border = "+" + ("~v^" * (self.w // 3))[:self.w-2] + "+"
        self.stdscr.addstr(0, 0, top_border, curses.color_pair(2))

        for i, line in enumerate(ASCII_SKULL.strip().split('\n')):
            self.stdscr.addstr(i+1, 2, line, curses.color_pair(1))

        # Menu with dynamic modules
        start_y = 13
        self.stdscr.addstr(start_y, 2, "MODULE SELECTION (j/k to navigate, Enter to run, q to quit):", curses.color_pair(2) | curses.A_BOLD)
        for idx, mod in enumerate(self.modules):
            prefix = "[*] " if idx == self.current_row else "[ ] "
            attr = curses.A_REVERSE if idx == self.current_row else curses.A_NORMAL
            self.stdscr.addstr(start_y + 2 + idx, 4, f"{prefix}{mod.metadata['name']} - {mod.metadata['description']}", attr | curses.color_pair(2))

        self.stdscr.refresh()

        # Draw Async Log Pad
        log_h = max(5, self.h // 2)
        start_log_y = self.h - log_h
        mid_border = "+" + ("-" * (self.w-2)) + "+"
        self.stdscr.addstr(start_log_y - 1, 0, mid_border, curses.color_pair(2))

        # Calculate scroll position
        max_scroll = max(0, len(self.logs) - log_h + 1)
        self.log_pad_pos = max_scroll

        self.log_pad.clear()
        for i, lg in enumerate(self.logs):
            self.log_pad.addstr(i, 0, lg[:self.w-1], curses.color_pair(3))
        self.log_pad.refresh(self.log_pad_pos, 0, start_log_y, 1, self.h-1, self.w-1)

    def process_queue(self):
        dirty = False
        while not self.q.empty():
            try:
                msg_type, payload = self.q.get_nowait()
                stamp = datetime.datetime.now().strftime("%H:%M:%S")
                if msg_type == "log":
                    self.logs.append(f"[{stamp}] [{payload['level'].upper()}] {payload['msg']}")
                    dirty = True
                elif msg_type == "result":
                    res_str = json.dumps(payload)
                    # Word wrap long results
                    for chunk in [res_str[i:i+self.w-20] for i in range(0, len(res_str), self.w-20)]:
                        self.logs.append(f"[{stamp}] [RESULT] {chunk}")
                    dirty = True
            except Exception:
                pass
        return dirty

    def run(self):
        self.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [INFO] Chimeric OS Hardware Control initialized.")
        self.draw()

        while True:
            dirty = self.process_queue()
            try:
                key = self.stdscr.getch()
            except curses.error:
                key = -1

            if key != -1:
                dirty = True
                # Vim j/k integration alongside arrow keys
                if key in [curses.KEY_UP, ord('k')] and self.current_row > 0:
                    self.current_row -= 1
                elif key in [curses.KEY_DOWN, ord('j')] and self.current_row < len(self.modules) - 1:
                    self.current_row += 1
                elif key in [10, 13]: # Enter
                    mod = self.modules[self.current_row]
                    valid, msg = mod.validate()
                    if valid:
                        self.q.put(("log", {"level": "info", "msg": f"Initiating {mod.metadata['name']}..."}))
                        threading.Thread(target=mod.run, args=(self.q,), daemon=True).start()
                    else:
                        self.q.put(("log", {"level": "error", "msg": f"Validation failed: {msg}"}))
                elif key == ord('q'):
                    break
                elif key == curses.KEY_RESIZE:
                    self.h, self.w = self.stdscr.getmaxyx()
                    self.log_pad = curses.newpad(2000, self.w)
                    self.stdscr.clear()

            if dirty:
                self.draw()
            time.sleep(0.05)

if __name__ == "__main__":
    try:
        curses.wrapper(lambda stdscr: TUI(stdscr).run())
    except KeyboardInterrupt:
        print("\n[!] Exiting hardware utility.")
