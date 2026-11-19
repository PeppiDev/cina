import base64
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple

SYSTEM_PROMPT = """
Eres un asistente de accesibilidad visual y lectura por voz en tiempo real, optimizado para claridad y baja latencia. Interpretas lo que se muestra en pantalla y lo explicas en lenguaje hablado natural.

Reglas:
1. Analiza el contenido visible en pantalla (documentos, interfaces, código, terminales, gráficos o consultas técnicas).
2. Si hay una consulta técnica, error de código o problema visible, empieza en la primera oración con la conclusión o diagnóstico principal y añade luego 1 o 2 frases breves de contexto.
3. Si hay elementos numerados relevantes, mantenlos en orden conservando su numeración.
4. Si se trata de contenido general (artículo, documento o aplicación), resume en 1 o 2 frases claras la información principal visible.
5. Formato obligatorio para voz (TTS): escribe en español natural y fluido, sin asteriscos (*), almohadillas (#), backticks (`), guiones de lista, tablas ni LaTeX.
""".strip()

DEPRECATED_MODEL_MAP = {
    "gemini-2.5-flash": "gemini-3.8-flash",
    "gemini-2.5-pro": "gemini-3.8-flash",
    "gemini-2.0-flash": "gemini-3.8-flash",
    "gemini-1.5-flash": "gemini-3.8-flash",
    "gemini-1.5-pro": "gemini-3.8-flash",
}

DEFAULT_MODEL = "gemini-3.8-flash"
FALLBACK_POOL = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash-lite")


@dataclass
class AnalysisResult:
    success: bool
    text: str
    elapsed: float
    ttft: float = 0.0
    model_used: str = ""
    key_index: int = 0
    error_type: str = ""


class GeminiService:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        thinking_level: str = "low",
        media_resolution: str = "high",
        timeout: float = 15.0,
    ):
        self.raw_api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.api_keys: List[str] = self._parse_keys(self.raw_api_key)
        self.current_key_idx = 0

        self.model = self._normalize_model(model)
        self.thinking_level = (thinking_level or "low").strip().lower()
        self.media_resolution = (media_resolution or "high").strip().lower()
        self.timeout = max(3.0, float(timeout))

        # Pool persistente de clientes SDK (reutiliza conexiones HTTP/2 + TLS) y sesión REST
        self._lock = threading.Lock()
        self._clients: Dict[str, object] = {}
        self._rest_session = None

        # Circuit breaker por (key_idx, model), claves inválidas y modelos caídos
        self._cooldowns: Dict[Tuple[int, str], float] = {}
        self._invalid_keys: Set[int] = set()
        self._unavailable_models: Dict[str, float] = {}

    @staticmethod
    def _normalize_model(model: Optional[str]) -> str:
        if not model:
            return DEFAULT_MODEL
        m = model.strip()
        return DEPRECATED_MODEL_MAP.get(m, m)

    def _parse_keys(self, key_str: str) -> List[str]:
        """Permite configurar varias claves separadas por coma, punto y coma o saltos de línea."""
        if not key_str:
            return []
        return [k.strip() for k in re.split(r"[,;\n\s]+", key_str) if k.strip()]

    def set_api_key(self, api_key: str):
        with self._lock:
            self.raw_api_key = api_key
            self.api_keys = self._parse_keys(api_key)
            self.current_key_idx = 0
            self._clients.clear()
            self._cooldowns.clear()
            self._invalid_keys.clear()

    def set_model(self, model: str):
        with self._lock:
            self.model = self._normalize_model(model)

    def set_thinking_level(self, level: str):
        with self._lock:
            self.thinking_level = (level or "low").strip().lower()

    def set_media_resolution(self, resolution: str):
        with self._lock:
            self.media_resolution = (resolution or "high").strip().lower()

    def warmup(self):
        """Pre-inicializa el cliente SDK en segundo plano para reducir latencia del primer disparo."""
        if not self.api_keys:
            return
        try:
            self._get_sdk_client(self.api_keys[0])
        except Exception as e:
            print(f"[GeminiService] Warmup omitido: {e}")

    def _get_active_key(self) -> str:
        if not self.api_keys:
            return ""
        return self.api_keys[self.current_key_idx % len(self.api_keys)]

    def _rotate_key(self):
        if len(self.api_keys) > 1:
            self.current_key_idx = (self.current_key_idx + 1) % len(self.api_keys)
            print(f"[GeminiService] Rotando a la clave de API #{self.current_key_idx + 1}")

    def _get_model_candidates(self) -> List[str]:
        """Devuelve la cadena de modelos en orden de prioridad sin duplicados."""
        models = [self.model]
        for fallback in FALLBACK_POOL:
            if fallback not in models:
                models.append(fallback)
        return models

    def _resolve_thinking_level(self, model: str) -> Optional[str]:
        """
        Ajusta thinking_level de forma segura por modelo:
        - gemini-3.8-flash / gemini-3.7-flash NO admiten MINIMAL (solo LOW, MEDIUM, HIGH).
        - gemini-3.5-flash-lite admite MINIMAL, LOW, MEDIUM, HIGH.
        """
        lvl = self.thinking_level.upper()
        if lvl not in ("MINIMAL", "LOW", "MEDIUM", "HIGH"):
            lvl = "LOW"

        is_lite = "lite" in model.lower()
        if lvl == "MINIMAL" and not is_lite:
            return "LOW"
        return lvl

    def _get_sdk_client(self, api_key: str):
        """Obtiene o crea un cliente genai.Client persistente con timeout estricto y fail-fast."""
        with self._lock:
            cached = self._clients.get(api_key)
            if cached is not None:
                return cached

        from google import genai
        from google.genai import types

        timeout_ms = int(self.timeout * 1000)
        http_opts_kwargs = {"timeout": timeout_ms}

        # Desactivar el backoff exponencial lento interno del SDK (1s+2s+4s+8s)
        # para que nuestro failover multi-clave/multi-modelo actúe en <100ms.
        if hasattr(types, "HttpRetryOptions"):
            try:
                http_opts_kwargs["retry_options"] = types.HttpRetryOptions(attempts=1)
            except Exception:
                pass

        try:
            http_options = types.HttpOptions(**http_opts_kwargs)
            client = genai.Client(api_key=api_key, http_options=http_options)
        except Exception:
            # Fallback compatible si una versión del SDK difiere en HttpOptions
            client = genai.Client(api_key=api_key)

        with self._lock:
            self._clients[api_key] = client
        return client

    def _get_rest_session(self):
        with self._lock:
            if self._rest_session is None:
                import requests
                session = requests.Session()
                session.headers.update({"Content-Type": "application/json"})
                self._rest_session = session
            return self._rest_session

    def _build_sdk_config(self, types_mod, model: str, include_advanced: bool = True):
        """Construye GenerateContentConfig optimizado para baja latencia y alta fiabilidad."""
        cfg_kwargs = {
            "system_instruction": SYSTEM_PROMPT,
            "temperature": 0.15,
            "max_output_tokens": 2048,
        }

        if include_advanced:
            # 1. ThinkingConfig: reducir latencia de razonamiento oculto (LOW / MINIMAL)
            thinking_lvl = self._resolve_thinking_level(model)
            if thinking_lvl and hasattr(types_mod, "ThinkingConfig"):
                try:
                    lvl_value = thinking_lvl
                    if hasattr(types_mod, "ThinkingLevel") and hasattr(types_mod.ThinkingLevel, thinking_lvl):
                        lvl_value = getattr(types_mod.ThinkingLevel, thinking_lvl)
                    cfg_kwargs["thinking_config"] = types_mod.ThinkingConfig(thinking_level=lvl_value)
                except Exception:
                    pass

            # 2. MediaResolution: equilibrio entre nitidez OCR y tokens visuales
            if hasattr(types_mod, "MediaResolution"):
                res_map = {
                    "low": "MEDIA_RESOLUTION_LOW",
                    "medium": "MEDIA_RESOLUTION_MEDIUM",
                    "high": "MEDIA_RESOLUTION_HIGH",
                }
                attr = res_map.get(self.media_resolution, "MEDIA_RESOLUTION_HIGH")
                if hasattr(types_mod.MediaResolution, attr):
                    cfg_kwargs["media_resolution"] = getattr(types_mod.MediaResolution, attr)

        return types_mod.GenerateContentConfig(**cfg_kwargs)

    @staticmethod
    def _classify_error(err_str: str) -> Tuple[str, float]:
        """
        Clasifica el error y devuelve (tipo, cooldown_segundos).
        Tipos: 'rate_limit', 'auth', 'not_found', 'invalid_arg', 'transient', 'unknown'
        """
        low = err_str.lower()
        if "429" in err_str or "resource_exhausted" in low or "quota" in low or "rate limit" in low:
            match = re.search(r"retry in\s*([\d\.]+)\s*s", err_str, re.IGNORECASE)
            secs = float(match.group(1)) + 1.0 if match else 35.0
            return "rate_limit", max(5.0, min(secs, 300.0))

        if "401" in err_str or "403" in err_str or "api_key_invalid" in low or "permission_denied" in low or "unauthenticated" in low:
            return "auth", 3600.0

        if "404" in err_str or "not_found" in low or "not found" in low:
            return "not_found", 600.0

        if "400" in err_str or "invalid_argument" in low:
            return "invalid_arg", 0.0

        if any(tok in low for tok in ("500", "502", "503", "504", "unavailable", "overloaded", "deadline", "timeout", "timed out", "connection", "reset by peer", "eof")):
            return "transient", 15.0

        return "unknown", 10.0

    def _record_failure(self, key_idx: int, model: str, err_type: str, cooldown_secs: float):
        now = time.monotonic()
        with self._lock:
            if err_type == "auth":
                self._invalid_keys.add(key_idx)
            elif err_type == "not_found":
                self._unavailable_models[model] = now + cooldown_secs
            elif cooldown_secs > 0:
                self._cooldowns[(key_idx, model)] = now + cooldown_secs

    def _build_candidate_plan(self) -> Tuple[List[Tuple[int, str]], float]:
        """
        Genera la lista ordenada de candidatos (key_idx, model) evitando claves inválidas
        y pares en cooldown activo.
        Prioridad: rotar primero claves en el modelo primario antes de degradar de modelo.
        Devuelve (candidatos_disponibles, menor_espera_si_todos_en_cooldown).
        """
        now = time.monotonic()
        models = self._get_model_candidates()
        num_keys = len(self.api_keys)
        if num_keys == 0:
            return [], 0.0

        # Orden circular de claves empezando por la clave activa
        key_order = [(self.current_key_idx + offset) % num_keys for offset in range(num_keys)]

        available: List[Tuple[int, str]] = []
        min_wait = float("inf")
        best_cooled_pair: Optional[Tuple[int, str]] = None

        with self._lock:
            for model in models:
                model_until = self._unavailable_models.get(model, 0.0)
                if model_until > now:
                    continue
                for k_idx in key_order:
                    if k_idx in self._invalid_keys:
                        continue
                    cd_until = self._cooldowns.get((k_idx, model), 0.0)
                    if cd_until <= now:
                        available.append((k_idx, model))
                    else:
                        wait = cd_until - now
                        if wait < min_wait:
                            min_wait = wait
                            best_cooled_pair = (k_idx, model)

        # Si todos están en cooldown pero al mejor le faltan <= 2s, intentar igualmente
        if not available and best_cooled_pair is not None and min_wait <= 2.0:
            available.append(best_cooled_pair)

        return available, (0.0 if min_wait == float("inf") else min_wait)

    def _format_429_error(self, raw_error: str = "", wait_secs: float = 0.0) -> str:
        """Extrae el tiempo de espera en segundos y genera un mensaje limpio para locución."""
        secs = int(wait_secs) + 1 if wait_secs > 0 else 0
        if secs <= 0 and raw_error:
            match = re.search(r"retry in\s*([\d\.]+)\s*s", raw_error, re.IGNORECASE)
            if match:
                secs = int(float(match.group(1))) + 1

        if secs > 0:
            return (
                f"Límite de solicitudes alcanzado en Gemini. "
                f"Por favor espera {secs} segundos antes de volver a capturar, "
                f"o añade otra clave de respaldo en ajustes."
            )
        return (
            "Límite de solicitudes alcanzado en la API de Gemini. "
            "Por favor espera unos segundos antes de volver a capturar, "
            "o añade otra clave de API en ajustes."
        )

    @staticmethod
    def _load_image_bytes(
        image_bytes: Optional[bytes] = None,
        image_path: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> Tuple[bytes, str]:
        if image_bytes is not None:
            resolved_mime = mime_type or "image/jpeg"
            return image_bytes, resolved_mime
        if not image_path or not os.path.exists(image_path):
            raise FileNotFoundError("No se proporcionó una imagen válida para analizar.")
        with open(image_path, "rb") as f:
            data = f.read()
        if not mime_type:
            low = image_path.lower()
            if low.endswith((".jpg", ".jpeg")):
                mime_type = "image/jpeg"
            elif low.endswith(".webp"):
                mime_type = "image/webp"
            else:
                mime_type = "image/png"
        return data, mime_type

    def _stream_via_sdk(
        self,
        api_key: str,
        model: str,
        img_bytes: bytes,
        mime_type: str,
        on_chunk: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        t0: float = 0.0,
    ) -> Tuple[str, float]:
        """Ejecuta streaming directo con google-genai SDK usando Part.from_bytes (cero re-codificación)."""
        from google.genai import types

        client = self._get_sdk_client(api_key)
        image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
        contents = [image_part, "Analiza esta pantalla y responde siguiendo las instrucciones del sistema."]

        def _run_stream(include_advanced: bool) -> Tuple[str, float]:
            cfg = self._build_sdk_config(types, model, include_advanced=include_advanced)
            stream = client.models.generate_content_stream(
                model=model,
                contents=contents,
                config=cfg,
            )
            chunks: List[str] = []
            ttft = 0.0
            for chunk in stream:
                if cancel_event and cancel_event.is_set():
                    break
                delta = getattr(chunk, "text", None)
                if delta:
                    if not chunks:
                        ttft = time.perf_counter() - t0
                    chunks.append(delta)
                    if on_chunk:
                        on_chunk(delta)
            return "".join(chunks).strip(), ttft

        try:
            return _run_stream(include_advanced=True)
        except Exception as e:
            err_str = str(e)
            err_type, _ = self._classify_error(err_str)
            # Si un parámetro avanzado (thinking_level / media_resolution) es rechazado, reintentar sin él
            if err_type == "invalid_arg" and any(k in err_str.lower() for k in ("thinking", "media_resolution", "resolution")):
                print(f"[GeminiService] Reintentando {model} sin parámetros avanzados...")
                return _run_stream(include_advanced=False)
            raise

    def _stream_via_rest(
        self,
        api_key: str,
        model: str,
        img_bytes: bytes,
        mime_type: str,
        on_chunk: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        t0: float = 0.0,
    ) -> Tuple[str, float]:
        """Fallback REST con SSE streaming (:streamGenerateContent?alt=sse) y conexión persistente."""
        session = self._get_rest_session()
        b64_data = base64.b64encode(img_bytes).decode("ascii")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key={api_key}"

        gen_cfg = {
            "temperature": 0.15,
            "maxOutputTokens": 2048,
        }
        thinking_lvl = self._resolve_thinking_level(model)
        if thinking_lvl:
            gen_cfg["thinkingConfig"] = {"thinkingLevel": thinking_lvl}

        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [
                {
                    "parts": [
                        {"inlineData": {"mimeType": mime_type, "data": b64_data}},
                        {"text": "Analiza esta pantalla y responde siguiendo las instrucciones del sistema."},
                    ]
                }
            ],
            "generationConfig": gen_cfg,
        }

        connect_timeout = min(5.0, self.timeout)
        with session.post(url, json=payload, timeout=(connect_timeout, self.timeout), stream=True) as resp:
            if resp.status_code != 200:
                body = resp.text[:500]
                raise RuntimeError(f"HTTP {resp.status_code}: {body}")

            chunks: List[str] = []
            ttft = 0.0
            for raw_line in resp.iter_lines(decode_unicode=True):
                if cancel_event and cancel_event.is_set():
                    break
                if not raw_line or not raw_line.startswith("data:"):
                    continue
                json_str = raw_line[5:].strip()
                if not json_str or json_str == "[DONE]":
                    continue
                try:
                    data = json.loads(json_str)
                    candidates = data.get("candidates") or []
                    if not candidates:
                        continue
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    for part in parts:
                        # Ignorar thought parts si el backend los marca
                        if part.get("thought"):
                            continue
                        delta = part.get("text")
                        if delta:
                            if not chunks:
                                ttft = time.perf_counter() - t0
                            chunks.append(delta)
                            if on_chunk:
                                on_chunk(delta)
                except json.JSONDecodeError:
                    continue

            return "".join(chunks).strip(), ttft

    def analyze_image_stream(
        self,
        image_bytes: Optional[bytes] = None,
        image_path: Optional[str] = None,
        mime_type: Optional[str] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> AnalysisResult:
        """
        Analiza la captura en streaming con:
        1. Conexión persistente Keep-Alive y envío directo de bytes JPEG (sin re-codificar).
        2. Thinking level optimizado para respuesta casi instantánea.
        3. Failover ultra-rápido multi-clave y multi-modelo con circuit breaker de cooldown.
        4. Fallback REST SSE si el SDK falla por error interno.
        """
        if not self.api_keys:
            return AnalysisResult(
                success=False,
                text="Por favor configura tu API Key de Gemini en la aplicación o mediante la variable de entorno GEMINI_API_KEY.",
                elapsed=0.0,
                error_type="no_key",
            )

        t0 = time.perf_counter()
        try:
            img_bytes, resolved_mime = self._load_image_bytes(image_bytes, image_path, mime_type)
        except Exception as e:
            return AnalysisResult(
                success=False,
                text=f"No se pudo leer la imagen capturada: {e}",
                elapsed=time.perf_counter() - t0,
                error_type="io_error",
            )

        candidates, min_wait = self._build_candidate_plan()
        if not candidates:
            elapsed = time.perf_counter() - t0
            if len(self._invalid_keys) == len(self.api_keys):
                return AnalysisResult(
                    success=False,
                    text="Las claves de API de Gemini configuradas no son válidas. Por favor revisa tu clave en ajustes.",
                    elapsed=elapsed,
                    error_type="auth",
                )
            msg = self._format_429_error(wait_secs=min_wait)
            return AnalysisResult(success=False, text=msg, elapsed=elapsed, error_type="rate_limit")

        last_429_error = ""
        last_error = ""
        last_error_type = "unknown"
        sdk_import_failed = False

        for key_idx, current_model in candidates:
            if cancel_event and cancel_event.is_set():
                return AnalysisResult(
                    success=False,
                    text="Solicitud cancelada.",
                    elapsed=time.perf_counter() - t0,
                    error_type="cancelled",
                )

            current_key = self.api_keys[key_idx]
            emitted_chunks: List[str] = []

            def _tracked_on_chunk(delta: str):
                emitted_chunks.append(delta)
                if on_chunk:
                    on_chunk(delta)

            print(f"[GeminiService] Streaming (Modelo: {current_model}, Key #{key_idx + 1}, Thinking: {self._resolve_thinking_level(current_model)})...")

            try:
                if not sdk_import_failed:
                    try:
                        text, ttft = self._stream_via_sdk(
                            api_key=current_key,
                            model=current_model,
                            img_bytes=img_bytes,
                            mime_type=resolved_mime,
                            on_chunk=_tracked_on_chunk,
                            cancel_event=cancel_event,
                            t0=t0,
                        )
                    except ImportError:
                        sdk_import_failed = True
                        text, ttft = self._stream_via_rest(
                            api_key=current_key,
                            model=current_model,
                            img_bytes=img_bytes,
                            mime_type=resolved_mime,
                            on_chunk=_tracked_on_chunk,
                            cancel_event=cancel_event,
                            t0=t0,
                        )
                else:
                    text, ttft = self._stream_via_rest(
                        api_key=current_key,
                        model=current_model,
                        img_bytes=img_bytes,
                        mime_type=resolved_mime,
                        on_chunk=_tracked_on_chunk,
                        cancel_event=cancel_event,
                        t0=t0,
                    )

                elapsed = time.perf_counter() - t0
                if cancel_event and cancel_event.is_set():
                    return AnalysisResult(
                        success=False,
                        text="Solicitud cancelada.",
                        elapsed=elapsed,
                        ttft=ttft,
                        model_used=current_model,
                        key_index=key_idx,
                        error_type="cancelled",
                    )

                if text:
                    # Recordar clave sana para priorizarla en el siguiente disparo
                    self.current_key_idx = key_idx
                    if current_model != self.model:
                        print(f"[GeminiService] Resuelto con modelo de respaldo: {current_model} (TTFT: {ttft:.2f}s, Total: {elapsed:.2f}s)")
                    else:
                        print(f"[GeminiService] Completado (TTFT: {ttft:.2f}s, Total: {elapsed:.2f}s)")
                    return AnalysisResult(
                        success=True,
                        text=text,
                        elapsed=elapsed,
                        ttft=ttft,
                        model_used=current_model,
                        key_index=key_idx,
                    )

                # Respuesta vacía: intentar siguiente candidato
                last_error = "Gemini devolvió una respuesta vacía."
                last_error_type = "empty"
                continue

            except Exception as e:
                err_str = str(e)
                err_type, cooldown = self._classify_error(err_str)
                last_error = err_str
                last_error_type = err_type
                print(f"[GeminiService] Error ({err_type}) en {current_model} con Key #{key_idx + 1}: {err_str[:220]}")

                # Si ya empezamos a emitir texto por voz/UI y se cortó al final, preservar lo recibido
                if emitted_chunks:
                    return AnalysisResult(
                        success=True,
                        text="".join(emitted_chunks).strip(),
                        elapsed=time.perf_counter() - t0,
                        model_used=current_model,
                        key_index=key_idx,
                    )

                self._record_failure(key_idx, current_model, err_type, cooldown)
                if err_type == "rate_limit":
                    last_429_error = err_str

                # Si fue un fallo raro del SDK (no HTTP), intentar REST en este mismo par antes de pasar
                if err_type == "unknown" and not sdk_import_failed:
                    try:
                        text, ttft = self._stream_via_rest(
                            api_key=current_key,
                            model=current_model,
                            img_bytes=img_bytes,
                            mime_type=resolved_mime,
                            on_chunk=_tracked_on_chunk,
                            cancel_event=cancel_event,
                            t0=t0,
                        )
                        if text:
                            self.current_key_idx = key_idx
                            return AnalysisResult(
                                success=True,
                                text=text,
                                elapsed=time.perf_counter() - t0,
                                ttft=ttft,
                                model_used=current_model,
                                key_index=key_idx,
                            )
                    except Exception as e_rest:
                        r_type, r_cd = self._classify_error(str(e_rest))
                        self._record_failure(key_idx, current_model, r_type, r_cd)
                        if r_type == "rate_limit":
                            last_429_error = str(e_rest)
                        last_error = str(e_rest)
                        last_error_type = r_type

        elapsed = time.perf_counter() - t0
        if last_429_error:
            return AnalysisResult(
                success=False,
                text=self._format_429_error(last_429_error),
                elapsed=elapsed,
                error_type="rate_limit",
            )
        if last_error_type == "auth":
            return AnalysisResult(
                success=False,
                text="Error de autenticación en Gemini. Verifica que tu API Key sea válida y esté activa.",
                elapsed=elapsed,
                error_type="auth",
            )
        if last_error_type == "transient":
            return AnalysisResult(
                success=False,
                text="Los servidores de Gemini tardaron demasiado o no respondieron. Intenta capturar nuevamente.",
                elapsed=elapsed,
                error_type="transient",
            )
        return AnalysisResult(
            success=False,
            text="No se pudo obtener respuesta de Gemini en este momento. Revisa tu conexión e inténtalo de nuevo.",
            elapsed=elapsed,
            error_type=last_error_type,
        )

    def analyze_image(self, image_path: str) -> Tuple[bool, str, float]:
        """Wrapper síncrono retrocompatible que devuelve (success, text, elapsed)."""
        result = self.analyze_image_stream(image_path=image_path)
        return result.success, result.text, result.elapsed
