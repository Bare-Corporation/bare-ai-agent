#!/usr/bin/env bash
############################################################
#    ____ _                 _ _      _            ____     #
#   / ___| | ___  _    _  ___| (_)_ __ | |_        / ___|___    #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|      | |   / _ \   #
#  | |___| | (_) | |_| | (__| | | | | | |_       | |__| (_) |  #
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|       \____\___/    #
#                                                          #
#            Hybrid Bare-AI-Agent Installer                #
#                                                          #
############################################################
set -euo pipefail

# Define colors
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

echo -e "${GREEN}Starting BARE-AI setup...${NC}"

# --- USER CHOICE: ENGINE SELECTION ---
echo -e "\n${YELLOW}Select your AI Engine:${NC}"
echo -e "1) Bare-AI-CLI (Sovereign, Local-First, Vault-Integrated)"
echo -e "2) Gemini-CLI (Standard Google Cloud SDK)"
read -p "Enter choice [1 or 2]: " ENGINE_CHOICE

# Define Directories
BARE_AI_DIR="$HOME/.bare-ai"
BIN_DIR="$BARE_AI_DIR/bin"
LOG_DIR="$BARE_AI_DIR/logs"
CLI_REPO_DIR="$HOME/bare-ai-cli"

execute_command() {
    local cmd="$1"
    local description="$2"
    echo -e "\n${YELLOW}Action: $description${NC}"
    eval "$cmd"
}

# 1. Base Infrastructure
execute_command "mkdir -p \"$BARE_AI_DIR/diary\" \"$LOG_DIR\" \"$BIN_DIR\"" "Create filesystem"

# 2. Engine Installation Logic
if [ "$ENGINE_CHOICE" == "1" ]; then
    echo -e "${GREEN}Configuring Sovereign Bare-AI Engine...${NC}"
    if [ ! -d "$CLI_REPO_DIR" ]; then
        echo -e "${RED}Error: Local repo not found at $CLI_REPO_DIR. Please clone it first.${NC}"
        exit 1
    fi
    ENGINE_CMD="cd $CLI_REPO_DIR && node sovereign.js"
else
    echo -e "${YELLOW}Installing Google Gemini-CLI via npm...${NC}"
    sudo npm install -g @google/gemini-cli
    ENGINE_CMD="gemini"
fi

# 3. Deploy Sovereign Constitution
CONSTITUTION_CONTENT="# MISSION
You are Bare-AI, an autonomous agent managing local datacenter infrastructure. [cite: 6]
# OPERATIONAL RULES
1. Use local tools (ls, cat, grep) to verify state. [cite: 6, 11]
2. Prioritize SearXNG for web search if BARE_AI_SEARCH_URL is set. [cite: 37]
3. Log daily to ~/.bare-ai/diary/{{DATE}}.md. [cite: 11]"

echo -e "$CONSTITUTION_CONTENT" > "$BARE_AI_DIR/constitution.md"

# 4. Modify .bashrc with Loader
BASHRC_FILE="$HOME/.bashrc"
echo -e "${YELLOW}Updating $BASHRC_FILE...${NC}"

BASHRC_FUNCTION=$(cat << INNER_EOF

# --- BARE-AI LOADER ---
bare() {
    local MODEL="\${1:-granite}"
    local TODAY=\$(date +%Y-%m-%d)
    
    # Sovereign Logic: Set Vault paths and tool-rules [cite: 21, 32]
    case "\$MODEL" in
        energy)  export VAULT_SECRET_PATH="secret/data/tir-na-ai/config"; export BARE_AI_NO_TOOLS="true" ;;
        loco)    export VAULT_SECRET_PATH="secret/data/tir-na-ai-fast/config"; export BARE_AI_NO_TOOLS="true" ;;
        granite) export VAULT_SECRET_PATH="secret/data/granite/config"; export BARE_AI_NO_TOOLS="false" ;;
    esac

    export BARE_AI_CONSTITUTION="$BARE_AI_DIR/constitution.md"
    
    # Execute chosen engine
    if [ "$ENGINE_CHOICE" == "1" ]; then
        $ENGINE_CMD
    else
        $ENGINE_CMD -i "\$(cat \$BARE_AI_CONSTITUTION | sed "s|{{DATE}}|\$TODAY|")"
    fi
}
INNER_EOF
)

if ! grep -q "# --- BARE-AI LOADER ---" "$BASHRC_FILE"; then
    echo -e "$BASHRC_FUNCTION" >> "$BASHRC_FILE"
fi

echo -e "${GREEN}Setup Complete.${NC}"
echo -e "Run: ${YELLOW}source ~/.bashrc${NC} then ${YELLOW}bare granite${NC}"
