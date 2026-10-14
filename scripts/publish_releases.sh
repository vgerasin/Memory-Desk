#!/bin/zsh
# Теги и релизы на GitHub для версий 2.2.0, 2.3.0, 2.4.0 и 3.0.0. Запускать в клоне репозитория на Mac, где есть `gh auth login`.
# Заметки лежат в docs/releases/. Если тег уже есть, он пропускается.
set -eu
cd "$(dirname "$0")/.."
git fetch --tags -q
for pair in "v2.2.0 f65bc61" "v2.3.0 1b91b1e" "v2.4.0 5bedf62" "v3.0.0 $(git rev-parse --short origin/main)"; do
  tag=${pair%% *}; commit=${pair##* }
  if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then echo "$tag уже есть"; else git tag -a "$tag" "$commit" -m "Memory Desk ${tag#v}"; fi
  git push -q origin "refs/tags/$tag"
  gh release view "$tag" >/dev/null 2>&1 || gh release create "$tag" --title "Memory Desk ${tag#v}" --notes-file "docs/releases/$tag.md" --verify-tag
done
echo "Готово: https://github.com/vgerasin/Memory-Desk/releases"
