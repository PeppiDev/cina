import os
import json
import tempfile
from pathlib import Path
from typing import Dict, Any

CONFIG_DIR = Path.home() / ".config" / "cina"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "api_key": "",
    "model": "gemini-3.8-flash",
    "thinking_level": "low",  # "minimal" (flash-lite), "low" (rápido), "medium" (profundo)
    "media_resolution": "high",  # "medium" (más rápido, 560 tokens) o "high" (máxima nitidez OCR, 1120 tokens)
    "stream_audio": True,  # Streaming Gemini + TTS frase por frase para latencia mínima
    "request_timeout": 15.0,  # Timeout por petición a la API en segundos
    "hedge_delay": 2.5,  # Si no llega el primer token en N s, lanzar en paralelo el modelo de respaldo (0 = off)
    "tts_timeout": 5.0,  # Timeout por frase en Edge-TTS antes de fallback local
    "image_max_width": 1600,  # Ancho máximo de captura (equilibrio nitidez/velocidad)
    "image_quality": 82,  # Calidad JPEG (80-85 ideal para texto)
    "voice": "es-ES-AlvaroNeural",
    "tts_engine": "piper",  # "piper" (local, recomendado), "edge-tts" (online) o "espeak-ng"
    "piper_model": "",  # Ruta al .onnx de Piper; vacío = autodetectar en ~/.local/share/cina/voices
    "speech_rate": "+0%",
    "speech_volume": "+0%",
    "system_prompt": "",  # Instrucciones de sistema para Gemini (vacío = prompt por defecto)
    "user_prompt": "",  # Mensaje enviado junto a la captura (vacío = prompt por defecto)
    "hotkey": "<ctrl>+<alt>+s",
    "capture_backend": "auto",  # "auto", "grim", "niri", "spectacle", "scrot", "maim", "mss", "gnome-screenshot", "pillow"
    "prompt_style": "concise_voice",  # Respuestas concisas preparadas para locución
    "play_start_chime": False,  # Omitir tono de inicio para lectura inmediata
    "audio_player": "auto",  # "auto", "pw-play", "mpv", "ffplay", "paplay"
}

AVAILABLE_VOICES = [
    ("es-ES-AlvaroNeural", "Español (España) - Álvaro (Hombre)"),
    ("es-ES-ElviraNeural", "Español (España) - Elvira (Mujer)"),
    ("es-MX-DaliaNeural", "Español (México) - Dalia (Mujer)"),
    ("es-MX-JorgeNeural", "Español (México) - Jorge (Hombre)"),
    ("es-AR-TomasNeural", "Español (Argentina) - Tomás (Hombre)"),
    ("es-AR-ElenaNeural", "Español (Argentina) - Elena (Mujer)"),
    ("es-CO-GonzaloNeural", "Español (Colombia) - Gonzalo (Hombre)"),
    ("es-CO-SalomeNeural", "Español (Colombia) - Salomé (Mujer)"),
    ("es-CL-LorenzoNeural", "Español (Chile) - Lorenzo (Hombre)"),
    ("es-CL-CatalinaNeural", "Español (Chile) - Catalina (Mujer)"),
]

AVAILABLE_MODELS = [
    ("gemini-3.8-flash", "Gemini 3.8 Flash (Recomendado: Rápido y Preciso)"),
    ("gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite (Ultra Rápido, ~0.5-0.8s)"),
    ("gemini-3.7-flash", "Gemini 3.7 Flash (Respaldo Multimodal)"),
]

THINKING_LEVELS = [
    ("low", "Rápido (LOW - Recomendado)"),
    ("minimal", "Instantáneo (MINIMAL - Flash-Lite)"),
    ("medium", "Profundo (MEDIUM - Código/Mate)"),
]

TTS_ENGINES = [
    ("edge-tts", "Edge-TTS (Neuronal Online)"),
    ("piper", "Piper (Neuronal Offline Ultra-Rápido)"),
    ("espeak-ng", "eSpeak-NG (Offline Instantáneo)"),
]


class ConfigManager:
    def __init__(self):
        self.config: Dict[str, Any] = DEFAULT_CONFIG.copy()
        self.load()

    def load(self) -> Dict[str, Any]:
        """Carga la configuración desde el disco o crea una por defecto."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.config.update(data)
            else:
                self.save()
        except Exception as e:
            print(f"[Config] Error al cargar configuración: {e}")

        # Comprobar si hay variable de entorno GEMINI_API_KEY
        env_key = os.environ.get("GEMINI_API_KEY")
        if env_key and not self.config.get("api_key"):
            self.config["api_key"] = env_key

        return self.config

    def save(self) -> bool:
        """Guarda la configuración de forma atómica con permisos seguros (0600)."""
        tmp_path = None
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=".config_", suffix=".tmp", dir=str(CONFIG_DIR))
            try:
                os.chmod(tmp_path, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.config, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
            except Exception:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            os.replace(tmp_path, CONFIG_FILE)
            os.chmod(CONFIG_FILE, 0o600)
            return True
        except Exception as e:
            print(f"[Config] Error al guardar configuración: {e}")
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            return False

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.config[key] = value
        self.save()


config_mgr = ConfigManager()
