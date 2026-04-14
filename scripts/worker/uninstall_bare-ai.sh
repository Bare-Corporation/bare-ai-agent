#!/usr/bin/env bash
#############################################################
#    ____ _                  _ _       _         ____       #
#   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#  Hybrid Bare-AI-Agent Worker Uninstaller                  #
#  by the Cloud Integration Corporation                     #
#############################################################
# ==============================================================================
# SCRIPT NAME:    uninstall_bare-ai.sh (alias: bare-uninstall via .bashrc)
# DESCRIPTION:    Completely removes the Bare-AI Agent, tools, and environments.
# AUTHOR:         Cian Egan
# DATE:           2026-04-14
# VERSION:        5.3.0 
#
# CHANGELOG (5.2.0 -> 5.3.0):
# - Added(git): During testing the req. for an Uninstaller script became clear to remove old files in case of a reinstall. 
# ==============================================================================

YELLOW='\033[1;33m'
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${RED}WARNING: This will completely destroy the Bare-AI Agent, its global toolkits, and all workspaces on this node.${NC}"
read -p "Are you sure you want to proceed? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${GREEN}Uninstallation aborted. Your agent is safe.${NC}"
    exit 1
fi

echo -e "${YELLOW}1. Terminating active agent processes...${NC}"
sudo pkill -f bare-ai-cli || true
sudo pkill -f node || true

echo -e "${YELLOW}2. Scrubbing ~/.bashrc overrides...${NC}"
sed -i '/# BARE-AI PATH/,/fi/d' ~/.bashrc
sed -i '/# BARE-AI Hybrid Loader/,/^alias bare-constitution.*/d' ~/.bashrc

echo -e "${YELLOW}3. Hunting and destroying global symlinks...${NC}"
sudo rm -f /usr/local/bin/cpu-temp.sh
sudo rm -f /usr/local/bin/pve-check.sh
sudo rm -f /usr/local/bin/disk-health.sh
sudo rm -f /usr/local/bin/net-audit.sh
sudo rm -f /usr/local/bin/error-log.sh
sudo rm -f /usr/local/bin/ai-monitor.py
sudo rm -f /usr/local/bin/code-map.py
sudo rm -f /usr/local/bin/pve-json.py

echo -e "${YELLOW}4. Wiping workspaces and hidden engine caches...${NC}"
cd ~
rm -rf ~/bare-ai-agent ~/bare-ai-cli ~/.bare-ai ~/.gemini

echo -e "${GREEN}✅ UNINSTALLATION COMPLETE.${NC}"
echo -e "The agent and all ghost paths have been permanently removed."
echo -e "Run ${YELLOW}exec bash -l${NC} to refresh your current terminal session."