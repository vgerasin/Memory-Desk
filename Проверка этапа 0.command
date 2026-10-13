#!/bin/zsh
# Этап 0: проверка Memory Desk на этом Mac. Собирает отчёт stage0-report.md без текстов записей.
# Дополнительные параметры передаются скрипту, например: ./"Проверка этапа 0.command" --models qwen3:4b,qwen3:8b
set -uo pipefail
SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"
PY="$SCRIPT_DIR/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
"$PY" scripts/stage0_report.py "$@"
echo
echo "Готово. Файл отчёта: $SCRIPT_DIR/stage0-report.md"
echo "Окно можно закрыть."
read -k 1 "?Нажмите любую клавишу…"
