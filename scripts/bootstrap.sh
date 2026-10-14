#!/bin/zsh
# Memory Desk — установка одной командой на чистом Mac:
#
#   curl -fsSL https://raw.githubusercontent.com/vgerasin/Memory-Desk/main/scripts/bootstrap.sh | zsh
#
# Скрипт только забирает код в папку ~/memory-desk (или в $MEMORY_DESK_DIR) и запускает `install.command`, который
# делает всё остальное и описан в README. Ничего не ставится без вашего ведома: `install.command` перечисляет шаги.
# Если папка уже есть, код обновляется (`git pull --ff-only`), данные не затрагиваются.
# Те, кто не любит запускать скрипт из сети, делают то же вручную: git clone … && ./install.command.

set -eu

REPO="${MEMORY_DESK_REPO:-https://github.com/vgerasin/Memory-Desk.git}"
DEST="${MEMORY_DESK_DIR:-$HOME/memory-desk}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Memory Desk работает только на macOS (Apple Silicon)."; exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "Нужен git. macOS предложит поставить Command Line Tools: согласитесь, дождитесь конца установки и повторите команду."
  xcode-select --install 2>/dev/null || true
  exit 1
fi

if [[ -d "$DEST/.git" ]]; then
  echo "Папка $DEST уже есть, обновляю код…"
  git -C "$DEST" pull --ff-only
elif [[ -e "$DEST" ]]; then
  echo "$DEST уже существует и не является копией Memory Desk. Задайте другую папку: MEMORY_DESK_DIR=~/другая-папка"; exit 1
else
  git clone "$REPO" "$DEST"
fi

cd "$DEST"
chmod +x ./*.command meeting-assistant meetings-process 2>/dev/null || true
exec ./install.command "$@"
