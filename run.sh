#!/usr/bin/env bash
# Lanzador principal de Cina (compatible con NixOS y distribuciones Linux tradicionales)
DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

# En NixOS / entornos Nix (nix develop, nix-shell) o cuando el Python activo ya tiene
# las dependencias instaladas, ejecutar directamente sin crear venv ni llamar a pip.
if [ "${CINA_USE_SYSTEM_PYTHON:-0}" = "1" ] || [ -n "${IN_NIX_SHELL:-}" ] || [ -f /etc/NIXOS ]; then
    if python3 -c "import PIL, requests" 2>/dev/null; then
        exec python3 "$DIR/main.py" "$@"
    fi
fi

# Fallback con entorno virtual aislado para distribuciones tradicionales
if [ ! -d "$DIR/venv" ]; then
    if [ -f /etc/NIXOS ]; then
        echo "[Cina] Detectado NixOS sin dependencias en el Python actual."
        echo "[Cina] Recomendado: ejecuta 'nix run .' o 'nix develop' usando flake.nix."
        exit 1
    fi
    echo "[Cina] Creando entorno virtual..."
    python3 -m venv "$DIR/venv"
    "$DIR/venv/bin/pip" install -r "$DIR/requirements.txt"
fi

exec "$DIR/venv/bin/python3" "$DIR/main.py" "$@"
