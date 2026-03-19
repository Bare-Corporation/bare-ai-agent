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
# Repo root is two levels up from scripts/worker/
REPO_DIR="$(cd "$SOURCE_DIR/../.." && pwd)"
TEMPLATES_DIR="$REPO_DIR/scripts/templates"

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

# --- 5. CONSTITUTIONS ---
# technical-constitution.md — base Linux rules, managed by bare-ai-agent, read-only
# role.md                   — node personality, user-owned, never overwritten

echo -e "${YELLOW}Deploying technical constitution...${NC}"
TECH_CONST_SRC="$TEMPLATES_DIR/technical-constitution.md"
TECH_CONST_DEST="$BARE_AI_DIR/technical-constitution.md"

if [ -f "$TECH_CONST_SRC" ]; then
    # Always overwrite technical constitution — it is managed by the repo
    cp "$TECH_CONST_SRC" "$TECH_CONST_DEST"
    chmod 444 "$TECH_CONST_DEST"
    echo -e "${GREEN}✓ Technical constitution deployed (read-only)${NC}"
else
    echo -e "${RED}❌ Error: technical-constitution.md not found at $TECH_CONST_SRC${NC}"
    exit 1
fi

echo -e "${YELLOW}Checking role constitution...${NC}"
ROLE_CONST="$BARE_AI_DIR/role.md"
ROLE_STARTER="$TEMPLATES_DIR/role-starter.md"

if [ ! -f "$ROLE_CONST" ]; then
    if [ -f "$ROLE_STARTER" ]; then
        cp "$ROLE_STARTER" "$ROLE_CONST"
        echo -e "${GREEN}✓ Starter role constitution created at ~/.bare-ai/role.md${NC}"
        echo -e "${YELLOW}  → Please edit ~/.bare-ai/role.md to define this node's personality and mission.${NC}"
    else
        echo -e "${YELLOW}⚠️  Role starter template not found — creating blank role.md${NC}"
        echo "# BARE-AI ROLE CONSTITUTION
# Edit this file to define this agent's role and personality." > "$ROLE_CONST"
    fi
else
    echo -e "${GREEN}✓ Role constitution already exists — not overwritten${NC}"
fi

# --- 6. README ---
echo -e "${YELLOW}Writing README.md...${NC}"
cat << 'README_EOF' > "$BARE_AI_DIR/README.md"
# BARE-AI Setup and Configuration

This directory stores the persistent configuration and memory for the BARE-AI agent.

## Directory Structure
- **technical-constitution.md** — Core Linux tool rules (read-only, managed by bare-ai-agent)
- **role.md**                  — Agent personality and mission (edit freely, never overwritten)
- **diary/**                   — Daily activity logs
- **logs/**                    — JSON telemetry per command execution
- **bin/**                     — Local artifacts (bare-summarize, etc.)
- **config**                   — Agent config (AGENT_ID, ENGINE_TYPE)

## Customising Your Agent
Edit ~/.bare-ai/role.md to define this agent's personality, mission, and domain rules.
The technical-constitution.md is managed by the repo — do not edit it directly.

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
    local TECH_CONST="$HOME/.bare-ai/technical-constitution.md"
    local ROLE_CONST="$HOME/.bare-ai/role.md"
    local DIARY="$HOME/.bare-ai/diary/$TODAY.md"
    local CONFIG="$HOME/.bare-ai/config"

    mkdir -p "$(dirname "$DIARY")"
    touch "$DIARY"

    if [ ! -f "$TECH_CONST" ]; then
        echo -e "\033[0;31mError: Technical constitution not found at $TECH_CONST\033[0m"
        echo -e "\033[0;31mRe-run setup_bare-ai-worker.sh to restore it.\033[0m"
        return 1
    fi
    if [ ! -f "$ROLE_CONST" ]; then
        echo -e "\033[1;33mWarning: No role constitution at $ROLE_CONST — running with technical only.\033[0m"
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

    export BARE_AI_CONSTITUTION="$TECH_CONST"
    export BARE_AI_ROLE_CONSTITUTION="$ROLE_CONST"
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
        local combined_const
        combined_const=$(sed "s|{{DATE}}|$TODAY|g" "$TECH_CONST")
        if [ -f "$ROLE_CONST" ]; then
            combined_const="${combined_const}"$'\n\n---\n\n'"$(sed "s|{{DATE}}|$TODAY|g" "$ROLE_CONST")"
        fi
        gemini -m gemini-2.5-flash-lite -i "$combined_const"
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
alias bare-role='${EDITOR:-nano} ~/.bare-ai/role.md'
alias bare-constitution='cat ~/.bare-ai/technical-constitution.md'
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
echo -e "5. ${YELLOW}Edit role:${NC}    bare-role  (customise your agent personality)"
echo -e "4. ${YELLOW}Engine type:${NC}  $ENGINE_TYPE"
exit 0