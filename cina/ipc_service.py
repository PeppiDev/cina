import os
import socket
import threading
from typing import Callable, Optional


def get_socket_path() -> str:
    uid = os.getuid()
    return f"/tmp/cina_{uid}.sock"


class IPCServer:
    def __init__(
        self,
        on_trigger: Callable[[], None],
        on_stop: Optional[Callable[[], None]] = None,
    ):
        self.on_trigger = on_trigger
        self.on_stop = on_stop
        self.socket_path = get_socket_path()
        self.running = False
        self._server_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Inicia el servidor IPC en un hilo en segundo plano."""
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(self.socket_path)
        self._server_sock.listen(8)
        os.chmod(self.socket_path, 0o600)

        self.running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        print(f"[IPC] Servidor escuchando en {self.socket_path}")

    def _listen_loop(self):
        while self.running:
            try:
                conn, _ = self._server_sock.accept()
                with conn:
                    conn.settimeout(1.0)
                    data = conn.recv(1024).decode("utf-8", errors="ignore").strip().upper()
                    if data == "TRIGGER":
                        conn.sendall(b"OK\n")
                        if self.on_trigger:
                            threading.Thread(target=self.on_trigger, daemon=True).start()
                    elif data == "PING":
                        conn.sendall(b"PONG\n")
                    elif data == "STOP":
                        conn.sendall(b"STOPPED\n")
                        if self.on_stop:
                            threading.Thread(target=self.on_stop, daemon=True).start()
            except OSError:
                if not self.running:
                    break
            except Exception as e:
                if not self.running:
                    break
                print(f"[IPC] Error en conexión: {e}")

    def stop(self):
        self.running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass


class IPCClient:
    @staticmethod
    def _send_command(cmd: str, expected_prefix: str, timeout: float = 0.8) -> bool:
        socket_path = get_socket_path()
        if not os.path.exists(socket_path):
            return False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(socket_path)
                s.sendall(f"{cmd}\n".encode("utf-8"))
                resp = s.recv(1024).decode("utf-8", errors="ignore").strip()
                return resp == expected_prefix
        except Exception:
            return False

    @staticmethod
    def is_server_running() -> bool:
        return IPCClient._send_command("PING", "PONG", timeout=0.5)

    @staticmethod
    def send_trigger() -> bool:
        return IPCClient._send_command("TRIGGER", "OK", timeout=0.8)

    @staticmethod
    def send_stop() -> bool:
        return IPCClient._send_command("STOP", "STOPPED", timeout=0.8)
