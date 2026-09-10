import threading
from typing import Callable, Optional
from cina.ipc_service import IPCServer

class HotkeyService:
    def __init__(self, hotkey_str: str, on_trigger: Callable[[], None]):
        self.hotkey_str = hotkey_str
        self.on_trigger = on_trigger
        self.ipc_server = IPCServer(on_trigger=self.on_trigger)
        self._listener = None
        self._running = False

    def start(self):
        """Inicia el servidor IPC y el escuchador de teclas global si el entorno lo soporta."""
        self._running = True
        # 1. Siempre iniciar el servidor IPC (permite atajos directos de KDE/Wayland/CLI)
        self.ipc_server.start()

        # 2. Intentar registrar el hotkey nativo con pynput
        try:
            from pynput import keyboard

            # Formatear el string para pynput (ej: <ctrl>+<alt>+s)
            formatted_hotkey = self.hotkey_str.strip().lower()
            if not formatted_hotkey.startswith("<"):
                # Conversión básica si el usuario ingresó "ctrl+alt+s"
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
            print(f"[Hotkey] Nota sobre Wayland: El escuchador de teclado directo no está permitido por el compositor ({e}). Se usará el atajo nativo de KDE Plasma mediante IPC.")

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

