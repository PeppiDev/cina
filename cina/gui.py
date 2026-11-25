import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional
from PIL import Image, ImageTk

from cina.config import (
    config_mgr,
    AVAILABLE_VOICES,
    AVAILABLE_MODELS,
    THINKING_LEVELS,
    TTS_ENGINES,
)
from cina.capture import ScreenCapture
from cina.gemini_service import GeminiService
from cina.tts_service import TTSService
from cina.hotkey_service import HotkeyService


class CinaApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Cina - Lector Visual y Asistente por Voz")
        self.root.geometry("860x760")
        self.root.minsize(740, 620)

        # Configuración de colores estilo KDE Breeze Dark
        self.bg_color = "#1e2227"
        self.card_bg = "#282c34"
        self.fg_color = "#abb2bf"
        self.accent_color = "#3daee9"
        self.accent_hover = "#2980b9"
        self.success_color = "#2ecc71"
        self.warning_color = "#f39c12"
        self.error_color = "#e74c3c"
        self.border_color = "#3e4451"

        self.root.configure(bg=self.bg_color)

        # Servicios persistentes
        self.capture_service = ScreenCapture(preferred_backend=config_mgr.get("capture_backend", "auto"))
        self.gemini_service = GeminiService(
            api_key=config_mgr.get("api_key", ""),
            model=config_mgr.get("model", "gemini-3.8-flash"),
            thinking_level=config_mgr.get("thinking_level", "low"),
            media_resolution=config_mgr.get("media_resolution", "high"),
            timeout=config_mgr.get("request_timeout", 15.0),
            hedge_delay=config_mgr.get("hedge_delay", 2.5),
            system_prompt=config_mgr.get("system_prompt", ""),
            user_prompt=config_mgr.get("user_prompt", ""),
        )
        self.tts_service = TTSService(
            voice=config_mgr.get("voice", "es-ES-AlvaroNeural"),
            rate=config_mgr.get("speech_rate", "+0%"),
            volume=config_mgr.get("speech_volume", "+0%"),
            engine=config_mgr.get("tts_engine", "edge-tts"),
            audio_player=config_mgr.get("audio_player", "auto"),
            tts_timeout=config_mgr.get("tts_timeout", 5.0),
            piper_model=config_mgr.get("piper_model", ""),
        )

        # Control de concurrencia y cancelación limpia (sin race conditions)
        self._req_lock = threading.Lock()
        self._request_id = 0
        self._last_trigger_ts = 0.0
        self._active_cancel_event: Optional[threading.Event] = None
        self.last_thumbnail_img = None

        self._setup_styles()
        self._build_ui()

        # Pre-calentar cliente Gemini en segundo plano para reducir latencia del 1er disparo
        threading.Thread(target=self.gemini_service.warmup, daemon=True).start()
        threading.Thread(target=self.tts_service.warmup, daemon=True).start()

        # Iniciar servicio de atajo global e IPC
        self.hotkey_service = HotkeyService(
            hotkey_str=config_mgr.get("hotkey", "<ctrl>+<alt>+s"),
            on_trigger=self.trigger_capture_and_read,
            on_stop=self.stop_audio,
        )
        self.hotkey_service.start()

        # Manejar cierre de ventana
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=self.bg_color, foreground=self.fg_color, font=("Sans", 10))
        style.configure("TFrame", background=self.bg_color)
        style.configure("Card.TFrame", background=self.card_bg, relief="flat")

        style.configure("TLabel", background=self.bg_color, foreground=self.fg_color)
        style.configure("Card.TLabel", background=self.card_bg, foreground=self.fg_color)
        style.configure("Title.TLabel", font=("Sans", 16, "bold"), foreground="#ffffff")
        style.configure("Subtitle.TLabel", font=("Sans", 10), foreground="#8a93a2")
        style.configure("Badge.TLabel", font=("Sans", 11, "bold"), padding=6)

        style.configure("Accent.TButton", font=("Sans", 11, "bold"), background=self.accent_color, foreground="#ffffff", borderwidth=0, padding=8)
        style.map("Accent.TButton", background=[("active", self.accent_hover), ("pressed", "#1f618d")])

        style.configure("Action.TButton", font=("Sans", 10), background="#3e4451", foreground="#ffffff", borderwidth=0, padding=6)
        style.map("Action.TButton", background=[("active", "#4c5363")])

        style.configure("Stop.TButton", font=("Sans", 10), background="#c0392b", foreground="#ffffff", borderwidth=0, padding=6)
        style.map("Stop.TButton", background=[("active", "#e74c3c")])

        style.configure("TCombobox", fieldbackground="#333842", background="#3e4451", foreground="#ffffff")
        style.configure("TEntry", fieldbackground="#333842", foreground="#ffffff")

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=16)
        main_frame.pack(fill="both", expand=True)

        # 1. Cabecera
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill="x", pady=(0, 12))

        title_lbl = ttk.Label(header_frame, text="Cina Voice Reader", style="Title.TLabel")
        title_lbl.pack(anchor="w")

        sub_lbl = ttk.Label(
            header_frame,
            text="Accesibilidad visual en tiempo real · Captura en RAM + Streaming multimodal + Voz",
            style="Subtitle.TLabel",
        )
        sub_lbl.pack(anchor="w")

        # 2. Barra de Estado
        self.status_card = ttk.Frame(main_frame, style="Card.TFrame", padding=12)
        self.status_card.pack(fill="x", pady=(0, 12))

        status_box = ttk.Frame(self.status_card, style="Card.TFrame")
        status_box.pack(fill="x")

        ttk.Label(status_box, text="ESTADO:", font=("Sans", 10, "bold"), foreground="#8a93a2", style="Card.TLabel").pack(side="left", padx=(0, 8))

        self.status_label = tk.Label(
            status_box,
            text="🟢 Listo (Esperando atajo o clic)",
            font=("Sans", 11, "bold"),
            bg=self.card_bg,
            fg=self.success_color,
        )
        self.status_label.pack(side="left")

        hotkey_text = config_mgr.get("hotkey", "<ctrl>+<alt>+s").upper().replace("<", "").replace(">", "")
        self.hotkey_info = ttk.Label(
            status_box,
            text=f"Atajo: [{hotkey_text}]",
            font=("Sans", 10, "bold"),
            foreground="#61afef",
            style="Card.TLabel",
        )
        self.hotkey_info.pack(side="right")

        # 3. Botones de Acción Inmediata
        actions_frame = ttk.Frame(main_frame)
        actions_frame.pack(fill="x", pady=(0, 12))

        self.btn_capture = ttk.Button(actions_frame, text="⚡ Capturar y Leer Ahora", style="Accent.TButton", command=self.trigger_capture_and_read)
        self.btn_capture.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_stop = ttk.Button(actions_frame, text="⏹️ Detener Audio", style="Stop.TButton", command=self.stop_audio)
        self.btn_stop.pack(side="left", padx=(0, 6))

        is_niri = bool(os.environ.get("NIRI_SOCKET")) or "niri" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        shortcut_btn_text = "⚙️ Atajo Niri" if is_niri else "⚙️ Atajo Escritorio"
        self.btn_kde = ttk.Button(actions_frame, text=shortcut_btn_text, style="Action.TButton", command=self.show_kde_shortcut_dialog)
        self.btn_kde.pack(side="left", padx=(0, 6))

        self.btn_prompt = ttk.Button(actions_frame, text="📝 Prompt", style="Action.TButton", command=self.show_prompt_editor)
        self.btn_prompt.pack(side="left")

        # 4. Sección de Configuración
        config_card = ttk.Frame(main_frame, style="Card.TFrame", padding=12)
        config_card.pack(fill="x", pady=(0, 12))

        # Fila 1: Gemini API Key
        key_frame = ttk.Frame(config_card, style="Card.TFrame")
        key_frame.pack(fill="x", pady=4)

        ttk.Label(key_frame, text="Gemini API Key:", width=15, style="Card.TLabel").pack(side="left")
        self.api_key_var = tk.StringVar(value=config_mgr.get("api_key", ""))
        self.api_key_entry = tk.Entry(
            key_frame,
            textvariable=self.api_key_var,
            show="•",
            bg="#333842",
            fg="#ffffff",
            insertbackground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.border_color,
        )
        self.api_key_entry.pack(side="left", fill="x", expand=True, padx=6, ipady=4)

        self.btn_show_key = ttk.Button(key_frame, text="👁", width=3, style="Action.TButton", command=self._toggle_show_key)
        self.btn_show_key.pack(side="left", padx=(0, 4))

        self.btn_save_key = ttk.Button(key_frame, text="Guardar", style="Action.TButton", command=self._save_api_key)
        self.btn_save_key.pack(side="left")

        ttk.Label(
            config_card,
            text="💡 Tip: Puedes ingresar varias claves separadas por coma para rotar automáticamente ante límites de cuota (429).",
            font=("Sans", 8),
            foreground="#8a93a2",
            style="Card.TLabel",
        ).pack(anchor="w", padx=(115, 0), pady=(0, 6))

        # Fila 2: Modelo y Razonamiento (Velocidad vs Profundidad)
        opts_frame = ttk.Frame(config_card, style="Card.TFrame")
        opts_frame.pack(fill="x", pady=4)

        ttk.Label(opts_frame, text="Modelo IA:", width=15, style="Card.TLabel").pack(side="left")
        current_model = self.gemini_service.model
        model_values = [m[0] for m in AVAILABLE_MODELS]
        if current_model not in model_values:
            model_values.insert(0, current_model)
        self.model_var = tk.StringVar(value=current_model)
        model_combo = ttk.Combobox(opts_frame, textvariable=self.model_var, values=model_values, state="readonly", width=21)
        model_combo.pack(side="left", padx=(0, 12))
        model_combo.bind("<<ComboboxSelected>>", self._on_model_changed)

        ttk.Label(opts_frame, text="Razonamiento:", style="Card.TLabel").pack(side="left", padx=(4, 4))
        self.thinking_var = tk.StringVar(value=config_mgr.get("thinking_level", "low"))
        thinking_combo = ttk.Combobox(
            opts_frame,
            textvariable=self.thinking_var,
            values=[t[0] for t in THINKING_LEVELS],
            state="readonly",
            width=10,
        )
        thinking_combo.pack(side="left", padx=(0, 12))
        thinking_combo.bind("<<ComboboxSelected>>", self._on_thinking_changed)

        ttk.Label(opts_frame, text="Motor TTS:", style="Card.TLabel").pack(side="left", padx=(4, 4))
        self.tts_engine_var = tk.StringVar(value=config_mgr.get("tts_engine", "edge-tts"))
        engine_combo = ttk.Combobox(
            opts_frame,
            textvariable=self.tts_engine_var,
            values=[e[0] for e in TTS_ENGINES],
            state="readonly",
            width=11,
        )
        engine_combo.pack(side="left")
        engine_combo.bind("<<ComboboxSelected>>", self._on_tts_engine_changed)

        # Fila 3: Voz y Probar Voz
        voice_frame = ttk.Frame(config_card, style="Card.TFrame")
        voice_frame.pack(fill="x", pady=4)

        ttk.Label(voice_frame, text="Voz en Español:", width=15, style="Card.TLabel").pack(side="left")
        self.voice_var = tk.StringVar(value=config_mgr.get("voice", "es-ES-AlvaroNeural"))
        self.voice_combo = ttk.Combobox(
            voice_frame,
            textvariable=self.voice_var,
            values=[v[0] for v in AVAILABLE_VOICES],
            state="readonly",
            width=24,
        )
        self.voice_combo.pack(side="left", padx=(0, 8))
        self.voice_combo.bind("<<ComboboxSelected>>", self._on_voice_changed)

        btn_test_voice = ttk.Button(voice_frame, text="🔊 Probar Voz", style="Action.TButton", command=self._test_voice)
        btn_test_voice.pack(side="left")

        # 5. Panel de Resultados y Previsualización
        results_frame = ttk.Frame(main_frame)
        results_frame.pack(fill="both", expand=True)

        preview_card = ttk.Frame(results_frame, style="Card.TFrame", padding=10)
        preview_card.pack(side="left", fill="both", expand=False, padx=(0, 8))

        ttk.Label(preview_card, text="ÚLTIMA CAPTURA", font=("Sans", 9, "bold"), foreground="#8a93a2", style="Card.TLabel").pack(anchor="w", pady=(0, 4))

        self.preview_lbl = tk.Label(preview_card, text="Sin capturas recientes", bg="#1e2227", fg="#5c6370", width=34, height=15)
        self.preview_lbl.pack(fill="both", expand=True)

        text_card = ttk.Frame(results_frame, style="Card.TFrame", padding=10)
        text_card.pack(side="left", fill="both", expand=True)

        text_header = ttk.Frame(text_card, style="Card.TFrame")
        text_header.pack(fill="x", pady=(0, 4))

        ttk.Label(text_header, text="LECTURA EN VIVO (STREAMING AUDIO)", font=("Sans", 9, "bold"), foreground="#8a93a2", style="Card.TLabel").pack(side="left")

        self.time_lbl = ttk.Label(text_header, text="", font=("Sans", 9), foreground="#61afef", style="Card.TLabel")
        self.time_lbl.pack(side="right")

        self.response_text = tk.Text(
            text_card,
            wrap="word",
            bg="#1e2227",
            fg="#e5c07b",
            insertbackground="#ffffff",
            font=("Sans", 11),
            relief="flat",
            padx=10,
            pady=10,
        )
        self.response_text.pack(fill="both", expand=True)
        self.response_text.insert("1.0", "Presiona el atajo de teclado o pulsa 'Capturar y Leer Ahora' para analizar la pantalla.")

        footer = ttk.Frame(main_frame)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Label(
            footer,
            text="💡 Tip: Puedes minimizar esta ventana; el servicio IPC seguirá respondiendo al atajo de teclado.",
            font=("Sans", 9),
            foreground="#5c6370",
        ).pack(side="left")

    def _toggle_show_key(self):
        if self.api_key_entry.cget("show") == "":
            self.api_key_entry.configure(show="•")
            self.btn_show_key.configure(text="👁")
        else:
            self.api_key_entry.configure(show="")
            self.btn_show_key.configure(text="🔒")

    def _save_api_key(self):
        key = self.api_key_var.get().strip()
        config_mgr.set("api_key", key)
        self.gemini_service.set_api_key(key)
        threading.Thread(target=self.gemini_service.warmup, daemon=True).start()
        messagebox.showinfo("Guardado", "API Key de Gemini guardada correctamente.")

    def _on_model_changed(self, event=None):
        model = self.model_var.get()
        config_mgr.set("model", model)
        self.gemini_service.set_model(model)

    def _on_thinking_changed(self, event=None):
        lvl = self.thinking_var.get()
        config_mgr.set("thinking_level", lvl)
        self.gemini_service.set_thinking_level(lvl)

    def _on_tts_engine_changed(self, event=None):
        engine = self.tts_engine_var.get()
        config_mgr.set("tts_engine", engine)
        self.tts_service.engine = engine
        threading.Thread(target=self.tts_service.warmup, daemon=True).start()

    def _on_voice_changed(self, event=None):
        voice = self.voice_var.get()
        config_mgr.set("voice", voice)
        self.tts_service.voice = voice

    def _test_voice(self):
        voice = self.voice_var.get()
        self.update_status("🔊 Probando voz...", self.warning_color)
        self.tts_service.speak_async(
            text="Hola, la síntesis de voz en español está funcionando con latencia mínima.",
            voice=voice,
            on_finish=lambda: self.root.after(0, lambda: self.update_status("🟢 Listo", self.success_color)),
        )

    def stop_audio(self):
        """Detiene cualquier análisis en curso y el audio activo (seguro desde cualquier hilo)."""
        with self._req_lock:
            self._request_id += 1
            if self._active_cancel_event is not None:
                self._active_cancel_event.set()
                self._active_cancel_event = None
        self.tts_service.stop()
        try:
            self.root.after(0, lambda: self.update_status("🟢 Listo", self.success_color))
        except Exception:
            pass

    def update_status(self, text: str, color: str):
        self.status_label.configure(text=text, fg=color)

    def set_response_text(self, text: str, metrics: Optional[str] = None):
        self.response_text.delete("1.0", "end")
        self.response_text.insert("1.0", text)
        if metrics is not None:
            self.time_lbl.configure(text=metrics)

    def append_response_text(self, delta: str):
        self.response_text.insert("end", delta)
        self.response_text.see("end")

    def update_thumbnail_image(self, thumb_img: Optional[Image.Image]):
        if thumb_img is None:
            return
        try:
            self.last_thumbnail_img = ImageTk.PhotoImage(thumb_img)
            self.preview_lbl.configure(image=self.last_thumbnail_img, text="")
        except Exception as e:
            print(f"[GUI] Error al cargar thumbnail: {e}")

    def trigger_capture_and_read(self):
        """Dispara el flujo de baja latencia: Captura en RAM -> Gemini Streaming -> TTS Streaming."""
        now = time.monotonic()
        with self._req_lock:
            # Anti-rebote de 250ms para evitar dobles pulsaciones accidentales
            if now - self._last_trigger_ts < 0.25:
                return
            self._last_trigger_ts = now

            # Cancelar cualquier solicitud/audio previo limpiamente
            if self._active_cancel_event is not None:
                self._active_cancel_event.set()
            self._request_id += 1
            req_id = self._request_id
            cancel_event = threading.Event()
            self._active_cancel_event = cancel_event

        self.tts_service.stop()

        def is_current() -> bool:
            with self._req_lock:
                return self._request_id == req_id and not cancel_event.is_set()

        def worker():
            t_start = time.perf_counter()
            ttfa_holder = {"ttfa": 0.0}
            try:
                # 1. Captura de pantalla en memoria
                self.root.after(0, lambda: is_current() and self.update_status("🟡 Capturando pantalla...", self.warning_color))
                captured = self.capture_service.capture_to_jpeg_bytes(
                    max_width=config_mgr.get("image_max_width", 1600),
                    quality=config_mgr.get("image_quality", 82),
                    make_thumbnail=True,
                )
                if not is_current():
                    return

                if not captured:
                    self.root.after(0, lambda: self.update_status("🔴 Error al capturar", self.error_color))
                    self.root.after(0, lambda: self.set_response_text("No se pudo capturar la pantalla.", ""))
                    self.tts_service.speak_async("Error: no se pudo capturar la pantalla.")
                    return

                jpeg_bytes, thumb_img = captured
                self.root.after(0, lambda: is_current() and self.update_thumbnail_image(thumb_img))
                self.root.after(0, lambda: is_current() and self.set_response_text("", ""))

                # 2. Streaming Gemini + Pipeline de Audio frase por frase
                self.root.after(0, lambda: is_current() and self.update_status("🟣 Analizando con Gemini...", "#b294bb"))

                stream_audio = bool(config_mgr.get("stream_audio", True))
                speech_session = None

                if stream_audio:
                    def _on_first_audio():
                        ttfa_holder["ttfa"] = time.perf_counter() - t_start
                        if is_current():
                            self.root.after(0, lambda: is_current() and self.update_status("🔊 Reproduciendo respuesta...", self.success_color))

                    def _on_tts_finish():
                        if is_current():
                            self.root.after(0, lambda: is_current() and self.update_status("🟢 Listo", self.success_color))

                    speech_session = self.tts_service.start_stream(
                        on_first_audio=_on_first_audio,
                        on_finish=_on_tts_finish,
                    )

                def _on_chunk(delta: str):
                    if not is_current():
                        return
                    if speech_session is not None:
                        speech_session.push(delta)
                    self.root.after(0, lambda d=delta: is_current() and self.append_response_text(d))

                result = self.gemini_service.analyze_image_stream(
                    image_bytes=jpeg_bytes,
                    mime_type="image/jpeg",
                    on_chunk=_on_chunk,
                    cancel_event=cancel_event,
                )

                if not is_current():
                    if speech_session is not None:
                        speech_session.cancel()
                    return

                total_elapsed = time.perf_counter() - t_start

                if not result.success:
                    if speech_session is not None:
                        speech_session.cancel()
                    if result.error_type == "cancelled":
                        return
                    metrics = f"Error en {total_elapsed:.2f}s"
                    self.root.after(0, lambda: self.update_status("🔴 Error en Gemini", self.error_color))
                    self.root.after(0, lambda: self.set_response_text(result.text, metrics))
                    self.tts_service.speak_async(
                        text=result.text,
                        on_finish=lambda: self.root.after(0, lambda: is_current() and self.update_status("🔴 Error", self.error_color)),
                    )
                    return

                if speech_session is not None:
                    speech_session.finish()
                else:
                    self.root.after(0, lambda: is_current() and self.update_status("🔊 Reproduciendo respuesta...", self.success_color))
                    self.tts_service.speak_async(
                        text=result.text,
                        on_finish=lambda: self.root.after(0, lambda: is_current() and self.update_status("🟢 Listo", self.success_color)),
                    )

                # Mostrar métricas precisas (TTFT / 1er audio / Total / Modelo)
                ttfa_val = ttfa_holder["ttfa"]
                if ttfa_val > 0:
                    metrics = f"1er audio: {ttfa_val:.2f}s | Total: {total_elapsed:.2f}s ({result.model_used})"
                else:
                    metrics = f"TTFT: {result.ttft:.2f}s | Total: {total_elapsed:.2f}s ({result.model_used})"
                self.root.after(0, lambda m=metrics: is_current() and self.time_lbl.configure(text=m))

            except Exception as e:
                print(f"[Cina] Error en pipeline: {e}")
                if is_current():
                    self.root.after(0, lambda err=str(e): self.update_status(f"🔴 Error: {err}", self.error_color))

        threading.Thread(target=worker, daemon=True).start()

    def show_prompt_editor(self):
        """Ventana para ver, editar y guardar los prompts que se envían a Gemini."""
        from cina.gemini_service import SYSTEM_PROMPT, USER_PROMPT

        existing = getattr(self, "_prompt_window", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return

        win = tk.Toplevel(self.root)
        self._prompt_window = win
        win.title("Prompt enviado a Gemini")
        win.configure(bg=self.bg_color)
        win.geometry("760x560")
        win.transient(self.root)

        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)

        def _make_text(height: int) -> tk.Text:
            return tk.Text(
                frame,
                wrap="word",
                height=height,
                bg="#1e2227",
                fg="#e5e5e5",
                insertbackground="#ffffff",
                font=("Sans", 10),
                relief="flat",
                padx=8,
                pady=8,
                undo=True,
            )

        ttk.Label(frame, text="INSTRUCCIONES DE SISTEMA (system prompt)", font=("Sans", 9, "bold"), foreground="#8a93a2").pack(anchor="w")
        system_txt = _make_text(18)
        system_txt.pack(fill="both", expand=True, pady=(4, 10))
        system_txt.insert("1.0", self.gemini_service.system_prompt)

        ttk.Label(frame, text="MENSAJE ENVIADO JUNTO A LA CAPTURA (user prompt)", font=("Sans", 9, "bold"), foreground="#8a93a2").pack(anchor="w")
        user_txt = _make_text(3)
        user_txt.pack(fill="x", pady=(4, 10))
        user_txt.insert("1.0", self.gemini_service.user_prompt)

        status_lbl = ttk.Label(frame, text="", font=("Sans", 9), foreground="#98c379")

        def _save():
            system_prompt = system_txt.get("1.0", "end").strip()
            user_prompt = user_txt.get("1.0", "end").strip()
            # Guardar vacío si coincide con el predeterminado, para seguir futuras mejoras del prompt por defecto
            config_mgr.config["system_prompt"] = "" if system_prompt == SYSTEM_PROMPT else system_prompt
            config_mgr.config["user_prompt"] = "" if user_prompt == USER_PROMPT else user_prompt
            saved = config_mgr.save()
            self.gemini_service.set_prompts(system_prompt, user_prompt)
            if saved:
                status_lbl.configure(text="✔ Guardado. Se aplicará en la próxima captura.", foreground="#98c379")
            else:
                status_lbl.configure(text="⚠ Aplicado, pero no se pudo guardar en disco.", foreground="#e06c75")

        def _restore_defaults():
            system_txt.delete("1.0", "end")
            system_txt.insert("1.0", SYSTEM_PROMPT)
            user_txt.delete("1.0", "end")
            user_txt.insert("1.0", USER_PROMPT)
            status_lbl.configure(text="Valores por defecto cargados (pulsa Guardar para aplicarlos).", foreground="#61afef")

        btns = ttk.Frame(frame)
        btns.pack(fill="x")
        ttk.Button(btns, text="💾 Guardar", style="Accent.TButton", command=_save).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="↺ Restaurar por defecto", style="Action.TButton", command=_restore_defaults).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Cerrar", style="Action.TButton", command=win.destroy).pack(side="right")
        status_lbl.pack(anchor="w", pady=(8, 0))

    def show_kde_shortcut_dialog(self):
        """Muestra la guía de configuración de atajos para Niri o KDE Plasma."""
        is_niri = bool(os.environ.get("NIRI_SOCKET")) or "niri" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        if is_niri:
            msg = (
                "Para activar los atajos en Niri (~/.config/niri/config.kdl):\n\n"
                "binds {\n"
                '    Ctrl+Alt+S { spawn "~/.nix-profile/bin/cina-trigger"; }\n'
                '    Ctrl+Alt+X { spawn "~/.nix-profile/bin/cina-trigger" "--stop"; }\n'
                "}\n\n"
                "En Niri el atajo debe registrarlo el compositor: así funciona aunque\n"
                "la ventana de Cina no esté visible ni enfocada.\n\n"
                "¿Deseas instalar/actualizar el acceso 'cina-trigger' en ~/.local/bin?"
            )
            if messagebox.askyesno("Configuración de Atajo Niri", msg):
                self._install_desktop_shortcut()
            return

        msg = (
            "Para activar el atajo en KDE Plasma (Wayland):\n\n"
            "1. Abre 'Preferencias del Sistema' en KDE.\n"
            "2. Ve a 'Atajos de teclado' -> 'Atajos personalizados' (o 'Añadir nuevo' -> 'Orden o script').\n"
            "3. En la orden a ejecutar, simplemente escribe:\n"
            "   cina-trigger\n\n"
            "   (Opcional: 'cina-trigger --stop' para detener el audio al instante)\n\n"
            "4. Asigna la combinación de teclas que desees (ej: Ctrl+Alt+S o Ctrl+').\n\n"
            "¿Deseas asegurar que el comando 'cina-trigger' esté instalado en tu sistema?"
        )
        if messagebox.askyesno("Configuración de Atajo de Escritorio", msg):
            self._install_desktop_shortcut()

    def _install_desktop_shortcut(self):
        try:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            trigger_script = os.path.join(base_dir, "cina-trigger")
            run_script = os.path.join(base_dir, "run.sh")
            desktop_dir = os.path.expanduser("~/.local/share/applications")
            bin_dir = os.path.expanduser("~/.local/bin")
            os.makedirs(desktop_dir, exist_ok=True)
            os.makedirs(bin_dir, exist_ok=True)

            link_trigger = os.path.join(bin_dir, "cina-trigger")
            link_run = os.path.join(bin_dir, "cina")
            if os.path.lexists(link_trigger):
                os.remove(link_trigger)
            os.symlink(trigger_script, link_trigger)
            if os.path.lexists(link_run):
                os.remove(link_run)
            os.symlink(run_script, link_run)

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
            messagebox.showinfo("Éxito", f"Comando 'cina-trigger' instalado correctamente en {link_trigger}.\n\nEn KDE -> Preferencias del Sistema -> Atajos, puedes usar la orden:\ncina-trigger")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo configurar: {e}")

    def _on_close(self):
        with self._req_lock:
            if self._active_cancel_event is not None:
                self._active_cancel_event.set()
        self.hotkey_service.stop()
        self.tts_service.stop()
        self.root.destroy()
