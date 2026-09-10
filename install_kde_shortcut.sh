#!/usr/bin/env bash
# Registrador de atajo para KDE Plasma 6 / 5
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
Name=Cina Screen Audio Assistant (Disparador)
Comment=Captura silenciosa y respuestas por voz con IA
Exec=$BIN_DIR/cina-trigger
Icon=camera-photo
Type=Application
Terminal=false
NoDisplay=false
Categories=Utility;Accessibility;
X-KDE-Shortcuts=Ctrl+Alt+S
EOF

chmod +x "$DESKTOP_FILE"

# También crear el lanzador de la app principal para el menú de aplicaciones
APP_DESKTOP="$APP_DIR/cina.desktop"
cat > "$APP_DESKTOP" <<EOF
[Desktop Entry]
Name=Cina AI Assistant
Comment=Asistente de Pantalla y Audio con Google Gemini
Exec=$BIN_DIR/cina
Icon=camera-photo
Type=Application
Terminal=false
Categories=Utility;Accessibility;
EOF
chmod +x "$APP_DESKTOP"

echo "============================================================"
echo "✅ Lanzadores instalados con éxito:"
echo "   - Lanzador principal: $APP_DESKTOP"
echo "   - Disparador de atajo: $DESKTOP_FILE"
echo "============================================================"
echo ""
echo "Para asignar o cambiar el atajo en KDE Plasma:"
echo "1. Abre 'Preferencias del Sistema' en KDE."
echo "2. Ve a 'Atajos de teclado' -> 'Atajos personalizados' o 'Añadir nuevo'."
echo "3. Selecciona 'Cina Screen Audio Assistant (Disparador)' o escribe simplemente:"
echo "   cina-trigger"
echo "4. Asigna la combinación de teclas que desees (ej: Ctrl+Alt+S)."
echo "============================================================"

