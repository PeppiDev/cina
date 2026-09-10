# AGENT GUIDE & SYSTEM ARCHITECTURE: CINA

Este documento está diseñado específicamente para que **agentes de Inteligencia Artificial** (o desarrolladores automatizados) puedan clonar, comprender, configurar, probar y desplegar este repositorio en cualquier distribución de Linux sin intervención manual.

---

## 1. Propósito del Proyecto

**Cina** es un asistente de audio en tiempo real para Linux que:
1. Permite capturar la pantalla activa de forma **100% silenciosa e invisible** (sin flash, sin sonido de obturador, sin ventana emergente ni robar el foco).
2. Procesa la imagen mediante **Google Gemini Multimodal Vision**.
3. Detecta preguntas, ejercicios, cuestionarios, exámenes de opción múltiple o código.
4. Resuelve y sintetiza la respuesta adecuada en un formato preparado para locución directa.
5. Reproduce la respuesta al usuario mediante **voz neuronal humana (TTS)** en tiempo real a través de auriculares o altavoces.

---

## 2. Árbol del Repositorio

```
.
├── cina/
│   ├── __init__.py           # Metadata del paquete
│   ├── config.py             # Configuración persistente (~/.config/cina/config.json)
│   ├── capture.py            # Captura universal silenciosa (Spectacle -m -b -n, Grim, Scrot, Pillow)
│   ├── gemini_service.py     # Integración Gemini con auto-fallback de cuota (429) y rotación de claves
│   ├── tts_service.py        # Síntesis Edge-TTS con voces neuronales + fallback espeak-ng + pw-play
│   ├── ipc_service.py        # Servidor y cliente de socket Unix (/tmp/cina_<uid>.sock)
│   ├── hotkey_service.py     # Gestor dual de atajos globales (IPC + pynput listener)
│   └── gui.py                # Interfaz gráfica moderna en Tkinter (tema oscuro estilo KDE Breeze)
├── main.py                   # Entrypoint único con modos GUI, headless e IPC trigger
├── cina-trigger              # Script disparador de alta velocidad para atajos globales
├── run.sh                    # Lanzador con auto-detección y arranque de entorno virtual
├── setup.sh                  # Script de despliegue y configuración automática en 1 paso
├── install_kde_shortcut.sh   # Instalador de accesos .desktop en KDE Plasma
├── requirements.txt          # Dependencias de Python
├── README.md                 # Documentación para usuarios humanos
├── AGENT.md                  # Este documento
└── .gitignore                # Reglas de exclusión de git
```

---

## 3. Despliegue en 1 Paso (Para Agentes)

Para clonar y desplegar en una nueva máquina Linux:

```bash
git clone https://github.com/PeppiDev/cina.git
cd cina
chmod +x setup.sh
./setup.sh
```

El script `setup.sh`:
- Detecta y valida las utilidades del sistema (`spectacle`, `grim`, `scrot`, `pw-play`, `paplay`).
- Crea un `venv` aislado con Python 3.
- Instala todas las dependencias de `requirements.txt`.
- Registra `cina` y `cina-trigger` en `~/.local/bin/` (incluido en el `$PATH` del usuario).
- Crea los accesos `.desktop` en `~/.local/share/applications/`.
- Configura protecciones anti-rotura si el repositorio se ubica en carpetas con espacios en su ruta.

---

## 4. Configuración y Claves de API (`config.json`)

El archivo de configuración reside en:
```
~/.config/cina/config.json
```

### Esquema:
```json
{
  "api_key": "AIzaSyA..., AIzaSyB...",
  "model": "gemini-3.7-flash",
  "voice": "es-ES-AlvaroNeural",
  "tts_engine": "edge-tts",
  "speech_rate": "+0%",
  "speech_volume": "+0%",
  "hotkey": "<ctrl>+<alt>+s",
  "capture_backend": "auto"
}
```

### Características Clave de la Configuración:
1. **Multi-Key Rotation**: Se pueden ingresar varias claves separadas por coma en `"api_key"`. Si una clave alcanza el límite temporal de cuota (HTTP 429), rota instantáneamente a la siguiente clave sin interrumpir al usuario.
2. **Variable de Entorno**: Si `"api_key"` está vacío, toma automáticamente `GEMINI_API_KEY` del entorno.

---

## 5. Estrategia de Manejo de Cuota (Error 429 Rate Limit)

Google AI Studio impone cuotas en la capa gratuita (e.g., 15-20 RPM en modelos grandes):
- **Modelo Primario**: `gemini-3.7-flash` (alta agudeza visual y razonamiento).
- **Fallback Automático**: Si el modelo primario arroja 429 (`RESOURCE_EXHAUSTED`), el servicio conmuta automáticamente a **`gemini-3.5-flash-lite`** (que posee cuota separada de hasta 1500 RPD y responde en ~0.7 segundos).
- **Traducción Auditiva Limpia**: Si todas las claves y modelos se agotan, la app nunca lee JSON crudo al usuario; extrae los segundos restantes del mensaje de Google y emite por voz:
  *"Límite de solicitudes alcanzado en Gemini. Por favor espera X segundos antes de volver a consultar..."*

---

## 6. Arquitectura de Disparo (IPC Socket)

En Wayland (KDE Plasma, GNOME, Sway), la captura global de teclas está restringida por seguridad. La solución arquitectónica implementada es un **Socket Unix de Dominio**:
- **Ruta del Socket**: `/tmp/cina_<uid>.sock`
- **Protocolo**: Líneas de texto plano (`TRIGGER\n`, `PING\n`, `STOP\n`).
- **Funcionamiento**:
  1. La aplicación principal abre el socket al iniciar.
  2. El atajo global del sistema operativo ejecuta `cina-trigger` (o `~/.local/bin/cina-trigger`).
  3. `cina-trigger` conecta al socket, envía `TRIGGER` y finaliza en < 5ms.
  4. La app en segundo plano ejecuta la captura en un hilo asíncrono sin bloquear la interfaz.
  5. **Modo Resiliente Standalone**: Si la app con GUI no está abierta en segundo plano, `cina-trigger` detecta la ausencia del socket y ejecuta la captura y respuesta de forma autónoma en modo consola/audio.

---

## 7. Verificación Headless (Comandos de Test para Agentes)

Para comprobar el correcto funcionamiento sin levantar interfaz gráfica:

```bash
# 1. Comprobar importación y componentes
./venv/bin/python3 -c "import cina; print('OK')"

# 2. Probar captura silenciosa
./venv/bin/python3 -c "
from cina.capture import ScreenCapture
cap = ScreenCapture()
p = cap.capture_silent()
print('Captura exitosa:', p)
"

# 3. Probar síntesis de voz y reproducción
./venv/bin/python3 -c "
from cina.tts_service import TTSService
tts = TTSService()
tts._speak_worker('Prueba de voz del sistema Cina.')
"

# 4. Probar disparo de un disparo completo (captura + IA + audio)
./venv/bin/python3 main.py --trigger
```

---

## 8. Integración de Atajos en Entornos de Escritorio

### KDE Plasma (Wayland / X11)
- Comando a registrar en *Preferencias del Sistema -> Atajos de teclado -> Añadir nuevo -> Orden o script*:
  ```bash
  cina-trigger
  ```
- Combinación recomendada: `Ctrl+Alt+S` o `Ctrl+'`.

### GNOME (Wayland / X11)
- *Configuración -> Teclado -> Atajos de teclado -> Atajos personalizados*:
  - Nombre: `Cina Trigger`
  - Orden: `cina-trigger`
  - Atajo: `Ctrl+Alt+S`

### Sway / Hyprland / i3
En el archivo de configuración del gestor de ventanas (`~/.config/hypr/hyprland.conf` o `~/.config/sway/config`):
```ini
bind = $mainMod, S, exec, cina-trigger
```
