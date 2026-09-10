#!/usr/bin/env python3
import os
import sys
import argparse
import tkinter as tk

# Asegurar que el directorio raíz esté en sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cina.config import config_mgr
from cina.ipc_service import IPCClient
from cina.capture import ScreenCapture
from cina.gemini_service import GeminiService
from cina.tts_service import TTSService
from cina.hotkey_service import HotkeyService

def run_oneshot():
    """Ejecuta una captura y respuesta directa sin interfaz gráfica (modo standalone / trigger)."""
    print("[Cina] Ejecutando captura silenciosa...")
    cap = ScreenCapture(preferred_backend=config_mgr.get("capture_backend", "auto"))
    gemini = GeminiService(
        api_key=config_mgr.get("api_key", ""),
        model=config_mgr.get("model", "gemini-2.5-flash")
    )
    tts = TTSService(
        voice=config_mgr.get("voice", "es-ES-AlvaroNeural"),
        rate=config_mgr.get("speech_rate", "+0%"),
        volume=config_mgr.get("speech_volume", "+0%")
    )

    raw_path = cap.capture_silent()
    if not raw_path:
        print("[Cina] Error: No se pudo capturar la pantalla.")
        tts._speak_espeak("Error: no se pudo capturar la pantalla.")
        return

    opt_path = cap.optimize_image(raw_path)
    print(f"[Cina] Captura lista: {opt_path}. Consultando Gemini...")

    success, response_text, elapsed = gemini.analyze_image(opt_path)
    print(f"[Cina] Respuesta obtenida en {elapsed:.2f}s:\n{response_text}")

    # Limpieza
    for p in [raw_path, opt_path]:
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    if success:
        print("[Cina] Reproduciendo audio de respuesta...")
        tts._speak_worker(response_text)
    else:
        print(f"[Cina] Error: {response_text}")
        tts._speak_worker(f"Error de Gemini: {response_text}")

def handle_trigger():
    """Envía la señal a la app en segundo plano o ejecuta captura única."""
    if IPCClient.is_server_running():
        print("[Cina] Notificando a la aplicación activa en segundo plano...")
        if IPCClient.send_trigger():
            print("[Cina] Disparo enviado con éxito.")
            return
        else:
            print("[Cina] No se pudo enviar el comando vía socket. Iniciando modo autónomo...")

    # Si la app no está abierta en segundo plano, ejecutar standalone directamente
    run_oneshot()

def run_headless():
    """Ejecuta el demonio en segundo plano sin ventana gráfica."""
    import time
    print("[Cina] Iniciando en modo demonio (Headless)... Presiona Ctrl+C para salir.")
    hotkey = HotkeyService(
        hotkey_str=config_mgr.get("hotkey", "<ctrl>+<alt>+s"),
        on_trigger=run_oneshot
    )
    hotkey.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[Cina] Deteniendo demonio...")
        hotkey.stop()

def install_shortcut():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    trigger_script = os.path.join(base_dir, "cina-trigger")
    run_script = os.path.join(base_dir, "run.sh")
    desktop_dir = os.path.expanduser("~/.local/share/applications")
    bin_dir = os.path.expanduser("~/.local/bin")
    os.makedirs(desktop_dir, exist_ok=True)
    os.makedirs(bin_dir, exist_ok=True)

    # Symlink a ~/.local/bin para evitar problemas con espacios en rutas
    link_trigger = os.path.join(bin_dir, "cina-trigger")
    link_run = os.path.join(bin_dir, "cina")
    try:
        if os.path.lexists(link_trigger):
            os.remove(link_trigger)
        os.symlink(trigger_script, link_trigger)
        if os.path.lexists(link_run):
            os.remove(link_run)
        os.symlink(run_script, link_run)
    except Exception as e:
        print(f"[Cina] Advertencia al crear symlinks: {e}")

    desktop_file = os.path.join(desktop_dir, "cina-trigger.desktop")
    content = f"""[Desktop Entry]
Name=CINA Screen Trigger
Comment=Disparador silencioso de captura y respuesta por voz CINA
Exec={link_trigger}
Icon=camera-photo
Type=Application
Terminal=false
NoDisplay=false
X-KDE-Shortcuts=Ctrl+Alt+S
"""
    with open(desktop_file, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(desktop_file, 0o755)
    print(f"[Cina] Lanzador de atajo instalado en: {desktop_file}")
    print("[Cina] En KDE -> Atajos de teclado puedes asignar la orden simple: cina-trigger")

def main():
    parser = argparse.ArgumentParser(description="Cina - Asistente de Pantalla y Audio con IA")
    parser.add_argument("--trigger", action="store_true", help="Disparar captura instantánea")
    parser.add_argument("--headless", action="store_true", help="Ejecutar en segundo plano sin ventana GUI")
    parser.add_argument("--install-shortcut", action="store_true", help="Instalar lanzador de atajo para KDE")

    args, _ = parser.parse_known_args()

    if args.trigger:
        handle_trigger()
        return

    if args.install_shortcut:
        install_shortcut()
        return

    if args.headless:
        run_headless()
        return

    # Iniciar GUI
    from cina.gui import CinaApp
    root = tk.Tk()
    app = CinaApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()

