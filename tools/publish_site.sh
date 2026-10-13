#!/usr/bin/env bash
# Publish one board's files to the `site` branch that GitHub Pages serves.
# usage: tools/publish_site.sh <board folder name, e.g. cfb> <dir with the files to publish>
# The branch is kept as a single commit (force-pushed) so frequent price updates don't grow the repo.
# Other boards' folders and files already published in this board's folder (e.g. the weekly workbook) are kept.
set -euo pipefail
board="$1"; src="$2"
repo_url="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
work="$(mktemp -d)"
if git fetch -q --depth=1 origin site 2>/dev/null; then
  git archive FETCH_HEAD | tar -x -C "$work"
fi
mkdir -p "$work/$board"
cp -r "$src"/. "$work/$board/"
cp "$(dirname "$0")/../site/index.html" "$work/index.html"
touch "$work/.nojekyll"
cd "$work"
git init -q -b site
git add -A
git -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit -q -m "Publish boards ($board $(TZ=America/New_York date '+%a %b %d %I:%M %p ET'))"
git push -q -f "$repo_url" site
