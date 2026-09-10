import os
import re
import shutil
import asyncio
import tempfile
import subprocess
import threading
from typing import Optional, Callable

class TTSService:
    def __init__(self, voice: str = "es-ES-AlvaroNeural", rate: str = "+0%", volume: str = "+0%"):
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.current_process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._is_speaking = False

    def clean_text_for_speech(self, text: str) -> str:
        """Limpia caracteres de markdown y formato para que suene natural al hablar."""
        if not text:
            return ""
        # Quitar bloques de código
        text = re.sub(r"```[\s\S]*?```", " código omitido ", text)
        # Quitar enlaces [texto](url)
        text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
        # Quitar negritas y cursivas (**texto**, *texto*, __texto__)
        text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
        # Quitar encabezados (# Header)
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
        # Quitar viñetas (- o * al inicio)
        text = re.sub(r"^[\*\-\+]\s*", "", text, flags=re.MULTILINE)
        # Quitar números de lista repetitivos si es necesario o espaciarlos
        text = re.sub(r"^\d+\.\s*", "", text, flags=re.MULTILINE)
        # Limpiar saltos de línea excesivos
        text = re.sub(r"\n+", ". ", text)
        # Limpiar espacios extra
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def stop(self):
        """Detiene cualquier audio que se esté reproduciendo actualmente."""
        with self._lock:
            if self.current_process:
                try:
                    self.current_process.terminate()
                    self.current_process.wait(timeout=0.5)
                except Exception:
                    try:
                        self.current_process.kill()
                    except Exception:
                        pass
                self.current_process = None
            self._is_speaking = False

    def is_speaking(self) -> bool:
        return self._is_speaking

    def speak_async(self, text: str, voice: Optional[str] = None, on_start: Optional[Callable] = None, on_finish: Optional[Callable] = None):
        """Genera y reproduce el audio en un hilo independiente sin bloquear."""
        thread = threading.Thread(target=self._speak_worker, args=(text, voice, on_start, on_finish), daemon=True)
        thread.start()

    def _speak_worker(self, text: str, voice: Optional[str] = None, on_start: Optional[Callable] = None, on_finish: Optional[Callable] = None):
        self.stop()
        clean_text = self.clean_text_for_speech(text)
        if not clean_text:
            if on_finish:
                on_finish()
            return

        target_voice = voice or self.voice
        tmp_mp3 = tempfile.mktemp(prefix="cina_tts_", suffix=".mp3")

        self._is_speaking = True
        if on_start:
            try:
                on_start()
            except Exception as e:
                print(f"[TTS] on_start error: {e}")

        success = False
        try:
            # 1. Intentar con Edge-TTS (Voz neuronal natural)
            success = self._generate_edge_tts(clean_text, target_voice, tmp_mp3)
            if success and os.path.exists(tmp_mp3) and os.path.getsize(tmp_mp3) > 100:
                self._play_audio(tmp_mp3)
            else:
                # 2. Fallback offline con espeak-ng
                print("[TTS] Fallback a espeak-ng...")
                self._speak_espeak(clean_text)
        except Exception as e:
            print(f"[TTS] Error en síntesis de voz: {e}")
            self._speak_espeak(clean_text)
        finally:
            self._is_speaking = False
            if os.path.exists(tmp_mp3):
                try:
                    os.remove(tmp_mp3)
                except Exception:
                    pass
            if on_finish:
                try:
                    on_finish()
                except Exception as e:
                    print(f"[TTS] on_finish error: {e}")

    def _generate_edge_tts(self, text: str, voice: str, output_path: str) -> bool:
        """Usa el comando o librería edge-tts para generar el mp3."""
        try:
            import edge_tts

            async def _run():
                communicate = edge_tts.Communicate(text, voice, rate=self.rate, volume=self.volume)
                await communicate.save(output_path)

            asyncio.run(_run())
            return True
        except Exception as e:
            print(f"[TTS] Error ejecutando edge-tts: {e}")
            return False

    def _speak_espeak(self, text: str):
        """Fallback local offline con espeak-ng."""
        if shutil.which("espeak-ng"):
            cmd = ["espeak-ng", "-v", "es", text]
        elif shutil.which("espeak"):
            cmd = ["espeak", "-v", "es", text]
        else:
            print("[TTS] No hay sintetizador de voz disponible.")
            return

        with self._lock:
            self.current_process = subprocess.Popen(cmd)

        self.current_process.wait()
        with self._lock:
            self.current_process = None

    def _play_audio(self, audio_file: str):
        """Reproduce un archivo de audio con el reproductor nativo del sistema."""
        player_cmd = None

        if shutil.which("pw-play"):
            player_cmd = ["pw-play", audio_file]
        elif shutil.which("mpv"):
            player_cmd = ["mpv", "--no-video", audio_file]
        elif shutil.which("ffplay"):
            player_cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", audio_file]
        elif shutil.which("paplay"):
            # Si es paplay, podemos convertir a wav rápidamente con ffmpeg si es mp3
            if audio_file.endswith(".mp3") and shutil.which("ffmpeg"):
                wav_file = audio_file.replace(".mp3", ".wav")
                subprocess.run(["ffmpeg", "-y", "-i", audio_file, wav_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                player_cmd = ["paplay", wav_file]
            else:
                player_cmd = ["paplay", audio_file]

        if not player_cmd:
            print("[TTS] No se encontró ningún reproductor de audio compatible (pw-play, mpv, ffplay, paplay).")
            return

        try:
            with self._lock:
                self.current_process = subprocess.Popen(player_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            self.current_process.wait()
        except Exception as e:
            print(f"[TTS] Error durante la reproducción de audio: {e}")
        finally:
            with self._lock:
                self.current_process = None

