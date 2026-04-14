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
# SCRIPT NAME:    grep_search.sh
# DESCRIPTION:    This allows the AI to search that larege code lines fast. Saving your token costs and preventing crashes.
# AUTHOR:         Cian Egan
# DATE:           2026-04-14
# VERSION:        5.3.0 (Sovereign Autonomy Edition)
#
# CHANGELOG (5.2.0 -> 5.3.0):
# - Added(git): For use with smaller models especially.
# ==============================================================================
# grep_search.sh - High-performance search for Bare-AI Agent
# Usage: grep_search "pattern" /path/to/file

PATTERN=$1
FILE=$2

if [ -z "$PATTERN" ] || [ -z "$FILE" ]; then
    echo "Usage: grep_search 'pattern' /path/to/file"
    exit 1
fi

echo "--- Searching for '$PATTERN' in $FILE ---"
# Returns line numbers and the matching lines
grep -nEi "$PATTERN" "$FILE" | head -n 50 || echo "No matches found."