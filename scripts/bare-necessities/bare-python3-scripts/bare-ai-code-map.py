#!/usr/bin/env python3
#############################################################
#    ____ _                  _ _       _         ____       #
#   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#  Python Structure Extractor (Context Optimizer)           #
#  Version: v1.1.0 | Updated: 2026-04-12                    #
#############################################################
#  by the Cloud Integration Corporation                     #
#############################################################

import ast
import sys

def map_code(filename):
    try:
        with open(filename, "r") as f:
            tree = ast.parse(f.read())
        
        print(f"--- Structure of {filename} ---")
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                print(f"Class: {node.name}")
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        print(f"  - Method: {item.name}")
            elif isinstance(node, ast.FunctionDef):
                print(f"Function: {node.name}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        map_code(sys.argv[1])
    else:
        print("Usage: code-map <filename.py>")