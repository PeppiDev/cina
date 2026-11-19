import os
import threading
from typing import Callable, Optional
from cina.ipc_service import IPCServer


class HotkeyService:
    def __init__(
        self,
        hotkey_str: str,
        on_trigger: Callable[[], None],
        on_stop: Optional[Callable[[], None]] = None,
    ):
        self.hotkey_str = hotkey_str
        self.on_trigger = on_trigger
        self.on_stop = on_stop
        self.ipc_server = IPCServer(on_trigger=self.on_trigger, on_stop=self.on_stop)
        self._listener = None
        self._running = False

    @staticmethod
    def _is_pure_wayland_without_x11() -> bool:
        session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        is_wayland = "wayland" in session_type or bool(os.environ.get("WAYLAND_DISPLAY"))
        return is_wayland and not os.environ.get("DISPLAY")

    def start(self):
        """Inicia el servidor IPC y el escuchador de teclas global si el entorno lo soporta."""
        self._running = True
        # 1. Siempre iniciar el servidor IPC (permite atajos directos de Niri/KDE/Hyprland/Sway/CLI)
        self.ipc_server.start()

        # 2. En sesiones Wayland puras sin X11/xwayland-satellite (ej. Niri puro), usar únicamente IPC
        if self._is_pure_wayland_without_x11():
            print("[Hotkey] Sesión Wayland pura detectada (sin X11): usando servidor IPC para 'cina-trigger'.")
            return

        # 3. Intentar registrar el hotkey nativo con pynput (X11)
        try:
            from pynput import keyboard

            formatted_hotkey = self.hotkey_str.strip().lower()
            if not formatted_hotkey.startswith("<"):
                parts = formatted_hotkey.split("+")
                formatted_hotkey = "+".join([f"<{p}>" if len(p) > 1 else p for p in parts])

            print(f"[Hotkey] Registrando atajo de teclado: {formatted_hotkey}")

            hotkeys_dict = {
                formatted_hotkey: self._on_hotkey_pressed
            }

            self._listener = keyboard.GlobalHotKeys(hotkeys_dict)
            self._listener.start()
            print("[Hotkey] Escuchador de teclado en segundo plano activado con éxito.")
        except Exception as e:
            print(f"[Hotkey] Nota sobre Wayland: El escuchador de teclado directo no está permitido por el compositor ({e}). Usa el atajo del compositor (Niri, KDE, Hyprland o Sway) apuntando a 'cina-trigger' mediante IPC.")

    def _on_hotkey_pressed(self):
        print("[Hotkey] ¡Atajo de teclado detectado!")
        if self.on_trigger:
            threading.Thread(target=self.on_trigger, daemon=True).start()

    def stop(self):
        self._running = False
        self.ipc_server.stop()
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
