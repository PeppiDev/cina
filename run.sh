#!/usr/bin/env bash
# Lanzador principal de Cina
DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

# Verificar si el venv existe
if [ ! -d "$DIR/venv" ]; then
    echo "[Cina] Creando entorno virtual..."
    python3 -m venv "$DIR/venv"
    "$DIR/venv/bin/pip" install -r "$DIR/requirements.txt"
fi

exec "$DIR/venv/bin/python3" "$DIR/main.py" "$@"

