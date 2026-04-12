#!/bin/bash
#############################################################
#    ____ _                  _ _       _         ____       #
#   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#  ZFS & NVMe Smart Health Audit                            #
#  Version: v1.0.0 | Updated: 2026-04-12                    #
#############################################################
#  by the Cloud Integration Corporation                     #
#############################################################
echo "--- ZFS Pool Status ---"
zpool list
echo -e "\n--- Drive Wear Levels ---"
for drive in /dev/nvme[0-9]; do
    echo -n "$drive: "
    smartctl -a $drive | grep "Percentage Used" || echo "No SMART data"
done