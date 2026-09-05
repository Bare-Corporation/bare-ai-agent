#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Bare-AI model catalog helper.
# Single source of truth = the Council API /v1/models endpoint.
# Provides: catalog_fetch, catalog_render_menu, catalog_resolve.
# Requires: jq, curl.
# ─────────────────────────────────────────────────────────────

COUNCIL_API_BASE_URL="${COUNCIL_API_BASE_URL:-https://api.bare-ai.net}"
CATALOG_CACHE="${CATALOG_CACHE:-$HOME/.bare-ai/model-catalog.json}"
CATALOG_MAX_AGE_SEC="${CATALOG_MAX_AGE_SEC:-3600}"
CATALOG_FALLBACK_MARKER="${CATALOG_CACHE}.fallback"

# Baked fallback: minimal catalog if network + cache both unavailable.
# (cloud essentials + council; local models resolve from cache when present)
_catalog_baked_fallback() {
  cat << 'FALLBACK_JSON'
{"models":[
{"shortcut":"236","model_id":"claude-sonnet-4-6","display_name":"claude-sonnet-4-6","provider":"anthropic","is_cloud":true,"tool_capability":"doer","is_free_tier":false},
{"shortcut":"246","model_id":"claude-opus-4-6","display_name":"claude-opus-4-6","provider":"anthropic","is_cloud":true,"tool_capability":"doer","is_free_tier":false},
{"shortcut":"103","model_id":"gemini-2.5-pro","display_name":"gemini-2.5-pro","provider":"google","is_cloud":true,"tool_capability":"doer","is_free_tier":false},
{"shortcut":"303","model_id":"deepseek-v4-flash","display_name":"deepseek-v4-flash","provider":"deepseek","is_cloud":true,"tool_capability":"doer","is_free_tier":true},
{"shortcut":"777","model_id":"bare-ai-council-v1","display_name":"Bare-AI Council","provider":"bare-ai","is_cloud":true,"tool_capability":"doer","is_free_tier":false}
]}
FALLBACK_JSON
}

# Fetch /v1/models -> write cache. Best-effort; never hard-fails.
# Returns 0 if cache is usable (fresh, refreshed, or pre-existing).
catalog_fetch() {
  mkdir -p "$(dirname "$CATALOG_CACHE")"
  local tmp="${CATALOG_CACHE}.tmp.$$"
  # Optional usage tracking: per-install AGENT_ID (agent.env). Absent/empty
  # -> no header; never hard-fails.
  local agent_hdr=()
  if [ -n "${AGENT_ID:-}" ]; then
    agent_hdr=(-H "X-Agent-Id: $AGENT_ID")
  fi
  if curl -s -k --max-time 8 "${agent_hdr[@]}" "${COUNCIL_API_BASE_URL}/v1/models" -o "$tmp" 2>/dev/null; then
    # validate it parses and has models
    if jq -e '.models | length > 0' "$tmp" >/dev/null 2>&1; then
      mv -f "$tmp" "$CATALOG_CACHE"
      rm -f "$tmp" 2>/dev/null
      rm -f "$CATALOG_FALLBACK_MARKER" 2>/dev/null
      return 0
    fi
  fi
  rm -f "$tmp" 2>/dev/null
  # fetch failed: keep existing cache if present
  [ -s "$CATALOG_CACHE" ] && return 0
  # no cache either: write baked fallback
  _catalog_baked_fallback > "$CATALOG_CACHE"
  touch "$CATALOG_FALLBACK_MARKER"
  return 0
}

# Ensure a usable cache exists; refresh if stale (best-effort).
catalog_ensure() {
  if [ ! -s "$CATALOG_CACHE" ]; then
    catalog_fetch
    return
  fi
  # refresh if older than max age (best-effort, non-fatal)
  local age now mtime
  now=$(date +%s)
  mtime=$(stat -c %Y "$CATALOG_CACHE" 2>/dev/null || echo 0)
  age=$(( now - mtime ))
  if [ "$age" -gt "$CATALOG_MAX_AGE_SEC" ] || [ -f "$CATALOG_FALLBACK_MARKER" ]; then
    catalog_fetch
  fi
  return 0
}

# Read models from cache (jq passthrough helper).
_catalog_jq() { jq -r "$@" "$CATALOG_CACHE" 2>/dev/null; }

# Render the interactive menu grouped by tier (local vs cloud+council).
catalog_render_menu() {
  catalog_ensure
  echo -e "
\033[1;36m===== BARE-AI SOVEREIGN (local) =====\033[0m"
  _catalog_jq '.models[] | select(.is_cloud==false) | "   \(.shortcut)) \(.display_name)   [\(.model_id)]"' | sort
  echo -e "
\033[1;35m===== BARE-AI PREMIUM (cloud + council) =====\033[0m"
  _catalog_jq '.models[] | select(.is_cloud==true) | "   \(.shortcut)) \(.display_name)   [\(.model_id)]"' | sort
}

# Resolve a shortcut -> prints "model_id|tool_capability|is_cloud".
# Returns 1 if not found.
catalog_resolve() {
  local sc="$1"
  catalog_ensure
  local line
  line=$(_catalog_jq --arg sc "$sc" '.models[] | select(.shortcut==$sc) | "\(.model_id)|\(.tool_capability)|\(.is_cloud)"')
  if [ -z "$line" ]; then
    # try exact model_id match
    line=$(_catalog_jq --arg sc "$sc" '.models[] | select(.model_id==$sc) | "\(.model_id)|\(.tool_capability)|\(.is_cloud)"')
  fi
  [ -z "$line" ] && return 1
  echo "$line"
  return 0
}
