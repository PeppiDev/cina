import asyncio
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Callable, List, Optional, Tuple


def _get_fast_temp_dir() -> str:
    shm = "/dev/shm"
    if os.path.isdir(shm) and os.access(shm, os.W_OK):
        return shm
    return tempfile.gettempdir()


PIPER_VOICE_DIRS = (
    os.path.expanduser("~/.local/share/cina/voices"),
    os.path.expanduser("~/.local/share/piper-voices"),
    os.path.expanduser("~/.local/share/piper"),
)


def find_piper_model(voice: str = "", search_dirs=PIPER_VOICE_DIRS) -> str:
    """
    Busca un modelo .onnx de Piper instalado localmente.
    Prioriza el que coincide con el locale de la voz configurada (es-MX-... -> es_MX-...).
    """
    found: List[str] = []
    for d in search_dirs:
        try:
            for name in sorted(os.listdir(d)):
                path = os.path.join(d, name)
                if name.endswith(".onnx") and os.path.exists(path + ".json"):
                    found.append(path)
        except OSError:
            continue
    if not found:
        return ""
    locale = "_".join((voice or "").split("-")[:2])  # "es-MX-JorgeNeural" -> "es_MX"
    if locale:
        for path in found:
            if os.path.basename(path).startswith(locale + "-"):
                return path
    for path in found:
        if os.path.basename(path).startswith("es_"):
            return path
    return found[0]


def _rate_to_length_scale(rate: str) -> Optional[float]:
    """Convierte el rate estilo edge-tts ("+20%", "-10%") a length_scale de Piper."""
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", rate or "")
    if not m:
        return None
    speed = 1.0 + float(m.group(1)) / 100.0
    if speed <= 0.1 or abs(speed - 1.0) < 1e-6:
        return None
    return 1.0 / speed


def _volume_to_factor(volume: str) -> float:
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", volume or "")
    if not m:
        return 1.0
    return max(0.0, 1.0 + float(m.group(1)) / 100.0)


class SentenceChunker:
    """
    Divide texto en streaming en frases naturales para TTS de baja latencia.
    Emite la primera frase cuanto antes (para TTFA mínimo) y agrupa las siguientes
    con tamaño equilibrado para mantener prosodia fluida.
    """

    # Final de oración (. ! ?) seguido de espacio o salto de línea (evita cortar decimales como 3.14)
    _SENTENCE_END_RE = re.compile(r"([.!?]+)\s+|\n+")

    def __init__(
        self,
        first_min_chars: int = 8,
        next_min_chars: int = 35,
        max_buffer_chars: int = 220,
        first_max_chars: int = 70,
    ):
        self.first_min_chars = first_min_chars
        # La síntesis local escala con la longitud: una primera frase corta reduce el tiempo al primer audio
        self.first_max_chars = first_max_chars
        self.next_min_chars = next_min_chars
        self.max_buffer_chars = max_buffer_chars
        self._buffer = ""
        self._emitted_count = 0

    def feed(self, delta: str) -> List[str]:
        if not delta:
            return []
        self._buffer += delta
        sentences: List[str] = []

        while True:
            if self._emitted_count == 0:
                cut = self._first_soft_cut()
                if cut:
                    chunk = self._buffer[:cut].strip()
                    self._buffer = self._buffer[cut:].lstrip()
                    if chunk:
                        sentences.append(chunk)
                        self._emitted_count += 1
                        continue

            min_len = self.first_min_chars if self._emitted_count == 0 else self.next_min_chars
            match = None
            for m in self._SENTENCE_END_RE.finditer(self._buffer):
                if m.end() >= min_len:
                    match = m
                    break

            if match:
                chunk = self._buffer[:match.end()].strip()
                self._buffer = self._buffer[match.end():].lstrip()
                if chunk:
                    sentences.append(chunk)
                    self._emitted_count += 1
                continue

            # Si el buffer crece demasiado sin punto (ej. lista larga con comas), cortar en coma/espacio
            if len(self._buffer) >= self.max_buffer_chars:
                cut_idx = self._buffer.rfind(", ", min_len, self.max_buffer_chars)
                if cut_idx == -1:
                    cut_idx = self._buffer.rfind(" ", min_len, self.max_buffer_chars)
                if cut_idx == -1:
                    cut_idx = self.max_buffer_chars
                else:
                    cut_idx += 1
                chunk = self._buffer[:cut_idx].strip()
                self._buffer = self._buffer[cut_idx:].lstrip()
                if chunk:
                    sentences.append(chunk)
                    self._emitted_count += 1
                continue

            break

        return sentences

    def _first_soft_cut(self) -> Optional[int]:
        """Si la primera frase es larga, corta antes en fin de oración o pausa natural (coma, punto y coma, dos puntos)."""
        if not self.first_max_chars or len(self._buffer) <= self.first_max_chars:
            return None
        for m in self._SENTENCE_END_RE.finditer(self._buffer):
            if m.end() > self.first_max_chars + 1:
                break
            if m.end() >= self.first_min_chars:
                return m.end()
        buf, lo, hi = self._buffer, self.first_min_chars, self.first_max_chars
        best = max(buf.rfind(sep, lo, hi + len(sep)) for sep in (", ", "; ", ": "))
        if best != -1:
            return best + 1
        # Sin pausa explícita: cortar antes de un conector para mantener una prosodia natural
        connectors = (" y ", " que ", " pero ", " con ", " donde ")
        best = max(buf.rfind(sep, lo, hi + len(sep)) for sep in connectors)
        if best != -1:
            return best + 1
        wide = int(hi * 1.6)
        if len(buf) > wide:
            best = max(buf.rfind(sep, lo, wide + len(sep)) for sep in connectors)
            if best != -1:
                return best + 1
        # Frase muy larga sin pausas: cortar en el último espacio antes del límite
        if len(buf) > 2 * hi:
            best = buf.rfind(" ", lo, hi + 1)
            if best != -1:
                return best + 1
        return None

    def flush(self) -> Optional[str]:
        rem = self._buffer.strip()
        self._buffer = ""
        if rem:
            self._emitted_count += 1
            return rem
        return None


class SpeechStreamSession:
    """
    Sesión de voz en streaming con pipeline de 2 etapas (Síntesis -> Reproducción):
    Mientras se reproduce la frase N, ya se está sintetizando la frase N+1 en paralelo.
    Incluye cancelación determinista por evento para evitar voces superpuestas.
    """

    def __init__(
        self,
        tts_service: "TTSService",
        session_id: int,
        voice: str,
        cancel_event: threading.Event,
        on_first_audio: Optional[Callable[[], None]] = None,
        on_finish: Optional[Callable[[], None]] = None,
    ):
        self._tts = tts_service
        self._session_id = session_id
        self._voice = voice
        self.cancel_event = cancel_event
        self._on_first_audio = on_first_audio
        self._on_finish = on_finish

        self._chunker = SentenceChunker()
        self._sentence_queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._audio_queue: "queue.Queue[Optional[Tuple[Optional[str], str]]]" = queue.Queue(maxsize=4)
        self._done_event = threading.Event()
        self._finished_input = False
        self._first_audio_notified = False
        self._input_lock = threading.Lock()

        self._synth_thread = threading.Thread(target=self._synth_worker, daemon=True)
        self._play_thread = threading.Thread(target=self._play_worker, daemon=True)
        self._synth_thread.start()
        self._play_thread.start()

    def push(self, delta: str):
        if self.cancel_event.is_set():
            return
        with self._input_lock:
            if self._finished_input:
                return
            for sentence in self._chunker.feed(delta):
                cleaned = self._tts.clean_text_for_speech(sentence)
                if cleaned:
                    self._sentence_queue.put(cleaned)

    def finish(self):
        with self._input_lock:
            if self._finished_input:
                return
            self._finished_input = True
            rem = self._chunker.flush()
            if rem and not self.cancel_event.is_set():
                cleaned = self._tts.clean_text_for_speech(rem)
                if cleaned:
                    self._sentence_queue.put(cleaned)
            self._sentence_queue.put(None)

    def cancel(self):
        self.cancel_event.set()
        with self._input_lock:
            if not self._finished_input:
                self._finished_input = True
                self._sentence_queue.put(None)

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._done_event.wait(timeout=timeout)

    def _synth_worker(self):
        try:
            while True:
                sentence = self._sentence_queue.get()
                if sentence is None or self.cancel_event.is_set():
                    break

                audio_path = self._tts._synthesize_sentence(sentence, self._voice, self.cancel_event)
                if self.cancel_event.is_set():
                    self._tts._safe_remove(audio_path)
                    break

                # Encolar audio sintetizado (o None para fallback espeak directo)
                while not self.cancel_event.is_set():
                    try:
                        self._audio_queue.put((audio_path, sentence), timeout=0.1)
                        break
                    except queue.Full:
                        continue
                else:
                    self._tts._safe_remove(audio_path)
                    break
        finally:
            # Señalizar fin al hilo reproductor
            while True:
                try:
                    self._audio_queue.put(None, timeout=0.1)
                    break
                except queue.Full:
                    # Vaciar un elemento si está cancelado para evitar bloqueo
                    try:
                        item = self._audio_queue.get_nowait()
                        if item and item[0]:
                            self._tts._safe_remove(item[0])
                    except queue.Empty:
                        pass

    def _play_worker(self):
        try:
            while True:
                item = self._audio_queue.get()
                if item is None:
                    break
                audio_path, fallback_text = item
                if self.cancel_event.is_set():
                    self._tts._safe_remove(audio_path)
                    continue

                if not self._first_audio_notified:
                    self._first_audio_notified = True
                    if self._on_first_audio:
                        try:
                            self._on_first_audio()
                        except Exception as e:
                            print(f"[TTS] on_first_audio error: {e}")

                try:
                    if audio_path and os.path.exists(audio_path) and os.path.getsize(audio_path) > 100:
                        self._tts._play_audio_file(audio_path, self.cancel_event)
                    elif fallback_text and not self.cancel_event.is_set():
                        self._tts._speak_espeak(fallback_text, self.cancel_event)
                finally:
                    self._tts._safe_remove(audio_path)
        finally:
            # Limpiar cualquier archivo restante en la cola si se canceló
            while True:
                try:
                    leftover = self._audio_queue.get_nowait()
                    if leftover and leftover[0]:
                        self._tts._safe_remove(leftover[0])
                except queue.Empty:
                    break

            self._tts._mark_session_finished(self._session_id)
            self._done_event.set()
            if self._on_finish:
                try:
                    self._on_finish()
                except Exception as e:
                    print(f"[TTS] on_finish error: {e}")


class TTSService:
    def __init__(
        self,
        voice: str = "es-ES-AlvaroNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        engine: str = "edge-tts",
        audio_player: str = "auto",
        tts_timeout: float = 5.0,
        piper_model: str = "",
    ):
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.engine = engine or "edge-tts"
        self.audio_player = audio_player or "auto"
        self.tts_timeout = max(1.5, float(tts_timeout))
        self.piper_model = piper_model or find_piper_model(voice)

        # Voz Piper cargada una sola vez en proceso (cargar el .onnx cuesta ~2 s; sintetizar ~0.3 s)
        self._piper_voice = None
        self._piper_voice_path = ""
        self._piper_lock = threading.Lock()
        self._piper_import_failed = False

        self.current_process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._is_speaking = False
        self._session_id = 0
        self._active_cancel_event: Optional[threading.Event] = None
        self._active_session: Optional[SpeechStreamSession] = None

        # Loop asyncio persistente en segundo plano para edge-tts (evita crear/destruir loops)
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None
        self._async_thread: Optional[threading.Thread] = None
        self._loop_lock = threading.Lock()

    def _ensure_async_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self._async_loop is not None and self._async_loop.is_running():
                return self._async_loop

            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _runner():
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()

            t = threading.Thread(target=_runner, daemon=True)
            t.start()
            ready.wait(timeout=2.0)
            self._async_loop = loop
            self._async_thread = t
            return loop

    def warmup(self):
        """Precarga el motor TTS para que el primer disparo no pague la carga del modelo."""
        engine = (self.engine or "edge-tts").lower()
        try:
            if engine == "piper":
                voice = self._get_piper_voice()
                if voice is not None:
                    # Una síntesis corta inicializa las sesiones de onnxruntime
                    for _ in voice.synthesize("Hola."):
                        pass
                    print(f"[TTS] Piper precargado en memoria: {os.path.basename(self._piper_voice_path)}")
                else:
                    print("[TTS] Piper no disponible (falta el módulo 'piper' o un modelo .onnx); se usará edge-tts/espeak-ng.")
            elif engine == "edge-tts":
                import edge_tts  # noqa: F401

                self._ensure_async_loop()
        except Exception as e:
            print(f"[TTS] Warmup omitido: {e}")

    def _get_piper_voice(self):
        """Devuelve la voz Piper cacheada (in-process), o None si no hay módulo/modelo."""
        model = self.piper_model
        if not model or not os.path.exists(model) or self._piper_import_failed:
            return None
        with self._piper_lock:
            if self._piper_voice is not None and self._piper_voice_path == model:
                return self._piper_voice
            try:
                from piper import PiperVoice
            except ImportError:
                self._piper_import_failed = True
                return None
            try:
                self._piper_voice = PiperVoice.load(model)
                self._piper_voice_path = model
            except Exception as e:
                print(f"[TTS] No se pudo cargar el modelo Piper {model}: {e}")
                self._piper_voice = None
            return self._piper_voice

    @staticmethod
    def _safe_remove(path: Optional[str]):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    def clean_text_for_speech(self, text: str) -> str:
        """Limpia caracteres de markdown y formato preservando numeración de listas y fluidez."""
        if not text:
            return ""
        # Quitar bloques de código
        text = re.sub(r"```[\s\S]*?```", " código omitido ", text)
        # Quitar backticks inline manteniendo el identificador
        text = re.sub(r"`([^`]+)`", r"\1", text)
        # Quitar enlaces [texto](url)
        text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
        # Quitar negritas y cursivas (**texto**, *texto*, __texto__)
        text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
        # Quitar encabezados (# Header)
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
        # Quitar viñetas (- o * al inicio) pero CONSERVAR numeración de listas (1., 2., etc.)
        text = re.sub(r"^[\*\-\+•]\s+", "", text, flags=re.MULTILINE)
        # Limpiar asteriscos/almohadillas sueltas
        text = text.replace("*", "").replace("#", "")
        # Limpiar saltos de línea excesivos
        text = re.sub(r"\n+", ". ", text)
        # Evitar doble puntuación (".. ")
        text = re.sub(r"\.\s*\.", ".", text)
        # Limpiar espacios extra
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _kill_current_process_locked(self):
        proc = self.current_process
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=0.35)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            self.current_process = None

    def stop(self):
        """Detiene inmediatamente cualquier síntesis o reproducción en curso sin race conditions."""
        with self._lock:
            self._session_id += 1
            if self._active_cancel_event is not None:
                self._active_cancel_event.set()
            if self._active_session is not None:
                try:
                    self._active_session.cancel()
                except Exception:
                    pass
                self._active_session = None
            self._kill_current_process_locked()
            self._is_speaking = False

    def _mark_session_finished(self, session_id: int):
        with self._lock:
            if self._session_id == session_id:
                self._is_speaking = False
                self._active_session = None

    def is_speaking(self) -> bool:
        return self._is_speaking

    def start_stream(
        self,
        voice: Optional[str] = None,
        on_first_audio: Optional[Callable[[], None]] = None,
        on_finish: Optional[Callable[[], None]] = None,
    ) -> SpeechStreamSession:
        """Inicia una nueva sesión de voz en streaming, cancelando cualquier audio previo."""
        with self._lock:
            self._session_id += 1
            if self._active_cancel_event is not None:
                self._active_cancel_event.set()
            if self._active_session is not None:
                try:
                    self._active_session.cancel()
                except Exception:
                    pass
            self._kill_current_process_locked()

            sid = self._session_id
            cancel_event = threading.Event()
            self._active_cancel_event = cancel_event
            self._is_speaking = True

            session = SpeechStreamSession(
                tts_service=self,
                session_id=sid,
                voice=voice or self.voice,
                cancel_event=cancel_event,
                on_first_audio=on_first_audio,
                on_finish=on_finish,
            )
            self._active_session = session
            return session

    def speak_async(
        self,
        text: str,
        voice: Optional[str] = None,
        on_start: Optional[Callable] = None,
        on_finish: Optional[Callable] = None,
    ):
        """Reproduce un texto completo de forma asíncrona usando el pipeline con prefetch de frases."""
        if not text or not self.clean_text_for_speech(text):
            if on_finish:
                try:
                    on_finish()
                except Exception as e:
                    print(f"[TTS] on_finish error: {e}")
            return
        session = self.start_stream(voice=voice, on_first_audio=on_start, on_finish=on_finish)
        session.push(text)
        session.finish()

    def _speak_worker(
        self,
        text: str,
        voice: Optional[str] = None,
        on_start: Optional[Callable] = None,
        on_finish: Optional[Callable] = None,
    ):
        """Método síncrono retrocompatible (usado en tests y modo oneshot)."""
        if not text or not self.clean_text_for_speech(text):
            if on_finish:
                on_finish()
            return
        session = self.start_stream(voice=voice, on_first_audio=on_start, on_finish=on_finish)
        session.push(text)
        session.finish()
        session.wait()

    def _synthesize_sentence(
        self,
        sentence: str,
        voice: str,
        cancel_event: threading.Event,
    ) -> Optional[str]:
        """
        Sintetiza una sola frase a un archivo temporal en /dev/shm según el motor configurado.
        Devuelve la ruta del archivo de audio o None si se debe usar espeak-ng directo.
        """
        if cancel_event.is_set():
            return None

        engine = (self.engine or "edge-tts").lower()

        if engine == "espeak-ng":
            return None

        if engine == "piper":
            wav_path = self._generate_piper_tts(sentence)
            if wav_path:
                return wav_path
            # Fallback a edge-tts si piper falla
            mp3_path = self._generate_edge_tts_file(sentence, voice)
            if mp3_path:
                return mp3_path
            return None

        # Default: edge-tts con timeout estricto -> fallback a piper -> fallback a espeak-ng
        mp3_path = self._generate_edge_tts_file(sentence, voice)
        if mp3_path:
            return mp3_path

        if self.piper_model:
            wav_path = self._generate_piper_tts(sentence)
            if wav_path:
                return wav_path

        return None

    def _generate_edge_tts_file(self, text: str, voice: str) -> Optional[str]:
        tmp_dir = _get_fast_temp_dir()
        fd, output_path = tempfile.mkstemp(prefix="cina_tts_", suffix=".mp3", dir=tmp_dir)
        os.close(fd)
        if self._generate_edge_tts(text, voice, output_path):
            if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                return output_path
        self._safe_remove(output_path)
        return None

    def _generate_edge_tts(self, text: str, voice: str, output_path: str) -> bool:
        """Usa edge-tts sobre el event loop persistente con timeout estricto."""
        try:
            import edge_tts

            async def _run():
                communicate = edge_tts.Communicate(text, voice, rate=self.rate, volume=self.volume)
                await asyncio.wait_for(communicate.save(output_path), timeout=self.tts_timeout)

            loop = self._ensure_async_loop()
            future = asyncio.run_coroutine_threadsafe(_run(), loop)
            future.result(timeout=self.tts_timeout + 1.0)
            return True
        except Exception as e:
            print(f"[TTS] Error o timeout en edge-tts: {e}")
            return False

    def _generate_piper_tts(self, text: str) -> Optional[str]:
        """
        Sintetizador neuronal local. Usa la voz Piper precargada en proceso (~0.3 s por frase);
        sólo si el módulo no está disponible recurre al binario `piper` (que recarga el modelo cada vez).
        """
        voice = self._get_piper_voice()
        if voice is not None:
            import wave

            from piper import SynthesisConfig

            tmp_dir = _get_fast_temp_dir()
            fd, wav_path = tempfile.mkstemp(prefix="cina_piper_", suffix=".wav", dir=tmp_dir)
            os.close(fd)
            try:
                syn_cfg = SynthesisConfig(
                    length_scale=_rate_to_length_scale(self.rate),
                    volume=_volume_to_factor(self.volume),
                )
                with wave.open(wav_path, "wb") as wav_file:
                    voice.synthesize_wav(text, wav_file, syn_config=syn_cfg)
                if os.path.getsize(wav_path) > 100:
                    return wav_path
            except Exception as e:
                print(f"[TTS] Error en Piper (in-process): {e}")
            self._safe_remove(wav_path)
            return None

        piper_bin = shutil.which("piper")
        if not piper_bin or not self.piper_model or not os.path.exists(self.piper_model):
            return None

        tmp_dir = _get_fast_temp_dir()
        fd, wav_path = tempfile.mkstemp(prefix="cina_piper_", suffix=".wav", dir=tmp_dir)
        os.close(fd)
        try:
            res = subprocess.run(
                [piper_bin, "--model", self.piper_model, "--output_file", wav_path],
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=4.0,
            )
            if res.returncode == 0 and os.path.exists(wav_path) and os.path.getsize(wav_path) > 100:
                return wav_path
        except Exception as e:
            print(f"[TTS] Error ejecutando piper: {e}")
        self._safe_remove(wav_path)
        return None

    def _speak_espeak(self, text: str, cancel_event: Optional[threading.Event] = None):
        """Fallback local offline con espeak-ng."""
        if cancel_event and cancel_event.is_set():
            return
        if shutil.which("espeak-ng"):
            cmd = ["espeak-ng", "-v", "es", text]
        elif shutil.which("espeak"):
            cmd = ["espeak", "-v", "es", text]
        else:
            print("[TTS] No hay sintetizador de voz disponible.")
            return

        with self._lock:
            if cancel_event and cancel_event.is_set():
                return
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.current_process = proc

        try:
            proc.wait()
        finally:
            with self._lock:
                if self.current_process is proc:
                    self.current_process = None

    def _resolve_player_command(self, audio_file: str) -> Tuple[Optional[List[str]], Optional[str]]:
        """
        Resuelve el comando del reproductor respetando audio_player configurado.
        Devuelve (cmd, extra_temp_file_to_cleanup).
        """
        preferred = (self.audio_player or "auto").lower()
        candidates = ["pw-play", "mpv", "ffplay", "paplay"]
        if preferred != "auto" and preferred in candidates:
            candidates = [preferred] + [c for c in candidates if c != preferred]

        for player in candidates:
            if not shutil.which(player):
                continue
            if player == "pw-play":
                return ["pw-play", audio_file], None
            if player == "mpv":
                return ["mpv", "--no-video", "--really-quiet", audio_file], None
            if player == "ffplay":
                return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", audio_file], None
            if player == "paplay":
                if audio_file.endswith(".mp3") and shutil.which("ffmpeg"):
                    fd, wav_file = tempfile.mkstemp(prefix="cina_wav_", suffix=".wav", dir=_get_fast_temp_dir())
                    os.close(fd)
                    res = subprocess.run(
                        ["ffmpeg", "-y", "-i", audio_file, wav_file],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5.0,
                    )
                    if res.returncode == 0 and os.path.exists(wav_file):
                        return ["paplay", wav_file], wav_file
                    self._safe_remove(wav_file)
                return ["paplay", audio_file], None

        return None, None

    def _play_audio_file(self, audio_file: str, cancel_event: Optional[threading.Event] = None):
        if cancel_event and cancel_event.is_set():
            return
        player_cmd, extra_cleanup = self._resolve_player_command(audio_file)
        if not player_cmd:
            print("[TTS] No se encontró ningún reproductor de audio compatible (pw-play, mpv, ffplay, paplay).")
            return

        proc = None
        try:
            with self._lock:
                if cancel_event and cancel_event.is_set():
                    return
                proc = subprocess.Popen(player_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.current_process = proc

            proc.wait()
        except Exception as e:
            print(f"[TTS] Error durante la reproducción de audio: {e}")
        finally:
            with self._lock:
                if proc is not None and self.current_process is proc:
                    self.current_process = None
            self._safe_remove(extra_cleanup)

    def _play_audio(self, audio_file: str):
        """Método retrocompatible para reproducir un archivo de audio."""
        self._play_audio_file(audio_file, cancel_event=None)
