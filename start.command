#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PORT="${MEETING_WEBUI_PORT:-8502}"
URL="http://127.0.0.1:$PORT"
mkdir -p "$SCRIPT_DIR/logs"

is_memory_desk() {
  # Streamlit отвечает 200 на любой адрес, поэтому проверяем содержимое ответа.
  /usr/bin/curl -fsS "$URL/api/health" 2>/dev/null | grep -q '"indexState"'
}

# Файлы кабинета новее запущенного сервера (после обновления) — сервер надо перезапустить, иначе страница и сервер расходятся.
needs_restart() {
  [[ -f "$SCRIPT_DIR/logs/webui.pid" ]] || return 1
  [[ -n "$(/usr/bin/find "$SCRIPT_DIR/memory_desk" "$SCRIPT_DIR/meetings_process.py" -newer "$SCRIPT_DIR/logs/webui.pid" \( -name '*.py' -o -name '*.js' -o -name '*.html' \) -print -quit 2>/dev/null)" ]]
}

if is_memory_desk && ! needs_restart; then
  open "$URL"
  exit 0
fi

# Порт занят чем-то другим (обычно прежним интерфейсом на Streamlit) — освобождаем его.
OLD_PIDS="$(/usr/sbin/lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$OLD_PIDS" ]]; then
  echo "Останавливаю прежнюю версию интерфейса (порт $PORT)…"
  pkill -f "streamlit run" 2>/dev/null || true
  echo "$OLD_PIDS" | xargs kill 2>/dev/null || true
  for _ in {1..10}; do
    [[ -z "$(/usr/sbin/lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true)" ]] && break
    sleep 1
  done
  # если мягкая остановка не помогла
  OLD_PIDS="$(/usr/sbin/lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -n "$OLD_PIDS" ]] && echo "$OLD_PIDS" | xargs kill -9 2>/dev/null || true
fi

MEETING_WEBUI_PORT="$PORT" nohup "$SCRIPT_DIR/meeting-assistant" \
  >>"$SCRIPT_DIR/logs/webui.log" 2>&1 &
SERVER_PID=$!

echo "Запускаю Memory Desk… (при первом запуске после обновления докачиваются пакеты, это может занять несколько минут)"
echo "Ход установки: tail -f \"$SCRIPT_DIR/logs/webui.log\""

for i in {1..300}; do
  if is_memory_desk; then
    open "$URL"
    exit 0
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    break
  fi
  (( i % 10 == 0 )) && echo "…ещё запускается (${i} с)"
  sleep 1
done

echo
echo "Memory Desk не запустился. Последние строки журнала:"
tail -n 20 "$SCRIPT_DIR/logs/webui.log" || true
read "?Нажмите Enter, чтобы закрыть окно…"
exit 1
