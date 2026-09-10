#!/usr/bin/env bash
# ==============================================================================
# CINA - Instalador y Desplegador Universal para Linux
# Compatible con: Fedora, Nobara, Ubuntu, Debian, Arch Linux, openSUSE
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"

echo "============================================================"
echo "🚀 Iniciando instalación de CINA AI Assistant"
echo "   Directorio base: $DIR"
echo "============================================================"

# 1. Asegurar directorios de usuario
mkdir -p "$BIN_DIR" "$APP_DIR" "$HOME/.config/cina"

# 2. Comprobar e intentar instalar dependencias del sistema recomendadas
echo "📦 Verificando herramientas del sistema..."
MISSING_PKGS=""

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        echo "   ⚠️ Falta comando: $1"
        MISSING_PKGS="$MISSING_PKGS $1"
    else
        echo "   ✅ Detectado: $1 ($(which $1))"
    fi
}

check_cmd python3
check_cmd spectacle || check_cmd grim || check_cmd scrot
check_cmd pw-play || check_cmd paplay || check_cmd mpv || check_cmd ffplay

# 3. Crear Entorno Virtual de Python
VENV_DIR="$DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "🐍 Creando entorno virtual Python en $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
else
    echo "🐍 Entorno virtual existente detectado."
fi

# 4. Instalar / actualizar dependencias de Python
echo "📥 Instalando dependencias de requirements.txt..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$DIR/requirements.txt"

# 5. Dar permisos de ejecución a los scripts
chmod +x "$DIR/main.py" "$DIR/cina-trigger" "$DIR/run.sh" "$DIR/install_kde_shortcut.sh"

# 6. Crear Enlaces Simbólicos Globales en ~/.local/bin
echo "🔗 Registrando comandos en $BIN_DIR..."
ln -sf "$DIR/cina-trigger" "$BIN_DIR/cina-trigger"
ln -sf "$DIR/run.sh" "$BIN_DIR/cina"

# 7. Crear Lanzadores .desktop para el Sistema
echo "🖥️ Creando accesos de escritorio..."
cat > "$APP_DIR/cina.desktop" <<EOF
[Desktop Entry]
Name=Cina AI Assistant
Comment=Asistente de Pantalla y Audio con Google Gemini
Exec=$BIN_DIR/cina
Icon=camera-photo
Type=Application
Terminal=false
Categories=Utility;Accessibility;
EOF
chmod +x "$APP_DIR/cina.desktop"

cat > "$APP_DIR/cina-trigger.desktop" <<EOF
[Desktop Entry]
Name=CINA Screen Trigger
Comment=Disparador silencioso de captura y respuesta por voz CINA
Exec=$BIN_DIR/cina-trigger
Icon=camera-photo
Type=Application
Terminal=false
NoDisplay=false
Categories=Utility;Accessibility;
X-KDE-Shortcuts=Ctrl+Alt+S
EOF
chmod +x "$APP_DIR/cina-trigger.desktop"

# 8. Script puente anti-espacios para rutas con carpetas compuestas
if [[ "$DIR" =~ [[:space:]] ]]; then
    FIRST_PART="${DIR%% *}"
    echo "🛡️ Detectados espacios en la ruta. Creando wrapper protector en $FIRST_PART..."
    cat > "$FIRST_PART" <<EOF
#!/usr/bin/env bash
exec "$BIN_DIR/cina-trigger"
EOF
    chmod +x "$FIRST_PART"
fi

# 9. Actualizar bases de datos de escritorio si las herramientas existen
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$APP_DIR" 2>/dev/null || true
fi
if command -v kbuildsycoca6 &>/dev/null; then
    kbuildsycoca6 2>/dev/null || true
elif command -v kbuildsycoca5 &>/dev/null; then
    kbuildsycoca5 2>/dev/null || true
fi

# 10. Auto-test de verificación
echo "🧪 Ejecutando prueba de verificación rápida..."
"$VENV_DIR/bin/python3" -c "
import cina
from cina.config import config_mgr
from cina.capture import ScreenCapture
from cina.tts_service import TTSService
print('   -> Todos los módulos de Cina se importaron correctamente.')
"

echo "============================================================"
echo "🎉 ¡Instalación completada con éxito!"
echo ""
echo "Comandos disponibles:"
echo "   - Iniciar la aplicación:  cina"
echo "   - Disparar captura/audio: cina-trigger"
echo ""
echo "Para asignar el atajo en KDE Plasma:"
echo "   Preferencias del Sistema -> Atajos de teclado -> Añadir nuevo"
echo "   Orden: cina-trigger"
echo "============================================================"
