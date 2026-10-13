#!/usr/bin/env bash
# Publish one board's files to the `site` branch that GitHub Pages serves.
# usage: tools/publish_site.sh <board folder name, e.g. cfb> <dir with the files to publish>
# The branch is kept as a single commit (force-pushed) so frequent price updates don't grow the repo.
# Other boards' folders, and files already published in this board's folder (e.g. the weekly workbook), are kept.
# Boards publish independently: the push is guarded by a lease on the commit it was built from, and a publish
# that lost a race is rebuilt from the newer site and retried.
set -euo pipefail
board="$1"
src="$(cd "$2" && pwd)"
here="$(cd "$(dirname "$0")" && pwd)"
cd "$(git rev-parse --show-toplevel)"   # git archive from a subfolder would only export that subfolder
repo_url="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
who=(-c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com")
for attempt in 1 2 3 4 5; do
  work="$(mktemp -d)"
  lease=""
  if git fetch -q --depth=1 origin site 2>/dev/null; then
    lease="$(git rev-parse FETCH_HEAD)"
    git archive FETCH_HEAD | tar -x -C "$work"
  fi
  mkdir -p "$work/$board"
  cp -r "$src"/. "$work/$board/"
  cp -r "$here/../site/." "$work/"   # landing page + shared files (bets.js, bets.css)
  touch "$work/.nojekyll"
  if (
    cd "$work"
    git init -q -b site
    git add -A
    git "${who[@]}" commit -q -m "Publish boards ($board $(TZ=America/New_York date '+%a %b %d %I:%M %p ET'))"
    if [ -n "$lease" ]; then
      git push -q --force-with-lease="site:$lease" "$repo_url" site
    else
      git push -q "$repo_url" site
    fi
  ); then
    echo "published $board (attempt $attempt)"
    exit 0
  fi
  sleep $((attempt * 5))
done
echo "publish of $board failed after 5 attempts" >&2
exit 1
