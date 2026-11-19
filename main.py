#!/usr/bin/env python3
import argparse
import os
import sys
import threading
import time

# Asegurar que el directorio raíz esté en sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _create_services():
    from cina.config import config_mgr
    from cina.capture import ScreenCapture
    from cina.gemini_service import GeminiService
    from cina.tts_service import TTSService

    cap = ScreenCapture(preferred_backend=config_mgr.get("capture_backend", "auto"))
    gemini = GeminiService(
        api_key=config_mgr.get("api_key", ""),
        model=config_mgr.get("model", "gemini-3.8-flash"),
        thinking_level=config_mgr.get("thinking_level", "low"),
        media_resolution=config_mgr.get("media_resolution", "high"),
        timeout=config_mgr.get("request_timeout", 15.0),
    )
    tts = TTSService(
        voice=config_mgr.get("voice", "es-ES-AlvaroNeural"),
        rate=config_mgr.get("speech_rate", "+0%"),
        volume=config_mgr.get("speech_volume", "+0%"),
        engine=config_mgr.get("tts_engine", "edge-tts"),
        audio_player=config_mgr.get("audio_player", "auto"),
        tts_timeout=config_mgr.get("tts_timeout", 5.0),
        piper_model=config_mgr.get("piper_model", ""),
    )
    return config_mgr, cap, gemini, tts


def _execute_pipeline(config_mgr, cap, gemini, tts, cancel_event: threading.Event, wait_audio: bool = True):
    """Ejecuta el pipeline completo en memoria: Captura -> Gemini Streaming -> TTS Streaming."""
    t_start = time.perf_counter()
    print("[Cina] Capturando pantalla en memoria...")

    captured = cap.capture_to_jpeg_bytes(
        max_width=config_mgr.get("image_max_width", 1600),
        quality=config_mgr.get("image_quality", 82),
        make_thumbnail=False,
    )
    if not captured:
        print("[Cina] Error: No se pudo capturar la pantalla.")
        tts._speak_worker("Error: no se pudo capturar la pantalla.")
        return

    jpeg_bytes, _ = captured
    t_cap = time.perf_counter() - t_start
    print(f"[Cina] Captura lista ({len(jpeg_bytes) // 1024} KB en {t_cap:.2f}s). Consultando Gemini...")

    stream_audio = bool(config_mgr.get("stream_audio", True))
    speech_session = None

    if stream_audio:
        def _on_first_audio():
            ttfa = time.perf_counter() - t_start
            print(f"[Cina] Primer audio emitido en {ttfa:.2f}s desde el disparo.")

        speech_session = tts.start_stream(on_first_audio=_on_first_audio)

        def _on_chunk(delta: str):
            if speech_session and not cancel_event.is_set():
                speech_session.push(delta)

        result = gemini.analyze_image_stream(
            image_bytes=jpeg_bytes,
            mime_type="image/jpeg",
            on_chunk=_on_chunk,
            cancel_event=cancel_event,
        )

        if result.success:
            print(f"[Cina] Respuesta ({result.model_used}, TTFT: {result.ttft:.2f}s, Total: {result.elapsed:.2f}s):\n{result.text}")
            speech_session.finish()
            if wait_audio:
                speech_session.wait()
        else:
            speech_session.cancel()
            if result.error_type != "cancelled":
                print(f"[Cina] Error: {result.text}")
                if wait_audio:
                    tts._speak_worker(result.text)
                else:
                    tts.speak_async(result.text)
    else:
        result = gemini.analyze_image_stream(
            image_bytes=jpeg_bytes,
            mime_type="image/jpeg",
            cancel_event=cancel_event,
        )
        if result.success:
            print(f"[Cina] Respuesta ({result.model_used}, Total: {result.elapsed:.2f}s):\n{result.text}")
            if wait_audio:
                tts._speak_worker(result.text)
            else:
                tts.speak_async(result.text)
        elif result.error_type != "cancelled":
            print(f"[Cina] Error: {result.text}")
            if wait_audio:
                tts._speak_worker(result.text)
            else:
                tts.speak_async(result.text)


def run_oneshot():
    """Ejecuta una captura y respuesta directa sin interfaz gráfica (modo standalone / trigger)."""
    config_mgr, cap, gemini, tts = _create_services()
    cancel_event = threading.Event()
    _execute_pipeline(config_mgr, cap, gemini, tts, cancel_event=cancel_event, wait_audio=True)


def handle_trigger():
    """Envía la señal en 1 solo viaje al servidor activo o ejecuta captura autónoma."""
    from cina.ipc_service import IPCClient

    if IPCClient.send_trigger():
        print("[Cina] Disparo enviado con éxito.")
        return

    print("[Cina] Servidor en segundo plano no detectado. Iniciando modo autónomo...")
    run_oneshot()


def handle_stop():
    from cina.ipc_service import IPCClient

    if IPCClient.send_stop():
        print("[Cina] Audio detenido.")
    else:
        print("[Cina] No hay ninguna instancia activa.")


def run_headless():
    """Ejecuta el demonio persistente en segundo plano sin GUI (reutiliza conexiones y circuit breaker)."""
    from cina.hotkey_service import HotkeyService

    print("[Cina] Iniciando en modo demonio persistente (Headless)... Presiona Ctrl+C para salir.")
    config_mgr, cap, gemini, tts = _create_services()
    threading.Thread(target=gemini.warmup, daemon=True).start()

    state_lock = threading.Lock()
    active_cancel: dict = {"event": None}

    def on_stop():
        with state_lock:
            ev = active_cancel.get("event")
            if ev is not None:
                ev.set()
        tts.stop()

    def on_trigger():
        # Cancelar cualquier lectura o consulta anterior de forma limpia
        with state_lock:
            prev = active_cancel.get("event")
            if prev is not None:
                prev.set()
            new_cancel = threading.Event()
            active_cancel["event"] = new_cancel

        tts.stop()
        _execute_pipeline(config_mgr, cap, gemini, tts, cancel_event=new_cancel, wait_audio=False)

    hotkey = HotkeyService(
        hotkey_str=config_mgr.get("hotkey", "<ctrl>+<alt>+s"),
        on_trigger=on_trigger,
        on_stop=on_stop,
    )
    hotkey.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[Cina] Deteniendo demonio...")
        on_stop()
        hotkey.stop()


def install_shortcut():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    trigger_script = os.path.join(base_dir, "cina-trigger")
    run_script = os.path.join(base_dir, "run.sh")
    desktop_dir = os.path.expanduser("~/.local/share/applications")
    bin_dir = os.path.expanduser("~/.local/bin")
    os.makedirs(desktop_dir, exist_ok=True)
    os.makedirs(bin_dir, exist_ok=True)

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
Name=Cina Screen Reader Trigger
Comment=Disparador rápido de lectura visual por voz Cina
Exec={link_trigger}
Icon=audio-volume-high
Type=Application
Terminal=false
NoDisplay=false
Categories=Utility;Accessibility;
X-KDE-Shortcuts=Ctrl+Alt+S
"""
    with open(desktop_file, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(desktop_file, 0o755)
    print(f"[Cina] Lanzador de atajo instalado en: {desktop_file}")
    print("[Cina] En KDE -> Atajos de teclado puedes asignar la orden simple: cina-trigger")


def main():
    parser = argparse.ArgumentParser(description="Cina - Asistente de accesibilidad visual y lectura por voz")
    parser.add_argument("--trigger", action="store_true", help="Disparar captura instantánea")
    parser.add_argument("--stop", action="store_true", help="Detener audio en reproducción")
    parser.add_argument("--headless", action="store_true", help="Ejecutar en segundo plano sin ventana GUI")
    parser.add_argument("--install-shortcut", action="store_true", help="Instalar lanzador de atajo para KDE")

    args, _ = parser.parse_known_args()

    if args.stop:
        handle_stop()
        return

    if args.trigger:
        handle_trigger()
        return

    if args.install_shortcut:
        install_shortcut()
        return

    if args.headless:
        run_headless()
        return

    # Importación diferida de Tkinter/GUI únicamente cuando se abre la ventana
    try:
        import tkinter as tk
        from cina.gui import CinaApp

        root = tk.Tk()
    except Exception as e:
        print(f"[Cina] No se pudo abrir la interfaz gráfica Tkinter ({e}).")
        if os.environ.get("NIRI_SOCKET") or "niri" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower():
            print("[Cina] En Niri (Wayland puro), habilita 'xwayland-satellite' para usar la GUI o ejecuta 'cina --headless'.")
        print("[Cina] Iniciando automáticamente en modo demonio (--headless) para responder a 'cina-trigger'...")
        run_headless()
        return

    CinaApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
