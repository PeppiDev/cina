import os
import io
import re
import json
import base64
import time
from typing import Optional, Tuple, List
from PIL import Image

SYSTEM_PROMPT = """
Eres un asistente de voz en tiempo real especializado en resolver dudas, preguntas y exámenes que aparecen en la pantalla del usuario.

Tu tarea:
1. Analiza cuidadosamente la captura de pantalla suministrada.
2. Determina si contiene preguntas, cuestionarios, exámenes tipo test, problemas matemáticos, ejercicios de programación o dudas teóricas.
3. SI DETECTAS PREGUNTAS:
   - Responde de forma directa, concisa y ordenada.
   - Si es opción múltiple: Indica el número de la pregunta o su tema, cuál es la opción correcta (ejemplo: "Opción B") y da una explicación breve de una o dos oraciones con la justificación.
   - Si es un problema de desarrollo o código: Da la solución directa y el razonamiento clave.
   - Prioriza la claridad para que el usuario entienda inmediatamente al escucharlo.
4. SI NO DETECTAS PREGUNTAS:
   - Di exactamente: "No se han detectado preguntas en la pantalla." y añade en una sola frase qué contenido o aplicación se está visualizando.
5. FORMATO OBLIGATORIO PARA AUDIO:
   - Tu respuesta será leída en voz alta por un sintetizador de voz (TTS) en los auriculares del usuario.
   - NO uses asteriscos (*), almohadillas (#), tablas markdown ni fórmulas complejas.
   - Usa un lenguaje hablado, natural, fluido y en español neutro o directo.
"""

class GeminiService:
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-3.7-flash"):
        self.raw_api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.api_keys = self._parse_keys(self.raw_api_key)
        self.current_key_idx = 0

        # Mapear modelos deprecados automáticamente
        if model in ("gemini-2.5-flash", "gemini-1.5-flash"):
            self.model = "gemini-3.7-flash"
        else:
            self.model = model

    def _parse_keys(self, key_str: str) -> List[str]:
        """Permite configurar varias claves separadas por coma, punto y coma o saltos de línea."""
        if not key_str:
            return []
        keys = [k.strip() for k in re.split(r"[,;\n\s]+", key_str) if k.strip()]
        return keys

    def set_api_key(self, api_key: str):
        self.raw_api_key = api_key
        self.api_keys = self._parse_keys(api_key)
        self.current_key_idx = 0

    def set_model(self, model: str):
        if model in ("gemini-2.5-flash", "gemini-1.5-flash"):
            self.model = "gemini-3.7-flash"
        else:
            self.model = model

    def _get_active_key(self) -> str:
        if not self.api_keys:
            return ""
        return self.api_keys[self.current_key_idx % len(self.api_keys)]

    def _rotate_key(self):
        if len(self.api_keys) > 1:
            self.current_key_idx = (self.current_key_idx + 1) % len(self.api_keys)
            print(f"[GeminiService] Rotando a la clave de API #{self.current_key_idx + 1}")

    def _get_model_candidates(self) -> List[str]:
        """Devuelve la lista de modelos a intentar en orden de prioridad."""
        models = [self.model]
        # Si el modelo primario es 3.7-flash, usar 3.5-flash-lite como fallback inmediato de cuota
        if self.model == "gemini-3.7-flash":
            models.append("gemini-3.5-flash-lite")
        elif self.model == "gemini-3.5-flash-lite":
            models.append("gemini-3.7-flash")
        return models

    def _format_429_error(self, raw_error: str) -> str:
        """Extrae el tiempo de espera en segundos y genera un mensaje limpio para locución y lectura."""
        match = re.search(r"retry in\s*([\d\.]+)\s*s", raw_error, re.IGNORECASE)
        if match:
            secs = int(float(match.group(1))) + 1
            return (
                f"Límite de solicitudes alcanzado en Gemini. "
                f"Por favor espera {secs} segundos para volver a capturar, "
                f"o añade una clave de respaldo en la configuración."
            )
        return (
            "Límite de solicitudes alcanzado en la API gratuita de Gemini. "
            "Por favor espera unos segundos antes de volver a capturar, "
            "o añade otra clave de API en ajustes."
        )

    def analyze_image(self, image_path: str) -> Tuple[bool, str, float]:
        """
        Envía la imagen a Gemini implementando:
        1. Rotación automática de claves de API si hay más de una disponible.
        2. Fallback automático a modelos alternativos (ej: gemini-3.5-flash-lite) si hay 429.
        3. Manejo limpio del error 429 sin leer JSON crudo en TTS.
        """
        if not self.api_keys:
            return False, "Por favor configura tu API Key de Gemini en la aplicación o mediante la variable de entorno GEMINI_API_KEY.", 0.0

        t0 = time.time()
        last_429_error = None
        models_to_try = self._get_model_candidates()
        keys_to_try = list(range(len(self.api_keys)))

        # Cargar imagen en memoria una sola vez
        with Image.open(image_path) as img:
            img_copy = img.copy()

        # Intentar con las claves disponibles
        for key_attempt in range(len(self.api_keys)):
            current_key = self._get_active_key()

            # Intentar con los modelos disponibles para esta clave
            for current_model in models_to_try:
                try:
                    from google import genai
                    from google.genai import types

                    client = genai.Client(api_key=current_key)
                    cfg = types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=1000
                    )

                    print(f"[GeminiService] Enviando petición (Modelo: {current_model}, Key #{self.current_key_idx + 1})...")
                    response = client.models.generate_content(
                        model=current_model,
                        contents=[img_copy, SYSTEM_PROMPT],
                        config=cfg
                    )

                    elapsed = time.time() - t0
                    text = response.text or "No se obtuvo respuesta de Gemini."

                    # Si se resolvió con modelo de respaldo, avisar sutilmente en consola
                    if current_model != self.model:
                        print(f"[GeminiService] Resuelto exitosamente con modelo de respaldo: {current_model}")

                    return True, text.strip(), elapsed

                except Exception as e_sdk:
                    err_str = str(e_sdk)
                    print(f"[GeminiService] Error en modelo {current_model} con key #{self.current_key_idx + 1}: {err_str}")

                    # Detectar si es error de cuota 429 / RESOURCE_EXHAUSTED
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                        last_429_error = err_str
                        # Probar el siguiente modelo o rotar clave
                        continue

                    # Si es error de autenticación u otro fallo no recuperable de esta clave
                    if "403" in err_str or "API_KEY_INVALID" in err_str or "PERMISSION_DENIED" in err_str:
                        break

            # Si la clave actual agotó cuota en todos los modelos, rotar a la siguiente clave
            if len(self.api_keys) > 1:
                self._rotate_key()

        # Si llegamos aquí y hubo error 429 de cuota en todos los intentos
        elapsed = time.time() - t0
        if last_429_error:
            friendly_msg = self._format_429_error(last_429_error)
            print(f"[GeminiService] Cuota agotada: {friendly_msg}")
            return False, friendly_msg, elapsed

        # Fallback final REST si no fue 429 pero falló el SDK
        try:
            import requests

            with open(image_path, "rb") as f:
                img_bytes = f.read()

            mime_type = "image/jpeg" if image_path.endswith((".jpg", ".jpeg")) else "image/png"
            b64_data = base64.b64encode(img_bytes).decode("utf-8")

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{models_to_try[0]}:generateContent?key={self._get_active_key()}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": SYSTEM_PROMPT},
                            {"inline_data": {"mime_type": mime_type, "data": b64_data}}
                        ]
                    }
                ],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800}
            }

            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            elapsed = time.time() - t0

            if resp.status_code == 200:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return True, text.strip(), elapsed
            elif resp.status_code == 429:
                friendly_msg = self._format_429_error(resp.text)
                return False, friendly_msg, elapsed
            else:
                return False, f"Error en la API de Gemini ({resp.status_code}): {resp.text}", elapsed

        except Exception as e_rest:
            elapsed = time.time() - t0
            return False, f"Error de conexión con Gemini: {e_rest}", elapsed
