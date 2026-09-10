import os
import sys
import time
import shutil
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional
from PIL import Image, ImageTk

from cina.config import config_mgr, AVAILABLE_VOICES, AVAILABLE_MODELS
from cina.capture import ScreenCapture
from cina.gemini_service import GeminiService
from cina.tts_service import TTSService
from cina.hotkey_service import HotkeyService

class CinaApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Cina - Asistente de Pantalla y Audio con IA")
        self.root.geometry("820x720")
        self.root.minsize(700, 600)

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

        # Servicios
        self.capture_service = ScreenCapture(preferred_backend=config_mgr.get("capture_backend", "auto"))
        self.gemini_service = GeminiService(
            api_key=config_mgr.get("api_key", ""),
            model=config_mgr.get("model", "gemini-2.5-flash")
        )
        self.tts_service = TTSService(
            voice=config_mgr.get("voice", "es-ES-AlvaroNeural"),
            rate=config_mgr.get("speech_rate", "+0%"),
            volume=config_mgr.get("speech_volume", "+0%")
        )

        self.is_processing = False
        self.last_thumbnail_img = None

        self._setup_styles()
        self._build_ui()

        # Iniciar servicio de atajo global e IPC
        self.hotkey_service = HotkeyService(
            hotkey_str=config_mgr.get("hotkey", "<ctrl>+<alt>+s"),
            on_trigger=self.trigger_capture_and_solve
        )
        self.hotkey_service.start()

        # Manejar cierre de ventana
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Configuración general
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
        # Contenedor principal con padding
        main_frame = ttk.Frame(self.root, padding=16)
        main_frame.pack(fill="both", expand=True)

        # 1. Cabecera
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill="x", pady=(0, 12))

        title_lbl = ttk.Label(header_frame, text="CINA AI Assistant", style="Title.TLabel")
        title_lbl.pack(anchor="w")

        sub_lbl = ttk.Label(header_frame, text="Captura silenciosa de pantalla y respuestas instantáneas por voz con Google Gemini", style="Subtitle.TLabel")
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
            fg=self.success_color
        )
        self.status_label.pack(side="left")

        # Atajo visible en barra de estado
        hotkey_text = config_mgr.get("hotkey", "<ctrl>+<alt>+s").upper().replace("<", "").replace(">", "")
        self.hotkey_info = ttk.Label(
            status_box,
            text=f"Atajo: [{hotkey_text}]",
            font=("Sans", 10, "bold"),
            foreground="#61afef",
            style="Card.TLabel"
        )
        self.hotkey_info.pack(side="right")

        # 3. Botones de Acción Inmediata
        actions_frame = ttk.Frame(main_frame)
        actions_frame.pack(fill="x", pady=(0, 12))

        self.btn_capture = ttk.Button(actions_frame, text="⚡ Capturar y Responder Ahora", style="Accent.TButton", command=self.trigger_capture_and_solve)
        self.btn_capture.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_stop = ttk.Button(actions_frame, text="⏹️ Detener Audio", style="Stop.TButton", command=self.stop_audio)
        self.btn_stop.pack(side="left", padx=(0, 6))

        self.btn_kde = ttk.Button(actions_frame, text="⚙️ Atajo KDE", style="Action.TButton", command=self.show_kde_shortcut_dialog)
        self.btn_kde.pack(side="left")

        # 4. Sección de Configuración (Card colapsable / ordenada)
        config_card = ttk.Frame(main_frame, style="Card.TFrame", padding=12)
        config_card.pack(fill="x", pady=(0, 12))

        # Fila 1: Gemini API Key
        key_frame = ttk.Frame(config_card, style="Card.TFrame")
        key_frame.pack(fill="x", pady=4)

        ttk.Label(key_frame, text="Gemini API Key:", width=15, style="Card.TLabel").pack(side="left")
        self.api_key_var = tk.StringVar(value=config_mgr.get("api_key", ""))
        self.api_key_entry = tk.Entry(key_frame, textvariable=self.api_key_var, show="•", bg="#333842", fg="#ffffff", insertbackground="#ffffff", relief="flat", highlightthickness=1, highlightbackground=self.border_color)
        self.api_key_entry.pack(side="left", fill="x", expand=True, padx=6, ipady=4)

        self.btn_show_key = ttk.Button(key_frame, text="👁", width=3, style="Action.TButton", command=self._toggle_show_key)
        self.btn_show_key.pack(side="left", padx=(0, 4))

        self.btn_save_key = ttk.Button(key_frame, text="Guardar", style="Action.TButton", command=self._save_api_key)
        self.btn_save_key.pack(side="left")

        # Nota de ayuda para múltiples claves
        ttk.Label(
            config_card,
            text="💡 Tip: Puedes ingresar varias claves separadas por coma para rotar automáticamente ante límites de cuota (429).",
            font=("Sans", 8),
            foreground="#8a93a2",
            style="Card.TLabel"
        ).pack(anchor="w", padx=(115, 0), pady=(0, 6))

        # Fila 2: Modelo y Voz
        opts_frame = ttk.Frame(config_card, style="Card.TFrame")
        opts_frame.pack(fill="x", pady=4)

        # Selector de Modelo
        ttk.Label(opts_frame, text="Modelo IA:", width=15, style="Card.TLabel").pack(side="left")
        self.model_var = tk.StringVar(value=config_mgr.get("model", "gemini-3.7-flash"))
        model_combo = ttk.Combobox(opts_frame, textvariable=self.model_var, values=[m[0] for m in AVAILABLE_MODELS], state="readonly", width=18)
        model_combo.pack(side="left", padx=(0, 12))
        model_combo.bind("<<ComboboxSelected>>", self._on_model_changed)

        # Selector de Voz
        ttk.Label(opts_frame, text="Voz en Español:", style="Card.TLabel").pack(side="left", padx=(6, 4))
        self.voice_var = tk.StringVar(value=config_mgr.get("voice", "es-ES-AlvaroNeural"))
        voice_dict = {v[0]: v[1] for v in AVAILABLE_VOICES}
        self.voice_combo = ttk.Combobox(opts_frame, textvariable=self.voice_var, values=[v[0] for v in AVAILABLE_VOICES], state="readonly", width=20)
        self.voice_combo.pack(side="left", padx=(0, 8))
        self.voice_combo.bind("<<ComboboxSelected>>", self._on_voice_changed)

        btn_test_voice = ttk.Button(opts_frame, text="🔊 Probar Voz", style="Action.TButton", command=self._test_voice)
        btn_test_voice.pack(side="left")

        # 5. Panel de Resultados y Previsualización
        results_frame = ttk.Frame(main_frame)
        results_frame.pack(fill="both", expand=True)

        # Columna Izquierda: Previsualización de la captura
        preview_card = ttk.Frame(results_frame, style="Card.TFrame", padding=10)
        preview_card.pack(side="left", fill="both", expand=False, padx=(0, 8))

        ttk.Label(preview_card, text="ÚLTIMA CAPTURA", font=("Sans", 9, "bold"), foreground="#8a93a2", style="Card.TLabel").pack(anchor="w", pady=(0, 4))

        self.preview_lbl = tk.Label(preview_card, text="Sin capturas recientes", bg="#1e2227", fg="#5c6370", width=34, height=15)
        self.preview_lbl.pack(fill="both", expand=True)

        # Columna Derecha: Texto de la respuesta de la IA
        text_card = ttk.Frame(results_frame, style="Card.TFrame", padding=10)
        text_card.pack(side="left", fill="both", expand=True)

        text_header = ttk.Frame(text_card, style="Card.TFrame")
        text_header.pack(fill="x", pady=(0, 4))

        ttk.Label(text_header, text="RESPUESTA DETECTADA (AUDIO)", font=("Sans", 9, "bold"), foreground="#8a93a2", style="Card.TLabel").pack(side="left")

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
            pady=10
        )
        self.response_text.pack(fill="both", expand=True)
        self.response_text.insert("1.0", "Presiona el atajo de teclado o pulsa 'Capturar y Responder' para analizar la pantalla.")

        # Pie de página / Info
        footer = ttk.Frame(main_frame)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Label(
            footer,
            text="💡 Tip: Puedes minimizar esta ventana. La captura se realizará silenciosamente en segundo plano.",
            font=("Sans", 9),
            foreground="#5c6370"
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
        messagebox.showinfo("Guardado", "API Key de Gemini guardada correctamente.")

    def _on_model_changed(self, event=None):
        model = self.model_var.get()
        config_mgr.set("model", model)
        self.gemini_service.set_model(model)

    def _on_voice_changed(self, event=None):
        voice = self.voice_var.get()
        config_mgr.set("voice", voice)
        self.tts_service.voice = voice

    def _test_voice(self):
        voice = self.voice_var.get()
        self.update_status("🔊 Probando voz...", self.warning_color)
        self.tts_service.speak_async(
            text="Hola, la síntesis de voz en español está funcionando con total claridad.",
            voice=voice,
            on_finish=lambda: self.root.after(0, lambda: self.update_status("🟢 Listo", self.success_color))
        )

    def stop_audio(self):
        self.tts_service.stop()
        self.update_status("🟢 Listo", self.success_color)

    def update_status(self, text: str, color: str):
        self.status_label.configure(text=text, fg=color)

    def set_response_text(self, text: str, elapsed: Optional[float] = None):
        self.response_text.delete("1.0", "end")
        self.response_text.insert("1.0", text)
        if elapsed is not None:
            self.time_lbl.configure(text=f"Tiempo: {elapsed:.2f}s")

    def update_thumbnail(self, image_path: str):
        try:
            with Image.open(image_path) as img:
                img.thumbnail((260, 200), Image.Resampling.LANCZOS)
                self.last_thumbnail_img = ImageTk.PhotoImage(img)
                self.preview_lbl.configure(image=self.last_thumbnail_img, text="")
        except Exception as e:
            print(f"[GUI] Error al cargar thumbnail: {e}")

    def trigger_capture_and_solve(self):
        """Dispara el flujo completo: Captura Silenciosa -> Gemini Vision -> Audio TTS."""
        if self.is_processing:
            print("[Cina] Ya hay una solicitud en curso, ignorando...")
            return

        self.is_processing = True

        def worker():
            try:
                # 1. Captura de pantalla silenciosa
                self.root.after(0, lambda: self.update_status("🟡 Capturando pantalla...", self.warning_color))
                raw_path = self.capture_service.capture_silent()
                if not raw_path:
                    self.root.after(0, lambda: self.update_status("🔴 Error al capturar", self.error_color))
                    self.root.after(0, lambda: self.set_response_text("No se pudo capturar la pantalla."))
                    self.tts_service.speak_async("Error: no se pudo capturar la pantalla.")
                    self.is_processing = False
                    return

                # Optimizar imagen para transmisión rápida
                opt_path = self.capture_service.optimize_image(raw_path)
                self.root.after(0, lambda: self.update_thumbnail(opt_path))

                # 2. Análisis con Gemini
                self.root.after(0, lambda: self.update_status("🟣 Analizando con Gemini...", "#b294bb"))
                success, response_text, elapsed = self.gemini_service.analyze_image(opt_path)

                # Limpieza de archivos temporales
                for p in [raw_path, opt_path]:
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass

                if not success:
                    self.root.after(0, lambda: self.update_status("🔴 Error en Gemini", self.error_color))
                    self.root.after(0, lambda: self.set_response_text(response_text, elapsed))
                    self.tts_service.speak_async(
                        text=response_text,
                        on_finish=lambda: self.root.after(0, lambda: self.update_status("🔴 Error", self.error_color))
                    )
                    self.is_processing = False
                    return

                # Mostrar texto
                self.root.after(0, lambda: self.set_response_text(response_text, elapsed))

                # 3. Reproducción por Audio (TTS)
                self.root.after(0, lambda: self.update_status("🔊 Reproduciendo respuesta...", self.success_color))
                self.tts_service.speak_async(
                    text=response_text,
                    on_finish=lambda: self.root.after(0, lambda: self.update_status("🟢 Listo", self.success_color))
                )

            except Exception as e:
                print(f"[Cina] Error en pipeline: {e}")
                self.root.after(0, lambda: self.update_status(f"🔴 Error: {e}", self.error_color))
            finally:
                self.is_processing = False

        threading.Thread(target=worker, daemon=True).start()

    def show_kde_shortcut_dialog(self):
        """Muestra la guía y opción de registrar el atajo en KDE Plasma."""
        msg = (
            "Para activar el atajo en KDE Plasma (Wayland):\n\n"
            "1. Abre 'Preferencias del Sistema' en KDE.\n"
            "2. Ve a 'Atajos de teclado' -> 'Atajos personalizados' (o 'Añadir nuevo' -> 'Orden o script').\n"
            "3. En la orden a ejecutar, simplemente escribe:\n"
            "   cina-trigger\n\n"
            "4. Asigna la combinación de teclas que desees (ej: Ctrl+Alt+S o Ctrl+').\n\n"
            "¿Deseas asegurar que el comando 'cina-trigger' esté instalado en tu sistema?"
        )
        if messagebox.askyesno("Configuración de Atajo KDE Plasma", msg):
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
            messagebox.showinfo("Éxito", f"Comando 'cina-trigger' instalado correctamente en {link_trigger}.\n\nEn KDE -> Preferencias del Sistema -> Atajos, puedes usar la orden:\ncina-trigger")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo configurar: {e}")

    def _on_close(self):
        self.hotkey_service.stop()
        self.tts_service.stop()
        self.root.destroy()

