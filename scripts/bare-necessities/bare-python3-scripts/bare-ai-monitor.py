#!/usr/bin/env python3
#############################################################
#    ____ _                  _ _       _         ____       #
#   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#  Journalctl Severity Filter (1hr)                         #
#  AI Model & VRAM/RAM Pressure Monitor                     #
#  Version: v1.0.0 | Updated: 2026-04-12                    #
#############################################################
#  by the Cloud Integration Corporation                     #
#############################################################

import psutil
import os

def get_size(bytes, suffix="B"):
    factor = 1024
    for unit in ["", "K", "M", "G", "T"]:
        if bytes < factor:
            return f"{bytes:.2f}{unit}{suffix}"
        bytes /= factor

print("--- Memory Pressure ---")
svmem = psutil.virtual_memory()
print(f"Total: {get_size(svmem.total)} | Available: {get_size(svmem.available)} | Usage: {svmem.percent}%")

print("\n--- AI Related Processes ---")
found = False
for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
    # Looking for common local AI engines
    if any(x in proc.info['name'].lower() for x in ['ollama', 'llama', 'gemma', 'python']):
        mem = get_size(proc.info['memory_info'].rss)
        if "G" in mem or "M" in mem: # Only show significant processes
            print(f"PID: {proc.info['pid']} | Name: {proc.info['name']} | Mem: {mem}")
            found = True
if not found:
    print("No major AI processes detected.")