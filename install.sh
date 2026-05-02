#!/usr/bin/env bash
#############################################################
#    ____ _                  _ _       _         ____       #
#   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#                                                           #
#  by the Cloud Integration Corporation                     #
#############################################################
set -e

GREEN="\033[0;32m"
YELLOW="\033[1;33m"
NC="\033[0m"

echo -e "${YELLOW}Starting BARE-AI Agent Bootstrap...${NC}"

# Detect user home directory (fixes sudo execution)
TARGET_USER="${SUDO_USER:-$USER}"
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
REPO_DIR="$TARGET_HOME/bare-ai-agent"

# 1. Clone or update the repository
if [ ! -d "$REPO_DIR" ]; then
    echo -e "${YELLOW}Cloning repository to $REPO_DIR...${NC}"
    git clone https://github.com/Bare-Corporation/bare-ai-agent.git "$REPO_DIR"
else
    echo -e "${GREEN}Repository found. Pulling latest updates...${NC}"
    cd "$REPO_DIR" && git pull origin main
fi

# 2. Execute the worker installer
echo -e "${YELLOW}Launching BARE-AI Worker Installer...${NC}"
cd "$REPO_DIR/scripts/worker"
chmod +x setup_bare-ai-worker.sh
./setup_bare-ai-worker.sh "$@" < /dev/tty