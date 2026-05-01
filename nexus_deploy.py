# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyscard",
#     "pyserial",
# ]
# ///

import curses
import serial
from smartcard.System import readers
from smartcard.util import toHexString
from smartcard.Exceptions import NoCardException, CardConnectionException

def ccid_audit():
    """
    Connects to the reader, performs an ATR (Answer to Reset), and sends a
    standard Select PPSE APDU.
    """
    try:
        r = readers()
        if not r:
            return {"status": "error", "message": "No smart card readers found"}

        reader = r[0]
        connection = reader.createConnection()
        connection.connect()

        atr = connection.getATR()
        atr_hex = toHexString(atr)

        # Select PPSE APDU: 00 A4 04 00 0E 32 50 41 59 2E 53 59 53 2E 44 44 46 30 31 00
        apdu = [0x00, 0xA4, 0x04, 0x00, 0x0E, 0x32, 0x50, 0x41, 0x59, 0x2E, 0x53, 0x59, 0x53, 0x2E, 0x44, 0x44, 0x46, 0x30, 0x31, 0x00]
        data, sw1, sw2 = connection.transmit(apdu)

        return {
            "status": "success",
            "atr": atr_hex,
            "sw1": hex(sw1),
            "sw2": hex(sw2),
            "data": toHexString(data)
        }
    except NoCardException:
        return {"status": "error", "message": "No card inserted"}
    except CardConnectionException as e:
        return {"status": "error", "message": f"Card connection error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"CCID error: {e}"}

def magstripe_audit():
    """
    Communicates with a device on /dev/ttyUSB0 and modifies the 'Service Code'
    field within a standard Track 2 data string to '101'.
    """
    try:
        # We use a short timeout as this is a diagnostic tool
        ser = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)
        ser.write(b"READ_TRACK2\n") # Pseudo-command to trigger a read if needed
        data = ser.readline().decode('utf-8').strip()
        ser.close()

        if not data:
            return {"status": "error", "message": "No data received from serial device"}

        # Standard Track 2 format: ;PAN=YYMMSSCDDDDDDD? (simplified)
        # Assuming PAN is up to 19 digits, followed by '=' separator,
        # then YY (Expiration Year), MM (Expiration Month), SSC (Service Code), D... (Discretionary Data)

        if '=' in data:
            parts = data.split('=')
            pan = parts[0]
            if pan.startswith(';'):
                pan = pan[1:] # remove start sentinel

            rest = parts[1]
            if len(rest) >= 7:
                exp_date = rest[0:4]
                service_code = rest[4:7]
                discretionary = rest[7:]

                # Modify Service Code to '101'
                new_service_code = '101'
                modified_data = f";{pan}={exp_date}{new_service_code}{discretionary}"
                if not modified_data.endswith('?'):
                    modified_data += '?'

                return {
                    "status": "success",
                    "original_data": data,
                    "modified_data": modified_data,
                    "service_code": service_code,
                    "new_service_code": new_service_code
                }

        return {"status": "warning", "message": "Data received but track 2 format not recognized", "data": data}

    except serial.SerialException as e:
        return {"status": "error", "message": f"Serial port error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Magstripe error: {e}"}


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
         ......
"""

def draw_skull(stdscr, y, x, color_pair):
    lines = ASCII_SKULL.strip("\n").split("\n")
    for i, line in enumerate(lines):
        try:
            stdscr.addstr(y + i, x, line, color_pair)
        except curses.error:
            pass
    return len(lines)

def draw_menu(stdscr, current_row, menu_items):
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    # Draw title and skull
    title = "CHIMERIC OS - HARDWARE DIAGNOSTIC UTILITY"
    try:
        stdscr.addstr(1, w//2 - len(title)//2, title, curses.color_pair(1) | curses.A_BOLD)
    except curses.error:
        pass

    skull_lines = draw_skull(stdscr, 3, w//2 - 15, curses.color_pair(3) | curses.A_BOLD)

    start_y = 3 + skull_lines + 2

    for idx, item in enumerate(menu_items):
        x = w//2 - len(item)//2
        y = start_y + idx
        if idx == current_row:
            try:
                stdscr.addstr(y, x, item, curses.color_pair(2) | curses.A_REVERSE)
            except curses.error:
                pass
        else:
            try:
                stdscr.addstr(y, x, item, curses.color_pair(3))
            except curses.error:
                pass

    stdscr.refresh()

def run_tui(stdscr):
    curses.curs_set(0)

    # Initialize colors
    curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)

    menu_items = ['1. CCID Smart Card Audit', '2. Magstripe Serial Audit', '3. Verification Audit', '4. Exit']
    current_row = 0

    draw_menu(stdscr, current_row, menu_items)

    while True:
        key = stdscr.getch()

        if key == curses.KEY_UP and current_row > 0:
            current_row -= 1
        elif key == curses.KEY_DOWN and current_row < len(menu_items) - 1:
            current_row += 1
        elif key == curses.KEY_ENTER or key in [10, 13]:
            # Enter pressed
            stdscr.clear()
            h, w = stdscr.getmaxyx()

            if current_row == 0:
                # CCID Audit
                stdscr.addstr(1, 2, "Running CCID Audit...", curses.color_pair(2))
                stdscr.refresh()
                result = ccid_audit()

                y = 3
                for k, v in result.items():
                    try:
                        stdscr.addstr(y, 2, f"{k}: {v}", curses.color_pair(3))
                    except curses.error:
                        pass
                    y += 1

            elif current_row == 1:
                # Magstripe Audit
                stdscr.addstr(1, 2, "Running Magstripe Serial Audit...", curses.color_pair(2))
                stdscr.refresh()
                result = magstripe_audit()

                y = 3
                for k, v in result.items():
                    try:
                        stdscr.addstr(y, 2, f"{k}: {v}", curses.color_pair(3))
                    except curses.error:
                        pass
                    y += 1

            elif current_row == 2:
                # Verification Audit
                stdscr.addstr(1, 2, "Running Verification Audit...", curses.color_pair(2))
                stdscr.refresh()
                result_ccid = ccid_audit()
                result_mag = magstripe_audit()

                y = 3
                stdscr.addstr(y, 2, "--- CCID Data ---", curses.color_pair(1))
                y += 1
                for k, v in result_ccid.items():
                    if k in ['status', 'message', 'data']:
                        try:
                            stdscr.addstr(y, 2, f"{k}: {v}", curses.color_pair(3))
                        except curses.error:
                            pass
                        y += 1
                y += 1

                stdscr.addstr(y, 2, "--- Magstripe Data ---", curses.color_pair(1))
                y += 1
                for k, v in result_mag.items():
                    if k in ['status', 'message', 'original_data', 'modified_data']:
                        try:
                            stdscr.addstr(y, 2, f"{k}: {v}", curses.color_pair(3))
                        except curses.error:
                            pass
                        y += 1

            elif current_row == 3:
                # Exit
                break

            try:
                stdscr.addstr(h-2, 2, "Press any key to return to menu...", curses.color_pair(2))
            except curses.error:
                pass
            stdscr.refresh()
            stdscr.getch()

        draw_menu(stdscr, current_row, menu_items)

if __name__ == '__main__':
    curses.wrapper(run_tui)
