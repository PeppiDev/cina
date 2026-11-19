#!/usr/bin/env bash
# Registrador de atajo de escritorio para KDE Plasma 6 / 5
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRIGGER_SCRIPT="$DIR/cina-trigger"
chmod +x "$TRIGGER_SCRIPT"
chmod +x "$DIR/run.sh"

APP_DIR="$HOME/.local/share/applications"
BIN_DIR="$HOME/.local/bin"
mkdir -p "$APP_DIR" "$BIN_DIR"

# Crear enlaces simbólicos en ~/.local/bin para evitar problemas con espacios en rutas
ln -sf "$TRIGGER_SCRIPT" "$BIN_DIR/cina-trigger"
ln -sf "$DIR/run.sh" "$BIN_DIR/cina"

DESKTOP_FILE="$APP_DIR/cina-trigger.desktop"

cat > "$DESKTOP_FILE" <<EOF
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

chmod +x "$DESKTOP_FILE"

# Crear el lanzador de la app principal para el menú de aplicaciones
APP_DESKTOP="$APP_DIR/cina.desktop"
cat > "$APP_DESKTOP" <<EOF
[Desktop Entry]
Name=Cina Voice Reader
Comment=Asistente de accesibilidad visual y lectura por voz en tiempo real
Exec=$BIN_DIR/cina
Icon=audio-volume-high
Type=Application
Terminal=false
Categories=Utility;Accessibility;
EOF
chmod +x "$APP_DESKTOP"

echo "============================================================"
echo "Lanzadores instalados con éxito:"
echo "   - Lanzador principal: $APP_DESKTOP"
echo "   - Disparador de atajo: $DESKTOP_FILE"
echo "============================================================"
echo ""
echo "Para asignar o cambiar el atajo en KDE Plasma:"
echo "1. Abre 'Preferencias del Sistema' en KDE."
echo "2. Ve a 'Atajos de teclado' -> 'Atajos personalizados' o 'Añadir nuevo'."
echo "3. Selecciona 'Cina Screen Reader Trigger' o escribe simplemente:"
echo "   cina-trigger"
echo "4. Asigna la combinación de teclas que desees (ej: Ctrl+Alt+S)."
echo "============================================================"
