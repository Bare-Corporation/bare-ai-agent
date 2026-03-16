#!/usr/bin/env bash

############################################################
#    ____ _                  _ _       _        ____       #
#   / ___| | ___  _    _  ___| (_)_ __ | |_      / ___|___  #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#  Hybrid Bare-AI-Agent Installer                           #
#  by the Cloud Integration Corporation                     #
############################################################
# ==============================================================================
# SCRIPT NAME:    setup_bare-ai-worker.sh
# DESCRIPTION:    bare-ai-worker "Apex" Installer (Level 4 Autonomy)
# AUTHOR:         Cian Egan
# DATE:           2026-02-01
# VERSION:        5.1.0-Enterprise (Hybrid Engine Choice + Full Autonomy)
# ==============================================================================
set -euo pipefail

# --- DOCKER WARNING ---
if [ ! -f "/.dockerenv" ]; then
    echo -e "\033[1;33mWarning: Running on host system. For enhanced security, Bare-ERP recommends running within Docker.\033[0m"
fi

# --- COLORS ---
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

echo -e "${GREEN}Starting BARE-AI setup...${NC}"

# --- ENGINE SELECTION ---
echo -e "\n${YELLOW}Select your AI Engine:${NC}"
echo -e "1) Bare-AI-CLI (Sovereign, Local-First, Vault-Integrated)"
echo -e "2) Gemini-CLI (Standard Google Cloud SDK)"
read -rp "Enter choice [1 or 2]: " ENGINE_CHOICE

# --- DIRECTORY DEFINITIONS ---
WORKSPACE_DIR="$HOME/.bare-ai"
BARE_AI_DIR="$WORKSPACE_DIR"
BIN_DIR="$BARE_AI_DIR/bin"
LOG_DIR="$BARE_AI_DIR/logs"
DIARY_DIR="$BARE_AI_DIR/diary"
CONFIG_FILE="$BARE_AI_DIR/config"
CLI_REPO_DIR="$HOME/bare-ai-cli"

# --- SOURCE DIR DETECTION (Path Paradox Fix) ---
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SOURCE_DIR="$(pwd)"
fi

# --- HELPER: execute_command ---
# Runs a command autonomously, logs result as JSON, honours set -e
execute_command() {
    local cmd="$1"
    local description="$2"

    echo -e "\n${YELLOW}Action: $description${NC}"
    echo -e "  Command: $cmd"

    mkdir -p "$LOG_DIR"

    local exit_code=0
    eval "$cmd" || exit_code=$?

    local log_file="$LOG_DIR/$(date +'%Y%m%d_%H%M%S')_$(date +%N | cut -c1-3).log"
    local status="success"
    [ $exit_code -ne 0 ] && status="failed"

    printf '{ "timestamp": "%s", "command": "%s", "description": "%s", "status": "%s", "exit_code": %d }\n' \
        "$(date +'%Y-%m-%dT%H:%M:%S%z')" \
        "$(echo "$cmd"         | sed 's/"/\\"/g')" \
        "$(echo "$description" | sed 's/"/\\"/g')" \
        "$status" \
        "$exit_code" > "$log_file"

    if [ $exit_code -ne 0 ]; then
        echo -e "${RED}Error executing command (exit $exit_code): $cmd${NC}"
        return $exit_code
    fi
    echo -e "${GREEN}✓ Done${NC}"
}

# --- 1. DIRECTORY SETUP ---
echo -e "${YELLOW}Creating BARE-AI directory structure...${NC}"
execute_command "mkdir -p \"$DIARY_DIR\" \"$LOG_DIR\" \"$BIN_DIR\"" "Create diary, logs, and bin directories"

if [ ! -d "$BARE_AI_DIR" ] || [ ! -d "$DIARY_DIR" ] || [ ! -d "$LOG_DIR" ] || [ ! -d "$BIN_DIR" ]; then
    echo -e "${RED}Error: Failed to create BARE-AI directories. Exiting.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Directory structure created${NC}"

# --- 2. ARTIFACT INSTALLATION ---
ARTIFACT_NAME="bare-summarize"
DEST_BIN="$BIN_DIR/$ARTIFACT_NAME"

echo -e "${YELLOW}Resolving artifact: $ARTIFACT_NAME...${NC}"

if [ -f "$SOURCE_DIR/$ARTIFACT_NAME" ]; then
    echo -e "${GREEN}Found artifact in source directory.${NC}"
    execute_command "cp \"$SOURCE_DIR/$ARTIFACT_NAME\" \"$DEST_BIN\"" "Install artifact from source dir"

elif [ -f "$(pwd)/$ARTIFACT_NAME" ]; then
    echo -e "${GREEN}Found artifact in current working directory.${NC}"
    execute_command "cp \"$(pwd)/$ARTIFACT_NAME\" \"$DEST_BIN\"" "Install artifact from cwd"

else
    echo -e "${YELLOW}Artifact not found locally. Generating emergency stub...${NC}"
    cat << 'STUB' > "$DEST_BIN"
#!/bin/bash
echo "{\"timestamp\": \"$(date -u +"%Y-%m-%dT%H:%M:%SZ")\", \"status\": \"healthy\", \"telemetry\": \"stubbed\"}"
STUB
    echo -e "${YELLOW}Notice: Installed stub version of bare-summarize.${NC}"
fi

execute_command "chmod +x \"$DEST_BIN\"" "Make bare-summarize executable"

# --- 3. ENGINE INSTALLATION ---
if [ "$ENGINE_CHOICE" == "1" ]; then
    echo -e "${GREEN}Configuring Sovereign Bare-AI Engine...${NC}"

    # Ensure npm is available and up to date before attempting build
    # npm 9.x has a known bug with npm: alias in overrides - requires npm 10+
    if ! command -v npm &>/dev/null; then
        echo -e "${YELLOW}npm not found. Installing Node.js and npm...${NC}"
        execute_command "sudo apt-get update -qq && sudo apt-get install -y -qq nodejs npm" "Install Node.js and npm"
    fi

    NPM_MAJOR=$(npm --version 2>/dev/null | cut -d. -f1)
    if [ "${NPM_MAJOR:-0}" -lt 10 ]; then
        echo -e "${YELLOW}npm version too old ($(npm --version)). Upgrading via n...${NC}"
        execute_command "sudo npm install -g n" "Install n (node version manager)"
        execute_command "sudo n stable" "Upgrade Node.js to stable"
        hash -r 2>/dev/null || true
        echo -e "${GREEN}✓ Node.js and npm upgraded (npm $(npm --version))${NC}"
    else
        echo -e "${GREEN}✓ npm $(npm --version) - OK${NC}"
    fi

    if [ ! -d "$CLI_REPO_DIR" ]; then
        echo -e "${YELLOW}CLI not found. Cloning sovereign engine from GitHub...${NC}"
        execute_command "git clone https://github.com/Cian-CloudIntCorp/bare-ai-cli.git \"$CLI_REPO_DIR\"" "Clone Bare-AI-CLI"
    else
        echo -e "${GREEN}Existing CLI found. Pulling latest...${NC}"
        execute_command "cd \"$CLI_REPO_DIR\" && git pull origin main" "Update Bare-AI-CLI"
    fi

    execute_command "cd \"$CLI_REPO_DIR\" && npm install && npm run build && npm run bundle" "Build Sovereign Engine"
    ENGINE_TYPE="sovereign"

else
    echo -e "${YELLOW}Configuring Gemini-CLI...${NC}"

    if ! command -v gemini &>/dev/null; then
        echo -e "${RED}Gemini CLI not found.${NC}"

        if command -v npm &>/dev/null; then
            echo -e "${YELLOW}Installing @google/gemini-cli via npm...${NC}"
            if execute_command "sudo npm install -g @google/gemini-cli" "Install Gemini CLI globally"; then
                echo -e "${GREEN}✓ Gemini CLI installed${NC}"
            else
                echo -e "${RED}Failed to install Gemini CLI. Ensure npm is available and you have sudo rights.${NC}"
                exit 1
            fi
        else
            echo -e "${RED}npm not found. Cannot install Gemini CLI. Exiting.${NC}"
            exit 1
        fi
    else
        echo -e "${GREEN}✓ Gemini CLI already installed${NC}"
    fi

    ENGINE_TYPE="cloud"
fi

# --- 4. AGENT CONFIG ---
if [ ! -f "$CONFIG_FILE" ]; then
    AGENT_ID=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "BARE-$(date +%s)-${RANDOM}")
    execute_command "printf 'AGENT_ID=%s\nENGINE_TYPE=%s\n' \"$AGENT_ID\" \"$ENGINE_TYPE\" > \"$CONFIG_FILE\"" "Write agent config"
    echo -e "${GREEN}✓ Agent config written (ID: $AGENT_ID)${NC}"
else
    echo -e "${YELLOW}⚠️  Config already exists, skipping ID generation${NC}"
fi

# --- 5. CONSTITUTION ---
# Written with single-quotes around heredoc delimiter so variables are NOT expanded at write time.
# The bare() function sources this at runtime where $HOME etc. resolve correctly.
echo -e "${YELLOW}Writing constitution.md...${NC}"
cat << 'CONST_EOF' > "$BARE_AI_DIR/constitution.md"
# MISSION
You are Bare-AI, an autonomous Linux Agent responsible for "Self-Healing" data pipelines.
Your goal is to fix data errors, convert formats, and verify integrity using standard Linux tools.

# OPERATIONAL RULES
1. **Tool First, Think Second:** Do not guess file contents. Use 'head', 'file', or 'grep' to inspect them first.
2. **Verification:** Never assume a conversion worked. Always run a check command (e.g., 'jq .' to verify JSON validity).
3. **Resource Efficiency:** Do not read files larger than 1MB into your context. Use 'split', 'awk', or 'sed'.
4. **Self-Correction:** If a command fails, read the error code, formulate a fix, and retry once.
5. **Updates:** Use 'sudo DEBIAN_FRONTEND=noninteractive' for updates.
6. **Sovereignty:** If using Bare-AI-CLI, prioritize SearXNG for web search if BARE_AI_SEARCH_URL is set.

# FORBIDDEN ACTIONS
- Do not use 'rm' on files outside the '/tmp' directory.
- Do not hallucinate library availability. Use 'dpkg -l' or 'pip list' to check before importing.

# DIARY RULES
1. Log all learnings and a succinct summary of actions to ~/.bare-ai/diary/{{DATE}}.md.
CONST_EOF
echo -e "${GREEN}✓ Constitution written${NC}"

# --- 6. README ---
echo -e "${YELLOW}Writing README.md...${NC}"
cat << 'README_EOF' > "$BARE_AI_DIR/README.md"
# BARE-AI Setup and Configuration

This directory stores the persistent configuration and memory for the BARE-AI agent.

## Directory Structure
- **constitution.md** — Core identity and operational rules
- **diary/**          — Daily activity logs
- **logs/**           — JSON telemetry per command execution
- **bin/**            — Local artifacts (bare-summarize, etc.)
- **config**          — Agent config (AGENT_ID, ENGINE_TYPE)

## Engine Selection
Two engines are supported:
- **Bare-AI-CLI** — Sovereign, local-first, Vault-integrated
- **Gemini-CLI**  — Standard Google Cloud SDK

## Gemini Setup (if using Gemini engine)
1. Install: `npm install -g @google/gemini-cli`
2. API Key:  add `export GEMINI_API_KEY="YOUR_KEY"` to `~/.bashrc`
README_EOF
echo -e "${GREEN}✓ README written${NC}"

# --- 7. TELEMETRY PING ---
# FIX: Use full https:// URL and suppress errors so set -e is not tripped on network issues
TELEMETRY_URL="https://www.bare-erp.com"
echo -e "${YELLOW}Pinging telemetry endpoint...${NC}"
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$TELEMETRY_URL" || echo "000")
echo -e "${GREEN}✓ Telemetry ping: HTTP $HTTP_CODE${NC}"

# --- 8. BASHRC UPDATES ---
BASHRC_FILE="$HOME/.bashrc"
echo -e "${YELLOW}Updating $BASHRC_FILE...${NC}"

# 8a. PATH entry
if ! grep -q "BARE-AI PATH" "$BASHRC_FILE"; then
    cat << 'PATH_EOF' >> "$BASHRC_FILE"

# BARE-AI PATH
if [ -d "$HOME/.bare-ai/bin" ] ; then
    PATH="$HOME/.bare-ai/bin:$PATH"
fi
PATH_EOF
    echo -e "${GREEN}✓ PATH entry added${NC}"
else
    echo -e "${YELLOW}⚠️  PATH entry already present, skipping${NC}"
fi

# 8b. bare() hybrid loader function
if ! grep -q "BARE-AI Hybrid Loader" "$BASHRC_FILE"; then
    cat << 'BARE_FUNC_EOF' >> "$BASHRC_FILE"

# BARE-AI Hybrid Loader
bare() {
    local MODEL="${1:-granite}"
    local TODAY=$(date +%Y-%m-%d)
    local CONSTITUTION="$HOME/.bare-ai/constitution.md"
    local DIARY="$HOME/.bare-ai/diary/$TODAY.md"
    local CONFIG="$HOME/.bare-ai/config"

    mkdir -p "$(dirname "$DIARY")"
    touch "$DIARY"

    if [ ! -f "$CONSTITUTION" ]; then
        echo -e "\033[0;31mError: Constitution not found at $CONSTITUTION\033[0m"
        return 1
    fi

    # Load engine type from config
    local ENGINE_TYPE="cloud"
    if [ -f "$CONFIG" ]; then
        source "$CONFIG"
    fi

    # Sovereign model/vault routing
    case "$MODEL" in
        energy)  export VAULT_SECRET_PATH="secret/data/tir-na-ai/config";      export BARE_AI_NO_TOOLS="true"  ;;
        loco)    export VAULT_SECRET_PATH="secret/data/tir-na-ai-fast/config"; export BARE_AI_NO_TOOLS="true"  ;;
        granite) export VAULT_SECRET_PATH="secret/data/granite/config";         export BARE_AI_NO_TOOLS="false" ;;
    esac

    export BARE_AI_CONSTITUTION="$CONSTITUTION"
    export BARE_AI_DIARY="$DIARY"

    if [ "$ENGINE_TYPE" = "sovereign" ]; then
        echo -e "\033[0;32m🤖 [Engine: Bare-AI CLI | Model: $MODEL]\033[0m"
        cd "$HOME/bare-ai-cli" && node sovereign.js
        # Log forwarding
        if [ -f "BARE.md" ]; then
            echo -e "\n--- SESSION APPENDED: $(date) [bare-ai | $MODEL] ---" >> "$DIARY"
            cat "BARE.md" >> "$DIARY"
            rm "BARE.md"
            echo -e "\033[0;32m📝 Session saved to Diary ($TODAY.md)\033[0m"
        fi
    else
        echo -e "\033[1;33m✨ [Engine: Gemini CLI | Model: gemini-2.5-flash-lite]\033[0m"
        local content
        content=$(sed "s|{{DATE}}|$TODAY|g" "$CONSTITUTION")
        gemini -m gemini-2.5-flash-lite -i "$content"
        # Log forwarding
        if [ -f "GEMINI.md" ]; then
            echo -e "\n--- SESSION APPENDED: $(date) [gemini] ---" >> "$DIARY"
            cat "GEMINI.md" >> "$DIARY"
            rm "GEMINI.md"
            echo -e "\033[0;32m📝 Session saved to Diary ($TODAY.md)\033[0m"
        fi
    fi
}

alias bare-status='echo "🔍 Local Telemetry Audit:"; bare-summarize | jq .'
BARE_FUNC_EOF
    echo -e "${GREEN}✓ bare() function added to .bashrc${NC}"
else
    echo -e "${YELLOW}⚠️  bare() already in .bashrc, skipping${NC}"
fi

# --- COMPLETE ---
echo -e "\n${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ BARE-AI WORKER SETUP COMPLETE${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "1. ${YELLOW}Reload:${NC}       source ~/.bashrc"
echo -e "2. ${YELLOW}Test artifact:${NC} bare-summarize"
echo -e "3. ${YELLOW}Run agent:${NC}    bare granite"
echo -e "4. ${YELLOW}Engine type:${NC}  $ENGINE_TYPE"
exit 0