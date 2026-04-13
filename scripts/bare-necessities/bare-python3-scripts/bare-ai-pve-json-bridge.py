#!/usr/bin/env python3
#############################################################
#    ____ _                  _ _       _         ____       #
#   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#  PVE to JSON Data Bridge for LLM Analysis                 #
#  Version: v1.0.0 | Updated: 2026-04-12                    #
#############################################################
#  by the Cloud Integration Corporation                     #
#############################################################

import subprocess
import json

def get_pve_data():
    try:
        # Get VM list from shell
        output = subprocess.check_output(["qm", "list"]).decode("utf-8")
        lines = output.strip().split("\n")[1:]
        vms = []
        for line in lines:
            parts = line.split()
            vms.append({
                "vmid": parts[0],
                "name": parts[1],
                "status": parts[2],
                "mem_mb": parts[3]
            })
        return json.dumps({"proxmox_vms": vms}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})

print(get_pve_data())