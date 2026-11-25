# Cina — Lector Visual y Asistente por Voz para Linux y NixOS

Aplicación ligera de **accesibilidad visual y lectura por voz** para Linux (compatible con NixOS, Fedora, Nobara, Ubuntu, Debian, Arch y openSUSE en Wayland y X11). Permite analizar el contenido de la pantalla activa bajo demanda mediante un atajo de teclado y escuchar un resumen o explicación concisa por voz en tiempo real.

---

## Características Principales

- **Baja Latencia End-to-End (Streaming)**:
  - **Disparador IPC `<10 ms`**: `cina-trigger` envía la señal directa por socket Unix (`socat` o `python3 -S`) sin arrancar la interfaz gráfica.
  - **Captura en RAM (`/dev/shm`)**: Envío directo de bytes JPEG (`Part.from_bytes`) sin re-codificación ni archivos temporales en disco.
  - **Streaming multimodal + TTS frase por frase**: Empieza a reproducir la primera frase en cuanto llega del stream mientras pre-sintetiza las siguientes en paralelo.
  - **Conexiones persistentes + control de razonamiento**: Reutilización de clientes Keep-Alive y ajuste de `thinking_level` (`minimal`, `low`, `medium`).
- **Alta Fiabilidad (Zero-Hang & Circuit Breaker)**:
  - **Failover instantáneo (`<100 ms`)**: Timeouts estrictos y conmutación inmediata ante `429`, `503` o fallos transitorios de red.
  - **Circuit Breaker por `(clave, modelo)`**: Memoriza cooldowns y claves inválidas para no perder tiempo en llamadas consecutivas.
  - **Cadena de respaldo automática**: Rota primero entre múltiples API Keys en `gemini-3.8-flash` y conmuta a `gemini-3.7-flash` y `gemini-3.5-flash-lite`, con fallback nativo REST SSE.
  - **Cancelación limpia**: Un nuevo disparo o `cina-trigger --stop` detiene de inmediato cualquier síntesis o reproducción anterior sin solapamientos.
- **Soporte Multi-Entorno de Escritorio**:
  - Wayland: **Niri** (`grim` en monitor enfocado + fallback nativo `niri msg action screenshot-screen`), KDE Plasma (`spectacle`), Hyprland / Sway (`grim`), GNOME (`gnome-screenshot`).
  - X11: `mss`, `scrot`, `maim`, `pillow`.
- **Síntesis de Voz con Fallback Multi-Nivel**:
  - `piper` (por defecto): voz neuronal 100% local, cargada una vez en memoria (~0.2–0.4 s por frase). Modelos `.onnx` autodetectados en `~/.local/share/cina/voices/` (descárgalos de [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices), p. ej. `es_ES-davefx-medium` o `es_MX-claude-high`, con su `.onnx.json`).
  - `edge-tts` (voces neuronales online, ~1.3–2.5 s por frase; timeout de 5 s).
  - `espeak-ng` offline y reproducción vía PipeWire (`pw-play`), `mpv`, `ffplay` o `paplay`.
- **Interfaz Gráfica Dark (Tkinter)** y **Modo Demonio (`--headless`)** (con fallback automático a `--headless` en sesiones Wayland puras sin XWayland).

---

## Instalación en NixOS (Soporte Nativo con Flakes)

El repositorio incluye un [flake.nix](flake.nix) listo para usar con Python, `tkinter`, `pillow`, `requests`, `pipewire`, `socat`, `espeak-ng`, `grim` y `scrot` compilados nativamente para NixOS (sin depender de `venv` ni `nix-ld`).

### Ejecutar directamente
```bash
nix run github:PeppiDev/cina
```
O localmente tras clonar el repositorio:
```bash
git clone https://github.com/PeppiDev/cina.git
cd cina
nix run .
```

### Instalar en tu perfil o en `configuration.nix` / Home Manager
```nix
# En tu flake.nix de sistema:
inputs.cina.url = "github:PeppiDev/cina";

# En environment.systemPackages o home.packages:
inputs.cina.packages.${pkgs.system}.default
```
Esto expone los binarios `cina` y `cina-trigger` y registra los accesos `.desktop` automáticamente.

### Entorno de desarrollo en NixOS
```bash
nix develop
./run.sh            # Interfaz gráfica
./run.sh --headless # Demonio sin ventana
```

---

## Instalación en Otras Distribuciones Linux

En Fedora, Nobara, Ubuntu, Debian, Arch u openSUSE:

```bash
git clone https://github.com/PeppiDev/cina.git
cd cina
chmod +x setup.sh
./setup.sh
```

El script crea un entorno virtual aislado, instala las dependencias y registra `cina` y `cina-trigger` en `~/.local/bin`.

---

## Configuración

1. **Clave de API de Gemini**:
   - Obtén una clave en [Google AI Studio](https://aistudio.google.com/).
   - Ingresa una o varias claves separadas por comas en la aplicación o mediante la variable de entorno:
     ```bash
     export GEMINI_API_KEY="tu-clave"
     ```
2. **Modelo, Razonamiento y Voz**:
   - **Modelos**: `gemini-3.8-flash` (recomendado), `gemini-3.5-flash-lite` (máxima velocidad), `gemini-3.7-flash`.
   - **Razonamiento**: `low` (rápido), `minimal` (en `3.5-flash-lite`), `medium` (análisis técnico más profundo).
   - **Motor TTS**: `piper` (local; `piper_model` vacío = autodetectar en `~/.local/share/cina/voices`), `edge-tts` o `espeak-ng`.
   - **Prompt (botón 📝 Prompt)**: muestra el *system prompt* y el mensaje que se envían a Gemini; se pueden editar, guardar (`system_prompt` / `user_prompt` en el config) o restaurar a los valores por defecto. Se aplican desde la siguiente captura sin reiniciar.
   - **`hedge_delay`** (por defecto `2.5`): si el modelo primario no entrega el primer token en ese tiempo, se lanza en paralelo el respaldo (`flash-lite`) y gana el primero en responder. `0` lo desactiva.

---

## Atajos de Teclado de Escritorio

- **Niri (`~/.config/niri/config.kdl`)**:
  ```kdl
  spawn-at-startup "cina" "--headless"

  binds {
      Ctrl+Alt+S repeat=false { spawn "~/.nix-profile/bin/cina-trigger"; }
      Ctrl+Alt+X repeat=false { spawn "~/.nix-profile/bin/cina-trigger" "--stop"; }
  }
  ```
  - **El atajo debe registrarlo Niri** (bloque `binds`): así es global aunque la ventana de Cina esté oculta o sin foco. Cina no usa `pynput` en Niri, porque vía XWayland sólo recibe teclas con su ventana enfocada.
  - Usa la ruta del perfil Nix (`~/.nix-profile/bin/cina-trigger`): ese wrapper incluye `socat` y Python en su `PATH`.
  - **Captura en Niri**: usa `grim` apuntando automáticamente al monitor enfocado (`niri msg --json focused-output`) con fallback nativo a `niri msg action screenshot-screen` en `/dev/shm`.
  - **Interfaz gráfica en Niri**: si quieres abrir la ventana Tkinter (`cina`), asegúrate de tener `xwayland-satellite` habilitado en NixOS/Niri. Si no está disponible, `cina` cambia automáticamente a modo demonio (`--headless`).
- **KDE Plasma (Wayland / X11)**: *Preferencias del Sistema -> Atajos de teclado -> Añadir nuevo -> Orden o script*:
  - Disparar lectura por voz: `cina-trigger` (ej. `Ctrl+Alt+S`)
  - Detener audio: `cina-trigger --stop`
- **Hyprland / Sway**:
  ```ini
  bind = $mainMod, S, exec, cina-trigger
  bind = $mainMod SHIFT, S, exec, cina-trigger --stop
  ```
- **GNOME**: *Configuración -> Teclado -> Atajos personalizados* -> `cina-trigger`.

---

## Comandos y Pruebas

```bash
cina                  # Inicia la interfaz gráfica
./run.sh --headless   # Demonio en segundo plano sin GUI
cina-trigger          # Disparo de lectura visual por voz
cina-trigger --stop   # Detiene la reproducción activa
python3 -m unittest discover -s tests -v
```

Consulta la arquitectura técnica en [AGENT.md](AGENT.md).
