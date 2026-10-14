#!/bin/zsh
# Собирает MemoryDesk.app из SwiftPM-пакета. Нужны Xcode или Command Line Tools (swift). Подпись — только «ad hoc»
# (для запуска на этом Mac); нотаризация и подпись Developer ID — отдельный шаг, см. README.md.
set -euo pipefail
cd "${0:A:h}"
VERSION="$(grep -m1 '__version__' ../memory_desk/__init__.py | sed -E 's/.*"([^"]+)".*/\1/')"
swift build -c release
APP="build/MemoryDesk.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$(swift build -c release --show-bin-path)/MemoryDesk" "$APP/Contents/MacOS/MemoryDesk"
cp ../scripts/bootstrap.sh "$APP/Contents/Resources/bootstrap.sh"
sed "s/__VERSION__/${VERSION:-0}/g" Resources/Info.plist > "$APP/Contents/Info.plist"
codesign --force --deep --sign - "$APP"
echo "Готово: $PWD/$APP"
echo "Перетащите в «Программы». Первый запуск: правый клик → «Открыть» (приложение не нотаризовано)."
