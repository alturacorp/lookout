"""Only one copy of the app at a time. A second launch pokes the first one to come to the front, then exits."""
import socket
import threading

PORT = 47653                      # localhost only
MAGIC = b"ROBLOX_TRACKER_SHOW"
ACK = b"ROBLOX_TRACKER_OK"          # only a real copy of this app answers with this


class SingleInstance:
    def __init__(self, port=PORT):
        self.port, self.sock, self._closing = port, None, False

    def acquire(self):
        """True if we are the only copy. False if another copy is running (it has been asked to show itself)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", self.port))
            s.listen(2)
        except OSError:
            s.close()
            return not self._poke_existing()
        self.sock = s
        return True

    def _poke_existing(self):
        """True if the port is held by another copy of this app (and it was told to show itself)."""
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1.5) as c:
                c.sendall(MAGIC)
                return c.recv(64) == ACK
        except OSError:
            return False          # port is taken by something else: don't block startup over it

    def listen(self, on_show):
        """Call on_show() (from a background thread) whenever a second launch pokes us."""
        if not self.sock:
            return

        def loop():
            while True:
                try:
                    conn, _ = self.sock.accept()
                except OSError:
                    return        # socket closed on release()
                if self._closing:
                    conn.close()
                    return
                with conn:
                    try:
                        conn.settimeout(1.0)
                        if conn.recv(64).startswith(MAGIC):
                            conn.sendall(ACK)
                            on_show()
                    except OSError:
                        pass
        threading.Thread(target=loop, daemon=True).start()

    def release(self):
        if self.sock:
            self._closing = True
            try:                                   # wake the listener thread: closing a socket another thread is
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()   # blocked on doesn't free it everywhere
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
