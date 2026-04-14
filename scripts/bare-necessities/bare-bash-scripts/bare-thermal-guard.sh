#!/usr/bin/env bash
#############################################################
#    ____ _                  _ _       _         ____       #
#   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#                                                           #
#  by the Cloud Integration Corporation                     #
#############################################################
# ==============================================================================
# SCRIPT NAME:    bare-thermal-guard.sh
# DESCRIPTION:    Parses the cpu-temp.sh and pulls the emergency brake if things get too hot. Assumes ai is using CPU/iGPU.
# AUTHOR:         Cian Egan
# DATE:           2026-04-14
# VERSION:        5.3.0 (Sovereign Autonomy Edition)
#
# CHANGELOG (5.2.0 -> 5.3.0):
# - Added(git): Added to reduce silicone degradation.
# ==============================================================================
# bare-thermal-guard.sh - Emergency shutoff for AI processes
# Threshold: 100°C

MAX_TEMP=$(/usr/local/bin/cpu-temp.sh | grep -Ei "(Tctl|edge|Composite)" | awk '{print $NF}' | sed 's/°C//g' | sort -nr | head -n 1 | cut -d. -f1)

# If temp is empty (error), default to 0 to stay safe
MAX_TEMP=${MAX_TEMP:-0}

THRESHOLD=100

if [ "$MAX_TEMP" -ge "$THRESHOLD" ]; then
    echo "$(date): 🔥 THERMAL CRITICAL ($MAX_TEMP°C). Shutting down AI agents." >> /var/log/bare-thermal.log
    
    # Kill the agent and any rogue node processes
    sudo pkill -f bare-ai-cli
    sudo pkill -f node
    
    # Notify the user (if they are logged in)
    wall "⚠️ BARE-AI EMERGENCY SHUTOFF: CPU hit $MAX_TEMP°C. Processes terminated for hardware safety."
fi