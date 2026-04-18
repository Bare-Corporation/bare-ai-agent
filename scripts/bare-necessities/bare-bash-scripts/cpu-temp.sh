#!/bin/bash
#############################################################
#    ____ _                  _ _       _         ____       #
#   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#  CPU Temps Bash ScriptWorker Installer                    #
#  Version: v1.0.1 | Updated: 2026-04-12                    #
#############################################################
#  by the Cloud Integration Corporation                     #
#############################################################
#!/usr/bin/env bash
# =============================================================================
#  cpu-temp.sh — Universal Hardware Temperature Reporter
#  by the Cloud Integration Corporation
#
#  Works on: Intel, AMD, NVIDIA GPU, AMD GPU, ARM (Pi, Jetson),
#            Proxmox VMs, cloud instances, bare metal servers.
#
#  Sources tried in order of reliability:
#    1. lm-sensors    — best for physical Intel/AMD CPUs
#    2. nvidia-smi    — NVIDIA GPU temperature
#    3. rocm-smi      — AMD GPU temperature
#    4. vcgencmd      — Raspberry Pi GPU/SoC
#    5. thermal_zone  — kernel fallback (works in some VMs too)
#
#  Usage:
#    ./cpu-temp.sh              # human-readable summary
#    ./cpu-temp.sh --json       # JSON output for scripts
#    ./cpu-temp.sh --max        # single highest temp value only (°C float)
#    ./cpu-temp.sh --watch      # refresh every 2s (like htop for temps)
# =============================================================================

set -euo pipefail

# ── Helpers ──────────────────────────────────────────────────────────────────

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

# Detect virtualisation once (systemd-detect-virt is universal)
VIRT=$(systemd-detect-virt 2>/dev/null || echo none)
IS_VM=false; [ "$VIRT" != "none" ] && IS_VM=true

# colour_temp <value_float>  →  prints coloured string
colour_temp() {
  local t=$1
  local i=${t%.*}   # integer part only for comparison
  if   [ "$i" -ge 85 ]; then printf "${RED}${BOLD}%.1f°C${RESET}" "$t"
  elif [ "$i" -ge 75 ]; then printf "${YELLOW}%.1f°C${RESET}" "$t"
  elif [ "$i" -ge 60 ]; then printf "${CYAN}%.1f°C${RESET}" "$t"
  else                       printf "${GREEN}%.1f°C${RESET}" "$t"
  fi
}

# ── Temperature Sources ───────────────────────────────────────────────────────

# Each function appends lines to READINGS array: "label|value_float|source_name"
# We collect ALL readings so we can show a full picture, not just the max.

declare -a READINGS=()

# 1. lm-sensors — Intel coretemp, AMD k10temp, board sensors (most accurate)
probe_lm_sensors() {
  command -v sensors >/dev/null 2>&1 || return
  # sensors -A gives cleaner output; -u gives machine-parseable SI units
  while IFS= read -r line; do
    # Match lines like: "Core 0:         +42.0°C  (high = +80.0°C"
    # and              "Tdie:            +55.8°C  (high = +70.0°C"
    if [[ "$line" =~ ^\s*([A-Za-z0-9_\ ]+):[[:space:]]+\+([0-9]+\.[0-9]+)[°]C ]]; then
      local label="${BASH_REMATCH[1]}"
      local val="${BASH_REMATCH[2]}"
      label=$(echo "$label" | xargs)   # trim whitespace
      READINGS+=("${label}|${val}|lm-sensors")
    fi
  done < <(sensors -A 2>/dev/null)
}

# 2. NVIDIA GPU (nvidia-smi)
probe_nvidia() {
  command -v nvidia-smi >/dev/null 2>&1 || return
  local idx=0
  while IFS=',' read -r name temp; do
    name=$(echo "$name" | xargs)
    temp=$(echo "$temp" | xargs)
    [[ "$temp" =~ ^[0-9]+$ ]] || continue
    READINGS+=("GPU${idx} ${name}|${temp}.0|nvidia-smi")
    (( idx++ )) || true
  done < <(nvidia-smi --query-gpu=name,temperature.gpu --format=csv,noheader 2>/dev/null)
}

# 3. AMD GPU (ROCm — rocm-smi)
probe_amd_gpu() {
  command -v rocm-smi >/dev/null 2>&1 || return
  local idx=0
  while IFS= read -r line; do
    # Matches: "GPU[0]          : Temperature (Sensor edge) (C): 55.0"
    if [[ "$line" =~ GPU\[([0-9]+)\].*Temperature.*:\s*([0-9]+\.?[0-9]*) ]]; then
      local gidx="${BASH_REMATCH[1]}"
      local val="${BASH_REMATCH[2]}"
      READINGS+=("AMD GPU${gidx}|${val}|rocm-smi")
    fi
  done < <(rocm-smi --showtemp 2>/dev/null)
}

# 4. Raspberry Pi / Jetson (vcgencmd)
probe_vcgencmd() {
  command -v vcgencmd >/dev/null 2>&1 || return
  local raw
  raw=$(vcgencmd measure_temp 2>/dev/null | grep -oP '[0-9]+\.[0-9]+' || echo "")
  [ -n "$raw" ] && READINGS+=("SoC|${raw}|vcgencmd")
}

# 5. Kernel thermal zones — universal last resort
# Reads ALL zones and labels them with their type (e.g., x86_pkg_temp, acpitz)
probe_thermal_zones() {
  local found=0
  for zone_dir in /sys/class/thermal/thermal_zone*; do
    [ -d "$zone_dir" ] || continue
    local temp_file="${zone_dir}/temp"
    local type_file="${zone_dir}/type"
    [ -f "$temp_file" ] || continue

    local raw; raw=$(cat "$temp_file" 2>/dev/null || echo 0)
    [ "$raw" -le 0 ] && continue

    # Convert millidegrees → Celsius
    local val; val=$(awk "BEGIN{printf \"%.1f\", $raw/1000}")
    local label; label=$(cat "$type_file" 2>/dev/null || echo "thermal_zone")
    local zname; zname=$(basename "$zone_dir")

    READINGS+=("${label} (${zname})|${val}|thermal-zone")
    (( found++ )) || true
  done
  return 0
}

# ── Collect Everything ────────────────────────────────────────────────────────

collect_all() {
  probe_lm_sensors
  probe_nvidia
  probe_amd_gpu
  probe_vcgencmd
  # Only use thermal zones if no better source gave us data
  if [ ${#READINGS[@]} -eq 0 ]; then
    probe_thermal_zones
  fi
}

# ── Find Maximum ──────────────────────────────────────────────────────────────

get_max() {
  local max=0
  for entry in "${READINGS[@]}"; do
    local val="${entry#*|}"
    val="${val%|*}"
    if awk "BEGIN{exit !($val > $max)}"; then
      max=$val
    fi
  done
  printf "%.1f" "$max"
}

# ── Output Modes ──────────────────────────────────────────────────────────────

output_human() {
  local hostname; hostname=$(hostname)
  printf "\n${BOLD}🌡️  Temperature Report — ${hostname}${RESET}"
  if $IS_VM; then
    printf " ${YELLOW}[VM: ${VIRT}]${RESET}"
  fi
  printf "\n"
  printf '%*s\n' 50 '' | tr ' ' '─'

  if [ ${#READINGS[@]} -eq 0 ]; then
    printf "  ${YELLOW}No temperature sensors found.${RESET}\n"
    if $IS_VM; then
      printf "  ${CYAN}This is a VM — thermal sensors are not passed through by default.\n"
      printf "  Change CPU type to 'host' in Proxmox to expose them.${RESET}\n"
    fi
    printf '%*s\n' 50 '' | tr ' ' '─'
    return
  fi

  for entry in "${READINGS[@]}"; do
    local label="${entry%%|*}"
    local rest="${entry#*|}"
    local val="${rest%%|*}"
    local src="${rest##*|}"
    printf "  %-30s " "$label"
    colour_temp "$val"
    printf "  ${CYAN}[%s]${RESET}\n" "$src"
  done

  printf '%*s\n' 50 '' | tr ' ' '─'
  local max; max=$(get_max)
  printf "  ${BOLD}Max: "; colour_temp "$max"; printf "${RESET}\n\n"
}

output_json() {
  local hostname; hostname=$(hostname)
  local max; max=$(get_max)

  printf '{"hostname":"%s","is_vm":%s,"virt":"%s","max_temp":%s,"sensors":[\n' \
    "$hostname" "$IS_VM" "$VIRT" "$max"

  local count=${#READINGS[@]}
  local i=0
  for entry in "${READINGS[@]}"; do
    local label="${entry%%|*}"
    local rest="${entry#*|}"
    local val="${rest%%|*}"
    local src="${rest##*|}"
    (( i++ )) || true
    local comma=","
    [ "$i" -eq "$count" ] && comma=""
    printf '  {"label":"%s","temp":%s,"source":"%s"}%s\n' \
      "$label" "$val" "$src" "$comma"
  done

  # If no sensors, output null for max_temp
  if [ ${#READINGS[@]} -eq 0 ]; then
    # Reprint without the partial sensors block
    printf '],"note":"no sensors available"}\n'
  else
    printf ']}\n'
  fi
}

output_max() {
  if [ ${#READINGS[@]} -eq 0 ]; then
    echo "null"
    return
  fi
  get_max
}

# ── Entry Point ───────────────────────────────────────────────────────────────

MODE="human"
WATCH=false

for arg in "$@"; do
  case "$arg" in
    --json)  MODE="json"  ;;
    --max)   MODE="max"   ;;
    --watch) WATCH=true   ;;
    --help|-h)
      echo "Usage: $0 [--json | --max | --watch]"
      echo "  (no flag)  Human-readable coloured summary"
      echo "  --json     JSON output for scripts/Brain"
      echo "  --max      Single highest temp value only"
      echo "  --watch    Refresh every 2s (Ctrl+C to stop)"
      exit 0
      ;;
  esac
done

run_once() {
  READINGS=()   # reset for watch mode
  collect_all
  case "$MODE" in
    json)  output_json  ;;
    max)   output_max   ;;
    *)     output_human ;;
  esac
}

if $WATCH; then
  while true; do
    clear
    run_once
    sleep 2
  done
else
  run_once
fi