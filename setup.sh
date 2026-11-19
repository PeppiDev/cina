#!/usr/bin/env bash
# ==============================================================================
# CINA - Instalador Universal para Linux (Fedora, Ubuntu, Debian, Arch, openSUSE, NixOS)
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"

# En NixOS el flujo nativo recomendado es flake.nix (evita conflictos con wheels FHS en pip)
if [ -f /etc/NIXOS ] && [ "${1:-}" != "--force-venv" ]; then
    echo "============================================================"
    echo "❄️ NixOS detectado"
    echo "============================================================"
    echo "En NixOS se recomienda usar el flake nativo incluido (flake.nix):"
    echo "  - Ejecutar directamente:     nix run ."
    echo "  - Entorno de desarrollo:     nix develop"
    echo "  - Instalar en tu perfil:     nix profile install ."
    echo "  - O añadir como input en tu configuration.nix / Home Manager."
    echo ""
    echo "Si deseas forzar la instalación tradicional con venv (requiere nix-ld):"
    echo "  ./setup.sh --force-venv"
    echo "============================================================"
    exit 0
fi

echo "============================================================"
echo "Iniciando instalación de CINA Voice Reader"
echo "   Directorio base: $DIR"
echo "============================================================"

# 1. Asegurar directorios de usuario
mkdir -p "$BIN_DIR" "$APP_DIR" "$HOME/.config/cina"

# 2. Comprobar utilidades del sistema recomendadas
echo "Verificando herramientas del sistema..."
MISSING_PKGS=""

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        echo "   [!] Falta comando: $1"
        MISSING_PKGS="$MISSING_PKGS $1"
    else
        echo "   [OK] Detectado: $1 ($(command -v "$1"))"
    fi
}

check_cmd python3
check_cmd spectacle || check_cmd grim || check_cmd scrot || true
check_cmd pw-play || check_cmd paplay || check_cmd mpv || check_cmd ffplay || true

# 3. Crear Entorno Virtual de Python
VENV_DIR="$DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creando entorno virtual Python en $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
else
    echo "Entorno virtual existente detectado."
fi

# 4. Instalar / actualizar dependencias de Python
echo "Instalando dependencias de requirements.txt..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$DIR/requirements.txt"

# 5. Dar permisos de ejecución a los scripts
chmod +x "$DIR/main.py" "$DIR/cina-trigger" "$DIR/run.sh" "$DIR/install_kde_shortcut.sh"

# 6. Crear Enlaces Simbólicos Globales en ~/.local/bin
echo "Registrando comandos en $BIN_DIR..."
ln -sf "$DIR/cina-trigger" "$BIN_DIR/cina-trigger"
ln -sf "$DIR/run.sh" "$BIN_DIR/cina"

# 7. Crear Lanzadores .desktop para el Sistema
echo "Creando accesos de escritorio..."
cat > "$APP_DIR/cina.desktop" <<EOF
[Desktop Entry]
Name=Cina Voice Reader
Comment=Asistente de accesibilidad visual y lectura por voz en tiempo real
Exec=$BIN_DIR/cina
Icon=audio-volume-high
Type=Application
Terminal=false
Categories=Utility;Accessibility;
EOF
chmod +x "$APP_DIR/cina.desktop"

cat > "$APP_DIR/cina-trigger.desktop" <<EOF
[Desktop Entry]
Name=Cina Screen Reader Trigger
Comment=Disparador rápido de lectura visual por voz Cina
Exec=$BIN_DIR/cina-trigger
Icon=audio-volume-high
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
    echo "Detectados espacios en la ruta. Creando wrapper protector en $FIRST_PART..."
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

# 10. Verificación rápida de módulos
echo "Ejecutando verificación rápida..."
"$VENV_DIR/bin/python3" -c "
import cina
from cina.config import config_mgr
from cina.capture import ScreenCapture
from cina.tts_service import TTSService
print('   -> Todos los módulos de Cina se importaron correctamente.')
"

echo "============================================================"
echo "Instalación completada con éxito."
echo ""
echo "Comandos disponibles:"
echo "   - Iniciar la aplicación:  cina"
echo "   - Disparar lectura/voz:   cina-trigger"
echo "   - Detener audio:          cina-trigger --stop"
echo ""
echo "Para asignar el atajo en KDE Plasma:"
echo "   Preferencias del Sistema -> Atajos de teclado -> Añadir nuevo"
echo "   Orden: cina-trigger"
echo "============================================================"
