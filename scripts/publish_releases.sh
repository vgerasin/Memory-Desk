#!/bin/zsh
# Теги и релизы на GitHub для версий 2.2.0, 2.3.0, 2.4.0, 3.0.0, 3.1.0 и 4.0.0. Запускать в клоне репозитория на Mac, где есть `gh auth login`.
# Заметки лежат в docs/releases/. Если тег уже есть, он пропускается.
set -eu
cd "$(dirname "$0")/.."
git fetch --tags -q
# Версия 4.0.0 помечается коммитом «Релиз 4.0.0» (его находит по заголовку: хеш зависит от истории).
RELEASE_400=$(git log origin/main --format=%H --grep='^Релиз 4.0.0' -n 1)
for pair in "v2.2.0 949e9b5" "v2.3.0 6c19453" "v2.4.0 e583c96" "v3.0.0 188b9fb" "v3.1.0 1bf7fe9" "v4.0.0 $RELEASE_400"; do
  tag=${pair%% *}; commit=${pair##* }
  [ -n "$commit" ] || { echo "$tag: нет коммита для метки, пропускаю"; continue; }
  if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then echo "$tag уже есть"; else git tag -a "$tag" "$commit" -m "Memory Desk ${tag#v}"; fi
  git push -q origin "refs/tags/$tag"
  gh release view "$tag" >/dev/null 2>&1 || gh release create "$tag" --title "Memory Desk ${tag#v}" --notes-file "docs/releases/$tag.md" --verify-tag
done
echo "Готово: https://github.com/vgerasin/Memory-Desk/releases"
