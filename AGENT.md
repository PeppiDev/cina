# AGENT GUIDE & SYSTEM ARCHITECTURE: CINA

Guía técnica para agentes y desarrolladores sobre la arquitectura, despliegue (Linux tradicional y NixOS) y pruebas de **Cina**.

---

## 1. Propósito y Arquitectura General

**Cina** es una utilidad de **accesibilidad visual y lectura por voz en tiempo real** para Linux y NixOS:
1. Captura la pantalla activa directamente en memoria (`/dev/shm` o stdout) bajo demanda mediante atajo de teclado o GUI.
2. Analiza el contenido visual mediante **streaming con la API oficial de Google Gemini** (`google-genai` SDK con fallback nativo REST SSE).
3. Genera descripciones, resúmenes o respuestas técnicas concisas en español hablado, priorizando la idea principal en la primera oración.
4. Sintetiza y reproduce el audio frase por frase en paralelo mediante TTS neuronal (`edge-tts` / `piper`) o `espeak-ng`.

### Arquitectura de Inferencia
- Usa la **API oficial directa de Google Gemini** con clientes HTTP/2 Keep-Alive persistentes, envío directo de bytes JPEG (`Part.from_bytes`), control de `thinking_level` (`low`/`minimal`) y circuit breaker por `(clave, modelo)` para lograr el menor tiempo al primer audio (TTFA) y alta tolerancia a fallos de red o cuota.

---

## 2. Estructura del Repositorio

```
.
├── cina/
│   ├── __init__.py           # Metadata del paquete
│   ├── config.py             # Configuración atómica (~/.config/cina/config.json)
│   ├── capture.py            # Captura en RAM/memoria (Grim + monitor enfocado en Niri, Niri IPC, Spectacle, MSS, Scrot, Maim, Pillow)
│   ├── gemini_service.py     # Streaming SDK + REST SSE, circuit breaker (key, model), thinking_level
│   ├── tts_service.py        # Pipeline streaming frase por frase (Edge-TTS + Piper + eSpeak-NG)
│   ├── ipc_service.py        # Servidor y cliente de socket Unix (/tmp/cina_<uid>.sock)
│   ├── hotkey_service.py     # Gestor dual de atajos (IPC para Niri/Wayland + pynput en X11)
│   └── gui.py                # Interfaz gráfica en Tkinter (Breeze Dark, streaming en vivo)
├── tests/
│   └── test_cina.py          # Suite de pruebas unitarias e integración
├── flake.nix                 # Empaquetado nativo y devShell para NixOS
├── main.py                   # Entrypoint con lazy imports, GUI, demonio headless e IPC trigger/stop
├── cina-trigger              # Disparador rápido (<10 ms) directo al socket Unix (socat / python3 -S)
├── run.sh                    # Lanzador compatible con NixOS y venv tradicional
├── setup.sh                  # Instalador automático (detecta NixOS y recomienda flake.nix)
├── install_kde_shortcut.sh   # Instalador de accesos .desktop para KDE Plasma
├── requirements.txt          # Dependencias Python (distros tradicionales)
├── README.md                 # Documentación de usuario
└── AGENT.md                  # Este documento
```

---

## 3. Despliegue

### NixOS (Nativo con Flake)
```bash
nix run .        # Ejecutar directamente
nix develop      # Shell con Python/Tkinter/Pillow + herramientas runtime
```
El `flake.nix` envuelve `cina` y `cina-trigger` con `python3.withPackages` (`pillow`, `tkinter`, `requests` y paquetes opcionales `google-genai`, `edge-tts`, `pynput`, `mss` según el canal de nixpkgs) y añade al `PATH` `pipewire`, `socat`, `espeak-ng`, `grim`, `scrot` y `piper-tts` si está disponible.

### Distribuciones Tradicionales (Fedora, Ubuntu, Debian, Arch, openSUSE)
```bash
chmod +x setup.sh
./setup.sh
```

---

## 4. Configuración (`~/.config/cina/config.json`)

```json
{
  "api_key": "AIzaSyA..., AIzaSyB...",
  "model": "gemini-3.8-flash",
  "thinking_level": "low",
  "media_resolution": "high",
  "stream_audio": true,
  "request_timeout": 15.0,
  "tts_timeout": 5.0,
  "image_max_width": 1600,
  "image_quality": 82,
  "voice": "es-ES-AlvaroNeural",
  "tts_engine": "edge-tts",
  "piper_model": "",
  "speech_rate": "+0%",
  "speech_volume": "+0%",
  "hotkey": "<ctrl>+<alt>+s",
  "capture_backend": "auto",
  "audio_player": "auto"
}
```

- **Escritura atómica**: `ConfigManager.save()` usa archivo temporal + `fsync` + `os.replace` con permisos `0600`.
- **Rotación multi-clave**: rota primero entre claves sanas en el modelo primario antes de conmutar a modelos de respaldo.
- **Variable de entorno**: si `api_key` está vacío, lee `GEMINI_API_KEY`.

---

## 5. Estrategia de Fiabilidad y Failover (`GeminiService`)

- **Fail-Fast**: `HttpOptions(timeout=15000, retry_options=HttpRetryOptions(attempts=1))` evita el backoff interno lento del SDK para conmutar en `<100 ms`.
- **Circuit Breaker en memoria**:
  - `429` / `RESOURCE_EXHAUSTED`: cooldown exacto por `(key_idx, model)`.
  - `401` / `403` / `API_KEY_INVALID`: invalida la clave y rota inmediatamente.
  - `404`: cooldown al modelo.
  - `500/502/503/504` / timeout: cooldown corto (15 s) y failover inmediato.
  - `400` en `thinking_level`/`media_resolution`: reintento sin parámetros avanzados.
- **Cadena de modelos**: `gemini-3.8-flash` -> `gemini-3.7-flash` -> `gemini-3.5-flash-lite` (con promoción segura `MINIMAL` -> `LOW` en modelos no-lite).
- **Fallback REST SSE**: si `google-genai` no está instalado (por ejemplo en un canal antiguo de nixpkgs) o falla internamente, usa `:streamGenerateContent?alt=sse` sobre `requests.Session` persistente.

---

## 6. Protocolo IPC (`/tmp/cina_<uid>.sock`)

- Comandos: `TRIGGER\n` (`OK`), `PING\n` (`PONG`), `STOP\n` (`STOPPED`).
- `cina-trigger` envía el comando directamente con `socat` o `python3 -S` en `<10 ms`. Si no hay instancia activa, ejecuta `main.py --trigger` en modo standalone.

---

## 7. Verificación y Pruebas

```bash
python3 -m py_compile main.py cina/*.py tests/test_cina.py
python3 -m unittest discover -s tests -v
```
