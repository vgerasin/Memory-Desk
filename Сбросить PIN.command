#!/bin/zsh
# Снимает PIN Memory Desk, если вы его забыли. Файлы и записи не затрагиваются: PIN их не шифрует.
set -uo pipefail
SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"
PY="$SCRIPT_DIR/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
"$PY" scripts/reset_pin.py
echo
read -k 1 "?Нажмите любую клавишу…"
