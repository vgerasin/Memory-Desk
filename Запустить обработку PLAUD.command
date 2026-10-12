#!/bin/zsh
set -o pipefail
SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"
clear
echo "Локальная обработка записи PLAUD"
echo "Окно можно закрыть после сообщения «Готово»."
echo
./meetings-process 2>&1 | tee logs/last-run.log
status=${pipestatus[1]}
echo
if [[ $status -eq 0 ]]; then
  echo "Готово. Можно открыть Obsidian."
else
  echo "Обработка завершилась с ошибкой. Не закрывайте окно до проверки."
fi
echo
read "?Нажмите Enter, чтобы закрыть окно..."
exit $status
