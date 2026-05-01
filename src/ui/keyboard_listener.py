import sys
import threading

def getch():
    """Cross-platform non-blocking key reader."""
    try:
        import tty
        import termios
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch
    except ImportError:
        import msvcrt
        return msvcrt.getch().decode('utf-8')

class KeyboardListener:
    def __init__(self, callback):
        self.callback = callback
        self._thread = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _listen(self):
        while self._running:
            ch = getch()
            if ch:
                self.callback(ch.lower())
