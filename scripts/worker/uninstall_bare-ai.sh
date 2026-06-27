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
# VERSION:        5.4.5 (Proxmox-Safe Edition)
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
# Ultra-strict targeting: Only kills Node processes running specifically out of the bare-ai-cli folder
sudo pkill -f "bare-ai-cli/bundle/bare-ai.js" || true
sudo pkill -f "bare-ai-cli/sovereign.js" || true

echo -e "${YELLOW}2. Scrubbing ~/.bashrc overrides...${NC}"
# Surgically remove the exact block injected by the installer
sed -i '/# START: BARE-AI-AGENT WORKER BASHRC MODIFICATIONS:/,/# END: BARE-AI-AGENT WORKER BASHRC MODIFICATIONS:/d' ~/.bashrc

echo -e "${YELLOW}3. Hunting and destroying global symlinks...${NC}"
sudo rm -f /usr/local/bin/cpu-temp.sh
sudo rm -f /usr/local/bin/pve-check.sh
sudo rm -f /usr/local/bin/disk-health.sh
sudo rm -f /usr/local/bin/net-audit.sh
sudo rm -f /usr/local/bin/error-log.sh
sudo rm -f /usr/local/bin/grep_search
sudo rm -f /usr/local/bin/bare-thermal-guard
sudo rm -f /usr/local/bin/ai-monitor.py
sudo rm -f /usr/local/bin/code-map.py
sudo rm -f /usr/local/bin/pve-json.py

echo -e "${YELLOW}4. Removing Thermal Guard Cronjob...${NC}"
if command -v crontab &>/dev/null; then
    crontab -l 2>/dev/null | grep -v "bare-thermal-guard" | crontab - || true
fi

echo -e "${YELLOW}5. Wiping workspaces and hidden engine caches...${NC}"
cd ~
rm -rf ~/bare-ai-agent 
rm -rf ~/bare-ai-cli 
rm -rf ~/.bare-ai 
rm -rf ~/.config/bare-ai

# Optional: Prompt to remove standard Gemini CLI config to prevent affecting non-bare workflows
read -p "Do you also want to clear the standard Google Gemini CLI cache (~/.config/gemini)? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    rm -rf ~/.config/gemini
    rm -rf ~/.gemini
    echo -e "${GREEN}Standard Gemini cache cleared.${NC}"
fi

echo -e "${GREEN}✅ UNINSTALLATION COMPLETE.${NC}"
echo -e "The agent and all ghost paths have been permanently removed."
echo -e "Run ${YELLOW}source ~/.bashrc${NC} to refresh your current terminal session."