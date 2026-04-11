#!/usr/bin/env bash
#############################################################
#    ____ _                  _ _       _        ____        #
#   / ___| | ___  _    _  ___| (_)_ __ | |_      / ___|___  #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#  Hybrid Bare-AI-Brain Worker Installer                    #
#  by the Cloud Integration Corporation                     #
#############################################################
# ==============================================================================
# SCRIPT NAME:    setup_bare-brain.sh
# VERSION:        5.1.1-Vault-Integrated
# DESCRIPTION:    Installs Brain v5 with dual constitution and Vault integration.
#                 Note a premiem version of the brain is available on request written in GO for massive agent deployments.
#                 contact us today! 
# ==============================================================================
set -euo pipefail
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

WORKSPACE_DIR="$HOME/.bare-ai"
BIN_DIR="$WORKSPACE_DIR/bin"
LOG_DIR="$WORKSPACE_DIR/logs"
CONFIG_DIR="$WORKSPACE_DIR/config"
FLEET_FILE="$WORKSPACE_DIR/fleet.conf"
BASHRC_FILE="$HOME/.bashrc"

# Derive repo root from script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATES_DIR="$REPO_DIR/scripts/templates"

echo -e "${GREEN}Installing Bare-AI Brain (v5.1.0)...${NC}"
mkdir -p "$LOG_DIR" "$BIN_DIR" "$CONFIG_DIR"

# --- 0. DEPENDENCY CHECK ---
if ! command -v jq &>/dev/null; then
    echo -e "${YELLOW}Installing jq...${NC}"
    sudo apt-get update -qq && sudo apt-get install -y -qq jq
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

# --- VAULT PRE-FLIGHT CHECK ---
echo -e "${YELLOW}Checking Vault configuration...${NC}"
VAULT_ENV_FILE="$HOME/.bare-ai/config/vault.env"
mkdir -p "$(dirname "$VAULT_ENV_FILE")"

# Create vault.env stub if it doesn't exist
if [ ! -f "$VAULT_ENV_FILE" ]; then
    cat << 'VAULT_STUB_EOF' > "$VAULT_ENV_FILE"
# Bare-AI Vault Credentials
# Fill in your Vault details and re-run the installer
export VAULT_ADDR=https://your-vault-address:8200
export VAULT_ROLE_ID=your-role-id-here
export VAULT_SECRET_ID=your-secret-id-here
VAULT_STUB_EOF
    echo -e "${YELLOW}⚠️  Vault credentials file created at $VAULT_ENV_FILE${NC}"
    echo -e "${YELLOW}   Please fill in your Vault details before running 'bare'.${NC}"
else
    echo -e "${GREEN}✓ Vault credentials file exists${NC}"
fi

# Source vault.env and test connectivity if VAULT_ADDR is set and not a placeholder
source "$VAULT_ENV_FILE" 2>/dev/null || true
if [ -n "${VAULT_ADDR:-}" ] && [ "$VAULT_ADDR" != "https://your-vault-address:8200" ]; then
    if curl -s -k --max-time 5 "$VAULT_ADDR/v1/sys/health" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Vault reachable at $VAULT_ADDR${NC}"
    else
        echo -e "${YELLOW}⚠️  Vault not reachable at $VAULT_ADDR${NC}"
        echo -e "${YELLOW}   The agent will install but 'bare' will fail until Vault is accessible.${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  Vault address not configured — edit $VAULT_ENV_FILE before running 'bare'.${NC}"
fi

# --- 1. PURGE OLD DEBRIS ---
rm -f "$WORKSPACE_DIR/brain_constitution.md"
rm -f "$WORKSPACE_DIR/constitution.md"

# --- 2. DEPLOY TECHNICAL CONSTITUTION (read-only, managed by repo) ---
echo -e "${YELLOW}Deploying technical constitution...${NC}"
TECH_CONST_SRC="$TEMPLATES_DIR/technical-constitution.md"
TECH_CONST_DEST="$WORKSPACE_DIR/technical-constitution.md"

if [ -f "$TECH_CONST_SRC" ]; then
    chmod 644 "$TECH_CONST_DEST" 2>/dev/null || true
    cp "$TECH_CONST_SRC" "$TECH_CONST_DEST"
    chmod 444 "$TECH_CONST_DEST"
    echo -e "${GREEN}✓ Technical constitution deployed (read-only)${NC}"
else
    echo -e "${RED}❌ Error: technical-constitution.md not found at $TECH_CONST_SRC${NC}"
    exit 1
fi

# --- 3. ROLE CONSTITUTION (user-owned, never overwritten) ---
echo -e "${YELLOW}Checking role constitution...${NC}"
ROLE_CONST="$WORKSPACE_DIR/role.md"
ROLE_STARTER="$TEMPLATES_DIR/role-starter.md"

if [ ! -f "$ROLE_CONST" ]; then
    if [ -f "$ROLE_STARTER" ]; then
        cp "$ROLE_STARTER" "$ROLE_CONST"
        echo -e "${GREEN}✓ Starter role constitution created at ~/.bare-ai/role.md${NC}"
        echo -e "${YELLOW}  → Edit ~/.bare-ai/role.md to define the Brain's role and Proxmox rules.${NC}"
    else
        echo "# BARE-AI ROLE CONSTITUTION\n# Edit this file to define the Brain agent's role." > "$ROLE_CONST"
    fi
else
    echo -e "${GREEN}✓ Role constitution already exists — not overwritten${NC}"
fi

# --- 4. COMPILE BRAIN LOGIC (VAULT EDITION) ---
BRAIN_SCRIPT="$BIN_DIR/bare-brain"

cat << 'INNER_EOF' > "$BRAIN_SCRIPT"
#!/bin/bash
set -e

# Config
TECH_CONST="$HOME/.bare-ai/technical-constitution.md"
ROLE_CONST="$HOME/.bare-ai/role.md"
FLEET_FILE="$HOME/.bare-ai/fleet.conf"
LOG_FILE="$HOME/.bare-ai/reflex_history.log"
CRED_FILE="$HOME/.bare-ai/config/vault.env"
TARGET_USER="bare-ai"

# --- VAULT AUTHENTICATION ---
fetch_api_key() {
    if [ ! -f "$CRED_FILE" ]; then
        echo "❌ Error: Vault credentials not found at $CRED_FILE" >&2
        return 1
    fi
    source "$CRED_FILE"

    VAULT_TOKEN=$(curl -s -k --request POST \
        --data "{\"role_id\":\"$VAULT_ROLE_ID\",\"secret_id\":\"$VAULT_SECRET_ID\"}" \
        "$VAULT_ADDR/v1/auth/approle/login" | jq -r '.auth.client_token')

    if [[ "$VAULT_TOKEN" == "null" || -z "$VAULT_TOKEN" ]]; then
        echo "❌ Error: Failed to authenticate with Vault." >&2
        return 1
    fi

    API_KEY=$(curl -s -k \
        --header "X-Vault-Token: $VAULT_TOKEN" \
        "$VAULT_ADDR/v1/secret/data/bare-ai/brain" | jq -r '.data.data.key')

    if [[ "$API_KEY" == "null" || -z "$API_KEY" ]]; then
        echo "❌ Error: Authenticated but failed to read secret." >&2
        return 1
    fi

    echo "$API_KEY"
}

# --- CIRCUIT BREAKER ---
should_block_reflex() {
    local target=$1
    local time_pattern=$(date +%Y-%m-%d\ %H:)
    local recent=$(grep "$target" "$LOG_FILE" 2>/dev/null | grep "REFLEX" | grep "$time_pattern" | tail -n 1)
    if [[ -n "$recent" ]]; then echo "YES"; else echo "NO"; fi
}

# --- BUILD COMBINED CONSTITUTION ---
build_constitution() {
    local combined
    combined=$(cat "$TECH_CONST" 2>/dev/null || echo "")
    if [ -f "$ROLE_CONST" ]; then
        combined="${combined}"$'\n\n---\n\n'"$(cat "$ROLE_CONST")"
    fi
    echo "$combined"
}

# --- MAIN EXECUTION ---
echo "🧠 Brain v5.1 (Vault-Aware): Scanning Fleet..."

GEMINI_API_KEY=$(fetch_api_key) || { echo "💀 Fatal: Cannot retrieve Neural Engine Key."; exit 1; }
export GEMINI_API_KEY

CONSTITUTION=$(build_constitution)

while IFS= read -r WORKER_HOST || [[ -n "$WORKER_HOST" ]]; do
    [[ "$WORKER_HOST" =~ ^#.*$ ]] && continue
    [[ -z "$WORKER_HOST" ]] && continue

    echo -e "\n📡 Targeting: $WORKER_HOST"

    # 1. Harvest
    RAW_DATA=$(ssh -q -o ConnectTimeout=5 $TARGET_USER@$WORKER_HOST "bare-summarize" || echo "")
    if [ -z "$RAW_DATA" ]; then echo "❌ Unreachable."; continue; fi

    # 2. Circuit Breaker
    if [[ $(should_block_reflex "$WORKER_HOST") == "YES" ]]; then
        echo "⚠️  Circuit Breaker: Skipping."
        continue
    fi

    # 3. Analyze
    PROMPT="$CONSTITUTION
    URGENT TASK: Analyze this telemetry.
    DATA: $RAW_DATA"

    RESPONSE=$(gemini -m gemini-2.5-flash-lite "$PROMPT" 2>/dev/null || echo "")

    # Fallback (Spinal Cord)
    if [ -z "$RESPONSE" ]; then
        echo "⚠️  Offline Mode: Engaging Spinal Cord."
        if echo "$RAW_DATA" | grep -q '"rke2_status": "inactive"'; then
            RESPONSE=$'REASON: Spinal Cord detected inactive service.\nCOMMAND: sudo systemctl start rke2-server'
        else
            RESPONSE=$'REASON: Spinal Cord nominal.\nCOMMAND: NONE'
        fi
    fi

    # 4. Reflex
    REASON=$(echo "$RESPONSE" | grep "REASON:" | cut -d':' -f2- | xargs)
    FIX_CMD=$(echo "$RESPONSE" | grep "COMMAND:" | cut -d':' -f2- | xargs)

    if [[ "$FIX_CMD" != "NONE" && ! -z "$FIX_CMD" ]]; then
        echo "⚡ REFLEX TRIGGERED: $FIX_CMD"
        ssh -t -q $TARGET_USER@$WORKER_HOST "$FIX_CMD"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] REFLEX | $WORKER_HOST | $REASON | $FIX_CMD" >> "$LOG_FILE"
    else
        echo "🟢 Healthy ($REASON)"
    fi
done < "$FLEET_FILE"
INNER_EOF

chmod +x "$BRAIN_SCRIPT"
echo -e "${GREEN}✓ bare-brain installed${NC}"

# --- 5. FLEET CONFIG ---
FLEET_SRC="$REPO_DIR/fleet.conf"
if [ ! -f "$FLEET_FILE" ]; then
    if [ -f "$FLEET_SRC" ]; then
        cp "$FLEET_SRC" "$FLEET_FILE"
        echo -e "${GREEN}✓ Fleet config deployed from repo${NC}"
    else
        echo "# Add worker IPs here, one per line" > "$FLEET_FILE"
        echo -e "${YELLOW}⚠️  Created empty fleet.conf — add worker IPs to ~/.bare-ai/fleet.conf${NC}"
    fi
else
    echo -e "${GREEN}✓ Fleet config already exists — not overwritten${NC}"
fi

# --- 6. BASHRC ---
echo -e "${YELLOW}Updating $BASHRC_FILE...${NC}"

if ! grep -q "BARE-AI PATH" "$BASHRC_FILE"; then
    cat << 'PATH_EOF' >> "$BASHRC_FILE"

# BARE-AI PATH
if [ -d "$HOME/.bare-ai/bin" ] ; then
    PATH="$HOME/.bare-ai/bin:$PATH"
fi
PATH_EOF
    echo -e "${GREEN}✓ PATH entry added${NC}"
else
    echo -e "${YELLOW}⚠️  PATH entry already present${NC}"
fi

if ! grep -q "BARE-AI Brain Loader" "$BASHRC_FILE"; then
    cat << 'BRAIN_FUNC_EOF' >> "$BASHRC_FILE"

# BARE-AI Brain Loader
bare-brain-run() {
    local TECH_CONST="$HOME/.bare-ai/technical-constitution.md"
    local ROLE_CONST="$HOME/.bare-ai/role.md"

    if [ ! -f "$TECH_CONST" ]; then
        echo -e "\033[0;31mError: Technical constitution not found. Re-run setup_bare-brain.sh.\033[0m"
        return 1
    fi

    export BARE_AI_CONSTITUTION="$TECH_CONST"
    export BARE_AI_ROLE_CONSTITUTION="$ROLE_CONST"

    bare-brain
}
BRAIN_FUNC_EOF
    echo -e "${GREEN}✓ Brain loader added to .bashrc${NC}"
else
    echo -e "${YELLOW}⚠️  Brain loader already in .bashrc${NC}"
fi

echo -e "\n${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ BARE-AI BRAIN SETUP COMPLETE${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "1. ${YELLOW}Reload:${NC}         source ~/.bashrc"
echo -e "2. ${YELLOW}Edit role:${NC}      nano ~/.bare-ai/role.md"
echo -e "3. ${YELLOW}Edit fleet:${NC}     nano ~/.bare-ai/fleet.conf"
echo -e "4. ${YELLOW}Run brain:${NC}      bare-brain-run"
echo -e "5. ${YELLOW}Tech const:${NC}     cat ~/.bare-ai/technical-constitution.md (read-only)"