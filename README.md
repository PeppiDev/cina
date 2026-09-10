# CINA - Asistente de Pantalla y Audio con IA (Google Gemini)

Aplicación universal para Linux (optimizada para Fedora / Nobara + KDE Plasma en Wayland y X11) que se ejecuta en segundo plano o minimizada, captura la pantalla de manera completamente silenciosa (sin flashes, sin sonido, sin popups ni perder el foco), analiza la imagen con la API multimodal de **Google Gemini** para detectar preguntas o ejercicios, y responde mediante **voz natural en tiempo real** a través de tus auriculares o altavoces.

---

## Características Principales

- **Captura 100% Silenciosa e Invisible**: Utiliza `spectacle -m -b -n` en KDE Plasma para capturar únicamente la pantalla activa de forma instantánea y nítida (o `grim` en Wayland / `scrot -z` en X11). Sin sonido de obturador, sin ventanas emergentes y sin notificaciones.
- **Doble Sistema de Atajo Global**:
  - Compatible con Wayland mediante socket IPC de alta velocidad (`cina-trigger`) enlazable a KDE Plasma Shortcuts o cualquier gestor de ventanas.
  - Escuchador de teclado global en segundo plano (`pynput`) para X11 o sesiones con permisos de entrada.
- **IA Multimodal Gemini**: Soporta `gemini-3.7-flash` y `gemini-3.5-flash-lite` con razonamiento visual especializado en exámenes, cuestionarios, opciones múltiples, problemas de lógica y código.
- **Manejo Inteligente de Cuotas (Error 429)**:
  - **Fallback automático**: Si el modelo primario alcanza el límite de cuota gratuita, conmuta automáticamente a `gemini-3.5-flash-lite` en milisegundos.
  - **Rotación de múltiples claves**: Permite configurar varias API Keys separadas por comas para alternar automáticamente.
  - **Mensajes de voz claros**: Si todas las cuotas se agotan, te avisa en lenguaje humano natural con el tiempo de espera restante sin leer código JSON.
- **Audio Neuronal Ultra-Realista**: Síntesis de voz en español con `edge-tts` (voces de España, México, Argentina, Colombia, Chile) y reproducción directa vía PipeWire (`pw-play`) o PulseAudio (`paplay`). Incluye fallback offline con `espeak-ng`.
- **Interfaz Gráfica Moderna**: Estilo oscuro Breeze Dark, estado en vivo, previsualización de la última captura y texto transcrito.

---

## Despliegue Rápido en 1 Paso

En cualquier sistema Linux (Fedora, Nobara, Ubuntu, Debian, Arch, openSUSE):

```bash
git clone https://github.com/PeppiDev/cina.git
cd cina
chmod +x setup.sh
./setup.sh
```

El script `setup.sh` configurará el entorno virtual, instalará las dependencias, creará los comandos globales `cina` y `cina-trigger` en `~/.local/bin` y registrará los lanzadores en tu entorno de escritorio.

---

## Cómo Iniciar la Aplicación

Simplemente ejecuta en tu terminal:

```bash
cina
```
*(O `./run.sh`, o desde el menú de aplicaciones de KDE buscando **"Cina AI Assistant"**).*

---

## Configuración

1. **API Key de Gemini**:
   - Obtén una clave gratuita en [Google AI Studio](https://aistudio.google.com/).
   - En la ventana de la aplicación, ingresa tu clave en el campo **Gemini API Key** y pulsa **Guardar** (puedes ingresar varias claves separadas por comas para rotación automática ante límites de cuota).
   - También puedes definir la variable de entorno: `export GEMINI_API_KEY="tu-clave"`.
2. **Selección de Voz y Modelo**:
   - Elige la voz que prefieras (ej: *Álvaro* de España, *Dalia* de México, *Tomás* de Argentina) y pruébala con **🔊 Probar Voz**.
   - El modelo por defecto es `gemini-3.7-flash` (con fallback transparente a `gemini-3.5-flash-lite`).

---

## Cómo Usar el Atajo de Teclado en KDE Plasma

1. Abre **Preferencias del Sistema** en KDE.
2. Ve a **Atajos de teclado** -> **Atajos personalizados** (o **Añadir nuevo** -> **Orden o script**).
3. En la orden o comando a ejecutar, escribe simplemente:
   ```bash
   cina-trigger
   ```
4. Asigna la combinación de teclas que quieras (por ejemplo, `Ctrl+Alt+S` o `Ctrl+'`).
5. ¡Listo! Cada vez que tengas una pregunta o examen en pantalla, presiona tu atajo. La aplicación capturará la pantalla en silencio y escucharás la respuesta por voz en tus auriculares.

---

## Modos de Ejecución Avanzados

- **Disparo manual desde terminal / script**:
  ```bash
  cina-trigger
  ```
- **Modo demonio sin interfaz gráfica (Headless)**:
  ```bash
  ./run.sh --headless
  ```

---

## Documentación para Agentes de IA

Para agentes autónomos que necesiten desplegar, auditar o extender esta aplicación, consulta la guía de arquitectura técnica en [AGENT.md](AGENT.md).
