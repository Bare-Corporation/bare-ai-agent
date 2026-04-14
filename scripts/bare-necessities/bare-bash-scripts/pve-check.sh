#!/bin/bash
#############################################################
#    ____ _                  _ _       _         ____       #
#   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#  PVE Health & Resource Monitor                            #
#  Version: v1.0.2 | Updated: 2026-04-14                    #
#############################################################
#  by the Cloud Integration Corporation                     #
#############################################################
echo "--- Virtual Machines (qm) ---"
sudo /usr/sbin/qm list | awk 'NR==1 || $3=="running"'
echo -e "\n--- Containers (pct) ---"
sudo /usr/sbin/pct list | awk 'NR==1 || $3=="status" || $3=="running"'
echo -e "\n--- Top RAM Consumers ---"
ps -eo pmem,pcpu,comm --sort=-pmem | head -n 6