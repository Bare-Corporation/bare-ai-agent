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
for hwmon in /sys/class/hwmon/hwmon*; do
    name=$(cat "$hwmon/name" 2>/dev/null)
    for temp in "$hwmon"/temp*_input; do
        if [ -f "$temp" ]; then
            temp_val=$(cat "$temp" 2>/dev/null)
            if [ -n "$temp_val" ]; then
                # Divide by 1000 to get degrees Celsius
                celsius=$(echo "scale=1; $temp_val / 1000" | bc 2>/dev/null || echo $(($temp_val / 1000)))
                label_file="${temp%_input}_label"
                label="Temp"
                [ -f "$label_file" ] && label=$(cat "$label_file" 2>/dev/null)
                echo "$name ($label): $celsius°C"
            fi
        fi
    done
done