#!/bin/bash
#############################################################
#    ____ _                  _ _       _         ____       #
#   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#  Journalctl Severity Filter (1hr)                         #
#  Version: v1.0.2 | Updated: 2026-04-12                    #
#############################################################
#  by the Cloud Integration Corporation                     #
#############################################################
echo "--- System Errors (Last 1 Hour) ---"
journalctl -p 3 --since "1 hour ago" --no-pager
echo -e "\n--- Recent OOM Kills (RAM Issues) ---"
dmesg | grep -i "out of memory" | tail -n 3