#!/usr/bin/env bash
# bare-untracked-secret-scan.sh - ticket 282: scan files an agent WROTE but git does not track.
#
# Why this exists: the pre-commit gitleaks hook only sees STAGED content. A file created by an
# agent's tool shell and never `git add`ed is invisible to it - that is exactly how
# import_daily_ledger.py sat untracked on .13 holding a key 37 times. This closes that gap.
#
# Design notes:
#  - Untracked-but-not-ignored by default. Ignored files (.env, vault env) legitimately hold
#    secrets and would drown the signal; --include-ignored opts in for a deliberate audit.
#  - Files are copied into a temp tree so gitleaks scans exactly these files and not the repo.
#  - --redact: findings never print a credential value, only rule id, file and line.
#  - Exit: 0 clean, 1 findings, 2 tooling problem. Never exit 0 on a tool failure.
#
# Usage: bare-untracked-secret-scan.sh [--include-ignored] [repo ...]
set -uo pipefail

PATH="$HOME/.local/bin:$PATH"
export PATH

INCLUDE_IGNORED=0
EXPLICIT=0
REPOS=()
for arg in "$@"; do
  case "$arg" in
    --include-ignored) INCLUDE_IGNORED=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) REPOS+=("$arg"); EXPLICIT=1 ;;
  esac
done
if [ ${#REPOS[@]} -eq 0 ]; then
  for d in "$HOME/bare-ai-agent" "$HOME/bare-ai-cli" "$HOME/bare-necessities-workspace"; do
    [ -d "$d/.git" ] && REPOS+=("$d")
  done
fi

CONFIG=""
[ -f "$HOME/bare-ai-agent/.gitleaks.toml" ] && CONFIG="$HOME/bare-ai-agent/.gitleaks.toml"

command -v gitleaks >/dev/null 2>&1 || { echo "FAIL: gitleaks not on PATH (looked in ~/.local/bin)" >&2; exit 2; }
[ ${#REPOS[@]} -gt 0 ] || { echo "FAIL: no git repositories to scan" >&2; exit 2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
TOTAL=0
SCANNED=0

for repo in "${REPOS[@]}"; do
  if [ ! -d "$repo/.git" ]; then
    # An explicitly named path that is not a repo must FAIL, not silently skip: reporting
    # "clean" because we scanned nothing is the exit_zero trap this ticket exists to remove.
    if [ "$EXPLICIT" = "1" ]; then echo "FAIL: not a git repository: $repo" >&2; exit 2; fi
    echo "skip (not a git repo): $repo"; continue
  fi
  list="$TMP/files.txt"
  git -C "$repo" ls-files --others --exclude-standard > "$list" 2>/dev/null || true
  if [ "$INCLUDE_IGNORED" = "1" ]; then
    git -C "$repo" ls-files --others --ignored --exclude-standard >> "$list" 2>/dev/null || true
  fi
  count=$(wc -l < "$list" | tr -d ' ')
  echo "repo $repo: $count untracked file(s)$([ "$INCLUDE_IGNORED" = 1 ] && echo ' (incl. ignored)')"
  [ "$count" -gt 0 ] || continue

  stage="$TMP/$(basename "$repo")"
  mkdir -p "$stage"
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    case "$f" in *.png|*.jpg|*.jpeg|*.gif|*.webp|*.pdf|*.zip|*.gz|*.tar|*.so|*.pyc|*.woff*|*.ttf) continue ;; esac
    size=$(stat -c %s "$repo/$f" 2>/dev/null || echo 0)
    [ "$size" -le 2097152 ] || { echo "  skip (>2MB): $f"; continue; }
    mkdir -p "$stage/$(dirname "$f")"
    cp -p "$repo/$f" "$stage/$f" 2>/dev/null || true
    SCANNED=$((SCANNED+1))
  done < "$list"

  args=(dir "$stage" --redact --no-banner --exit-code 1)
  [ -n "$CONFIG" ] && args+=(--config "$CONFIG")
  gitleaks "${args[@]}" > "$TMP/out.txt" 2>&1
  rc=$?
  if [ $rc -eq 1 ]; then
    echo "  FINDINGS in $repo:"
    sed -n '1,40p' "$TMP/out.txt" | sed 's/^/    /'
    TOTAL=$((TOTAL+1))
  elif [ $rc -ne 0 ]; then
    echo "  ERROR: gitleaks exited $rc in $repo" >&2
    sed -n '1,10p' "$TMP/out.txt" | sed 's/^/    /' >&2
    exit 2
  else
    echo "  clean"
  fi
done

echo "scanned $SCANNED untracked file(s) across ${#REPOS[@]} repo(s)"
if [ "$TOTAL" -gt 0 ]; then
  echo "RESULT: FINDINGS in $TOTAL repo(s)"
  exit 1
fi
echo "RESULT: clean"
exit 0
