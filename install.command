#!/bin/zsh
# Memory Desk — установка и проверка.
#
#   ./install.command                 установить всё и запустить кабинет
#   ./install.command --check         только проверить, что уже есть (ничего не меняет)
#   ./install.command --skip-models   пропустить загрузку моделей: их скачивает мастер первого запуска в кабинете
#                                     (так ставит приложение для Mac; для обновления кода тоже подходит)
#   ./install.command --no-open       не открывать кабинет в конце
#
# Скрипт можно запускать повторно: готовое пропускается, пользовательские данные не затрагиваются.
# Совместим и с zsh (macOS по умолчанию), и с bash.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

MODE=install
SKIP_MODELS=0
OPEN_AFTER=1
for arg in "$@"; do
  case "$arg" in
    --check) MODE=check ;;
    --skip-models) SKIP_MODELS=1 ;;
    --no-open) OPEN_AFTER=0 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Неизвестный параметр: $arg (см. --help)"; exit 2 ;;
  esac
done

# Модели. Те же значения использует код (tests/test_installer.py сверяет их).
EMBED_MODEL="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
WHISPER_MODEL="mlx-community/whisper-large-v3-turbo"
LLM_MODEL="qwen3:4b"
OLLAMA_ADDR="127.0.0.1:11435"          # свой экземпляр Ollama, не мешает обычному приложению на 11434
OLLAMA_URL="http://$OLLAMA_ADDR"
MODELS_DIR="$SCRIPT_DIR/ollama-models"
HF_CACHE="$SCRIPT_DIR/huggingface-cache"
VENV="$SCRIPT_DIR/.venv"
PY="$VENV/bin/python"
LOG_DIR="$SCRIPT_DIR/logs"
LOG="$LOG_DIR/install.log"
NEED_FREE_GB=12

PROBLEMS=0
STEP=0
TOTAL=7

mkdir -p "$LOG_DIR"

say()  { printf '%s\n' "$*"; }
step() { STEP=$((STEP + 1)); printf '\n[%s/%s] %s\n' "$STEP" "$TOTAL" "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }
bad()  { printf '  ✗ %s\n' "$*"; PROBLEMS=$((PROBLEMS + 1)); }
have() { command -v "$1" >/dev/null 2>&1; }
log()  { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

# Выполнить команду, подробности — в журнал; при ошибке показать последние строки.
run() {
  local title="$1"; shift
  log "RUN: $*"
  if "$@" >>"$LOG" 2>&1; then
    return 0
  fi
  bad "$title не удалось. Последние строки журнала ($LOG):"
  tail -n 8 "$LOG" | sed 's/^/      /'
  return 1
}

finish_with_error() {
  say ""
  say "Установка остановилась: $1"
  say "Журнал: $LOG"
  say "Проверить состояние без изменений: ./install.command --check"
  exit 1
}

say "Memory Desk — $([ "$MODE" = check ] && echo проверка || echo установка)"

# --------------------------------------------------------------------------- 1. система
step "Проверяю компьютер"
if [ "$(uname -s)" != "Darwin" ]; then
  bad "Нужна macOS (сейчас: $(uname -s))."
elif [ "$(uname -m)" != "arm64" ]; then
  bad "Нужен Mac с Apple Silicon (M1 и новее): MLX Whisper на Intel не работает."
else
  ok "Apple Silicon, macOS $(sw_vers -productVersion 2>/dev/null || echo '?')"
fi
MAJOR="$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)"
if [ -n "${MAJOR:-}" ] && [ "$MAJOR" -lt 13 ] 2>/dev/null; then
  bad "Нужна macOS 13 или новее."
fi
FREE_GB="$(df -g "$SCRIPT_DIR" 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -n "${FREE_GB:-}" ] && [ "$FREE_GB" -lt "$NEED_FREE_GB" ] 2>/dev/null; then
  if [ "$MODE" = check ] || [ "$SKIP_MODELS" = 1 ]; then
    warn "Свободно ${FREE_GB} ГБ; для моделей нужно около ${NEED_FREE_GB} ГБ."
  else
    bad "Свободно ${FREE_GB} ГБ; для моделей нужно около ${NEED_FREE_GB} ГБ."
  fi
else
  ok "Свободного места: ${FREE_GB:-?} ГБ"
fi
MEM_GB=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))
if [ "$MEM_GB" -gt 0 ] && [ "$MEM_GB" -lt 16 ]; then
  warn "Оперативной памяти ${MEM_GB} ГБ. Рекомендуется 16 ГБ: длинные записи могут обрабатываться медленно."
elif [ "$MEM_GB" -gt 0 ]; then
  ok "Оперативная память: ${MEM_GB} ГБ"
fi
[ "$PROBLEMS" -gt 0 ] && [ "$MODE" = install ] && finish_with_error "компьютер не подходит (см. выше)."

# --------------------------------------------------------------------------- 2. Homebrew и системные пакеты
step "Системные компоненты (Homebrew, Python 3.12, ffmpeg, Ollama)"
if ! have brew; then
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$candidate" ] && eval "$("$candidate" shellenv)" && break
  done
fi
if ! have brew; then
  bad "Не найден Homebrew. Установите его с https://brew.sh и запустите установщик снова."
  [ "$MODE" = install ] && finish_with_error "нет Homebrew."
else
  ok "Homebrew: $(command -v brew)"
  for pkg in python@3.12 ffmpeg ollama; do
    if brew list "$pkg" >/dev/null 2>&1; then
      ok "$pkg установлен"
    elif [ "$MODE" = check ]; then
      bad "$pkg не установлен"
    else
      say "  → устанавливаю $pkg…"
      run "Установка $pkg" brew install "$pkg" || finish_with_error "не удалось поставить $pkg."
      ok "$pkg установлен"
    fi
  done
fi

# --------------------------------------------------------------------------- 3. окружение Python
step "Окружение Python"
if have brew && brew list python@3.12 >/dev/null 2>&1; then
  SYS_PY="$(brew --prefix python@3.12)/bin/python3.12"
else
  SYS_PY=""
fi
if [ -x "$PY" ]; then
  ok "Окружение .venv есть: $("$PY" --version 2>&1)"
elif [ "$MODE" = check ]; then
  bad "Нет окружения .venv"
elif [ -n "$SYS_PY" ] && [ -x "$SYS_PY" ]; then
  say "  → создаю изолированное окружение…"
  run "Создание окружения" "$SYS_PY" -m venv "$VENV" || finish_with_error "не удалось создать .venv."
  ok "создано"
else
  bad "Не найден Python 3.12."
  [ "$MODE" = install ] && finish_with_error "нет Python 3.12."
fi

if [ -x "$PY" ]; then
  if "$PY" -c "import fastapi, uvicorn, qdrant_client, sentence_transformers, mlx_whisper, icalendar, recurring_ical_events" >/dev/null 2>&1; then
    ok "Python-пакеты на месте"
  elif [ "$MODE" = check ]; then
    bad "Не хватает Python-пакетов: запустите ./install.command"
  else
    say "  → ставлю пакеты (несколько минут, нужен интернет)…"
    run "Обновление pip" "$PY" -m pip install --upgrade pip || true
    run "Установка пакетов" "$PY" -m pip install -r "$SCRIPT_DIR/requirements.txt" || finish_with_error "не удалось поставить пакеты (журнал: $LOG)."
    ok "пакеты установлены"
  fi
  # Календарь и Напоминания macOS — необязательная часть: без неё кабинет работает.
  if "$PY" -c "import EventKit" >/dev/null 2>&1; then
    ok "Доступ к Календарю и Напоминаниям (PyObjC) есть"
  elif [ "$MODE" = install ]; then
    if "$PY" -m pip install -q "pyobjc-framework-EventKit>=10" >>"$LOG" 2>&1; then
      ok "Доступ к Календарю и Напоминаниям установлен"
    else
      warn "PyObjC не установился: Календарь и Напоминания будут недоступны (остальное работает)."
    fi
  else
    warn "Нет PyObjC: Календарь и Напоминания недоступны"
  fi
fi

# Распознавание текста на фото (вложения к записи) — тоже необязательная часть: без неё фото сохраняются без текста.
if [ -x "$PY" ]; then
  if "$PY" -c "import Vision" >/dev/null 2>&1; then
    ok "Распознавание текста на фото (Vision) есть"
  elif [ "$MODE" = install ]; then
    if "$PY" -m pip install -q "pyobjc-framework-Vision>=10" >>"$LOG" 2>&1; then
      ok "Распознавание текста на фото (Vision) установлено"
    else
      warn "Vision не установился: фото будут сохраняться без текста (запасной вариант: brew install tesseract tesseract-lang)"
    fi
  else
    warn "Нет Vision: текст с фото не распознаётся"
  fi
fi

# --------------------------------------------------------------------------- 4. папки
step "Рабочие папки"
for dir in logs runtime webui/data work; do
  if [ "$MODE" = install ]; then mkdir -p "$SCRIPT_DIR/$dir"; fi
  [ -d "$SCRIPT_DIR/$dir" ] && ok "$dir" || bad "нет папки $dir"
done
if [ "$MODE" = install ]; then
  chmod +x "$SCRIPT_DIR/install.command" "$SCRIPT_DIR/start.command" "$SCRIPT_DIR/meeting-assistant" "$SCRIPT_DIR/meetings-process" 2>/dev/null
  # Архив, скачанный из браузера, помечается системой как «из интернета» — снимаем пометку, иначе macOS блокирует запуск.
  xattr -dr com.apple.quarantine "$SCRIPT_DIR" >/dev/null 2>&1 || true
fi

# --------------------------------------------------------------------------- 5. локальная Ollama
ollama_up() { curl -fsS -m 3 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; }

step "Ollama и языковая модель ($LLM_MODEL)"
if ! have ollama; then
  bad "Команда ollama не найдена."
else
  if ! ollama_up; then
    if [ "$MODE" = check ]; then
      warn "Свой экземпляр Ollama ($OLLAMA_ADDR) сейчас не запущен; кабинет запустит его сам."
    else
      say "  → запускаю локальную Ollama на $OLLAMA_ADDR…"
      OLLAMA_HOST="$OLLAMA_ADDR" OLLAMA_MODELS="$MODELS_DIR" nohup ollama serve >>"$LOG_DIR/ollama.log" 2>&1 &
      for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do ollama_up && break; sleep 1; done
    fi
  fi
  if ollama_up; then
    if OLLAMA_HOST="$OLLAMA_ADDR" ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$LLM_MODEL"; then
      ok "Модель $LLM_MODEL уже загружена"
    elif [ "$SKIP_MODELS" = 1 ] && [ "$MODE" = install ]; then
      warn "Модель $LLM_MODEL не загружена: её скачает мастер первого запуска в кабинете (около 2,5 ГБ)"
    elif [ "$MODE" = check ]; then
      bad "Модель $LLM_MODEL не загружена"
    else
      say "  → загружаю $LLM_MODEL (около 2,5 ГБ)…"
      OLLAMA_HOST="$OLLAMA_ADDR" ollama pull "$LLM_MODEL" 2>&1 | tee -a "$LOG" | tail -n 2
      if OLLAMA_HOST="$OLLAMA_ADDR" ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$LLM_MODEL"; then
        ok "Модель $LLM_MODEL загружена в $MODELS_DIR"
      else
        bad "Модель $LLM_MODEL не загрузилась (проверьте интернет и повторите)."
      fi
    fi
  elif [ "$MODE" = install ]; then
    bad "Ollama не запустилась. Журнал: $LOG_DIR/ollama.log"
  fi
fi

# --------------------------------------------------------------------------- 6. модели поиска и распознавания
hf_cached() {  # $1 — репозиторий, $2 — каталог кеша
  local dir="$2/models--$(echo "$1" | sed 's#/#--#g')"
  [ -d "$dir/snapshots" ] && [ -n "$(ls -A "$dir/snapshots" 2>/dev/null)" ]
}

step "Модель поиска и модель распознавания речи"
EMBED_HOME="$HF_CACHE"            # sentence-transformers: cache_folder=huggingface-cache
WHISPER_HOME="$HF_CACHE/hub"      # mlx_whisper: HF_HOME=huggingface-cache → hub/
if hf_cached "$EMBED_MODEL" "$EMBED_HOME"; then
  ok "Модель поиска есть ($(basename "$EMBED_MODEL"))"
elif [ "$SKIP_MODELS" = 1 ] && [ "$MODE" = install ]; then
  warn "Модель поиска не загружена: её скачает мастер первого запуска в кабинете (около 0,5 ГБ)"
elif [ "$MODE" = check ]; then
  bad "Нет модели поиска: без неё не работают поиск и чат"
elif [ -x "$PY" ]; then
  say "  → загружаю модель поиска (около 0,5 ГБ)…"
  run "Загрузка модели поиска" env EMBED_MODEL="$EMBED_MODEL" HF_CACHE="$HF_CACHE" HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 \
    "$PY" -c "
import os
from sentence_transformers import SentenceTransformer
SentenceTransformer(os.environ['EMBED_MODEL'], cache_folder=os.environ['HF_CACHE'])
" && ok "Модель поиска загружена"
fi
if hf_cached "$WHISPER_MODEL" "$WHISPER_HOME"; then
  ok "Модель распознавания речи есть ($(basename "$WHISPER_MODEL"))"
elif [ "$MODE" = check ] || [ "$SKIP_MODELS" = 1 ]; then
  warn "Модель распознавания речи не загружена: её скачает мастер первого запуска в кабинете (около 1,6 ГБ)"
elif [ -x "$PY" ]; then
  say "  → загружаю модель распознавания речи (около 1,6 ГБ)…"
  run "Загрузка модели распознавания" env WHISPER_MODEL="$WHISPER_MODEL" HF_HOME="$HF_CACHE" HF_HUB_OFFLINE=0 \
    "$PY" -c "
import os
from huggingface_hub import snapshot_download
snapshot_download(os.environ['WHISPER_MODEL'])
" && ok "Модель распознавания речи загружена"
fi

# --------------------------------------------------------------------------- 7. итог
step "Итог"
if [ "$MODE" = install ] && [ -x "$PY" ]; then
  if "$PY" -c "import memory_desk.app" >/dev/null 2>&1; then
    ok "Кабинет импортируется без ошибок"
  else
    bad "Кабинет не запускается: $("$PY" -c 'import memory_desk.app' 2>&1 | tail -n 1)"
  fi
fi

say ""
if [ "$PROBLEMS" -gt 0 ]; then
  say "Найдено проблем: $PROBLEMS. Они перечислены выше; журнал: $LOG"
  [ "$MODE" = install ] && exit 1
  exit 1
fi
if [ "$MODE" = check ]; then
  say "Всё на месте. Запуск: ./start.command"
  exit 0
fi
say "Готово. Адрес кабинета: http://127.0.0.1:8502"
[ "$SKIP_MODELS" = 1 ] && say "Модели скачает мастер первого запуска: он откроется в кабинете сам."
say "Папки записей и Obsidian выбираются в кабинете: шестерёнка → «Настройки»."
if [ "$OPEN_AFTER" = 1 ]; then
  say "Открываю Memory Desk…"
  exec "$SCRIPT_DIR/start.command"
fi
