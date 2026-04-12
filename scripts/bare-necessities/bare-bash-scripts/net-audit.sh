#!/bin/bash
#############################################################
#    ____ _                  _ _       _         ____       #
#   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#  Network Topology & Service Audit                         #
#  Version: v1.0.0 | Updated: 2026-04-12                    #
#############################################################
#  by the Cloud Integration Corporation                     #
#############################################################
echo "--- Local IP Addresses ---"
hostname -I
echo -e "\n--- Ports Listening Locally ---"
ss -tulpn | grep LISTEN | awk '{print $5}' | cut -d: -f2 | sort -u | xargs
echo -e "\n--- External Connectivity ---"
ping -c 1 1.1.1.1 > /dev/null && echo "Internet: UP" || echo "Internet: DOWN"