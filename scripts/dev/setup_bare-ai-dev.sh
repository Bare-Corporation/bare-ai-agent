#!/usr/bin/env bash
#############################################################
#    ____ _                  _ _       _        ____        #
#   / ___| | ___  _    _  ___| (_)_ __ | |_      / ___|___  #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#  Hybrid Bare-AI-Developer Installer                       #
#  by the Cloud Integration Corporation                     #
#############################################################
# ==============================================================================
# SCRIPT NAME:    setup_bare-ai-dev.sh
# DESCRIPTION:    Bare-AI Developer Console ("The Architect")
# VERSION:        5.1.1-Dev (Hybrid Engine Detection)
#
# PURPOSE:
#   Transforms a developer machine (e.g., Penguin etc) into the control center.
#   1. Safety: Disables autonomous loops.
#   2. Deployment: Installs 'bare-enroll' (Pointing to worker installer).
#   3. Audit: Installs 'bare-audit'.
#   4. Logging: Forwards chat logs to the daily diary with hybrid engine support.
#   5. Hashi Corp Vault pre-flight check to all three install scripts
# ==============================================================================
set -euo pipefail

# --- CONFIGURATION ---
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

BARE_AI_DIR="$HOME/.bare-ai"
BIN_DIR="$BARE_AI_DIR/bin"

# Derive repo root dynamically from script location (works regardless of clone name/path)
# Script lives at <repo_root>/scripts/dev/setup_bare-ai-dev.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo -e "${GREEN}Repo root detected: $REPO_DIR${NC}"

# Store repo path so bare-enroll can find it at runtime
mkdir -p "$HOME/.bare-ai"
echo "BARE_AI_REPO=$REPO_DIR" > "$HOME/.bare-ai/agent.env"
TEMPLATES_DIR="$REPO_DIR/scripts/templates"

echo -e "${GREEN}Initializing BARE-AI ARCHITECT CONSOLE (v5.1.0)...${NC}"

# 1. Directory Setup
mkdir -p "$BIN_DIR" "$BARE_AI_DIR/diary"


# --- VAULT PRE-FLIGHT CHECK ---
echo -e "${YELLOW}Checking Vault configuration...${NC}"
VAULT_ENV_FILE="$HOME/.bare-ai/config/vault.env"
mkdir -p "$(dirname "$VAULT_ENV_FILE")"

# Create vault.env stub if it doesn't exist
if [ ! -f "$VAULT_ENV_FILE" ]; then
    cat << 'VAULT_STUB_EOF' > "$VAULT_ENV_FILE"
# Bare-AI Vault Credentials
# Fill in your Vault details and re-run the installer
VAULT_ADDR=https://your-vault-address:8200
VAULT_ROLE_ID=your-role-id-here
VAULT_SECRET_ID=your-secret-id-here
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

# 2. Install 'bare-audit' (from worker artifact)
WORKER_ARTIFACT="$REPO_DIR/scripts/worker/bare-summarize"

if [ -f "$WORKER_ARTIFACT" ]; then
    echo -e "${YELLOW}Installing bare-audit...${NC}"
    cp "$WORKER_ARTIFACT" "$BIN_DIR/bare-audit"
    chmod +x "$BIN_DIR/bare-audit"
    echo -e "${GREEN}✓ bare-audit installed${NC}"
else
    echo -e "${YELLOW}⚠️  Warning: Worker artifact not found at $WORKER_ARTIFACT${NC}"
    echo "   Expected: <repo_root>/scripts/worker/bare-summarize"
fi

# 3. Create 'bare-enroll' (The Deployment Tool)
cat << 'EnrollEOF' > "$BIN_DIR/bare-enroll"
#!/bin/bash
# Usage: bare-enroll user@192.168.1.50
TARGET=$1
if [ -z "$TARGET" ]; then
    echo "Usage: bare-enroll <user@host>"
    echo "Deploys the v5.1 Hybrid Worker logic to a remote node."
    exit 1
fi

echo "🚀 Enrolling Node: $TARGET"

# Load repo path written at install time by setup_bare-ai-dev.sh
if [ -f "$HOME/.bare-ai/agent.env" ]; then
    source "$HOME/.bare-ai/agent.env"
    REPO_PATH="$BARE_AI_REPO"
else
    echo "❌ Error: agent.env not found at ~/.bare-ai/agent.env"
    echo "   Re-run setup_bare-ai-dev.sh to regenerate it."
    exit 1
fi

# Correct paths: both files live under scripts/worker/
WORKER_SCRIPT="$REPO_PATH/scripts/worker/setup_bare-ai-worker.sh"
ARTIFACT="$REPO_PATH/scripts/worker/bare-summarize"

# Validation
if [ ! -f "$WORKER_SCRIPT" ]; then
    echo "❌ Error: Worker installer not found at $WORKER_SCRIPT"
    exit 1
fi
if [ ! -f "$ARTIFACT" ]; then
    echo "❌ Error: Artifact not found at $ARTIFACT"
    exit 1
fi

# Step 1: Create Staging
echo "   -> Preparing staging area..."
ssh "$TARGET" "mkdir -p /tmp/bare-install"

# Step 2: Upload Payload
echo "📦 -> Uploading Payload (Hybrid Engine Ready)..."
scp "$WORKER_SCRIPT" "$TARGET:/tmp/bare-install/setup"
scp "$ARTIFACT" "$TARGET:/tmp/bare-install/bare-summarize"

# Step 3: Execute
echo "⚡ -> Executing Remote Installer (User will select engine)..."
ssh -t "$TARGET" "bash /tmp/bare-install/setup"

echo "✅ Enrollment Complete."
EnrollEOF
chmod +x "$BIN_DIR/bare-enroll"
echo -e "${GREEN}✓ bare-enroll installed${NC}"

# 4. Deploy Constitutions
echo -e "${YELLOW}Deploying technical constitution...${NC}"
TECH_CONST_SRC="$TEMPLATES_DIR/technical-constitution.md"
TECH_CONST_DEST="$BARE_AI_DIR/technical-constitution.md"

if [ -f "$TECH_CONST_SRC" ]; then
    chmod 644 "$TECH_CONST_DEST" 2>/dev/null || true
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
        echo "# BARE-AI ROLE CONSTITUTION
# Edit this file to define this agent's role and personality." > "$ROLE_CONST"
    fi
else
    echo -e "${GREEN}✓ Role constitution already exists — not overwritten${NC}"
fi

# 5. .bashrc Updates (Log Forwarding & Hybrid Engine Detection)
cat << 'BashrcEOF' > "$BARE_AI_DIR/dev_aliases"
# BARE-AI DEV TOOLS
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"
if [ -d "$HOME/.bare-ai/bin" ] ; then PATH="$HOME/.bare-ai/bin:$PATH"; fi

# --- HYBRID ENGINE DETECTION ---
# Detects which CLI engine is available and routes accordingly.
# Priority: bare-ai-cli > gemini-cli
# Override with: export BARE_ENGINE=gemini  OR  export BARE_ENGINE=bare
_bare_detect_engine() {
    if [ "${BARE_ENGINE:-}" = "gemini" ]; then
        echo "gemini"
    elif [ "${BARE_ENGINE:-}" = "bare" ]; then
        echo "bare"
    elif command -v bare-ai &>/dev/null; then
        echo "bare"
    elif command -v gemini &>/dev/null; then
        echo "gemini"
    else
        echo "none"
    fi
}

# The Local Assistant (Hybrid Engine + Log Forwarding)
bare() {
    local TODAY=$(date +%Y-%m-%d)
    local TECH_CONST="$HOME/.bare-ai/technical-constitution.md"
    local ROLE_CONST="$HOME/.bare-ai/role.md"
    local DIARY="$HOME/.bare-ai/diary/$TODAY.md"
    # Load Vault credentials if not already set
    if [ -f "$HOME/.bare-ai/config/vault.env" ]; then
        source "$HOME/.bare-ai/config/vault.env" 2>/dev/null || true
    fi

    local ENGINE
    ENGINE=$(_bare_detect_engine)

    mkdir -p "$(dirname "$DIARY")"
    touch "$DIARY"

    case "$ENGINE" in
        bare)
            echo -e "${GREEN}🤖 [Engine: Bare-AI CLI]${NC}"
            export BARE_AI_CONSTITUTION="$TECH_CONST"
            export BARE_AI_ROLE_CONSTITUTION="$ROLE_CONST"
            bare-ai
            if [ -f "BARE.md" ]; then
                echo -e "\n--- SESSION APPENDED: $(date) [bare-ai] ---" >> "$DIARY"
                cat "BARE.md" >> "$DIARY"
                rm "BARE.md"
                echo -e "${GREEN}📝 Session saved to Diary ($TODAY.md).${NC}"
            fi
            ;;
        gemini)
            echo -e "${YELLOW}✨ [Engine: Gemini CLI]${NC}"
            export BARE_AI_CONSTITUTION="$TECH_CONST"
            export BARE_AI_ROLE_CONSTITUTION="$ROLE_CONST"
            local combined_const
            combined_const=$(cat "$TECH_CONST")
            if [ -f "$ROLE_CONST" ]; then
                combined_const="${combined_const}"$'\n\n---\n\n'"$(cat "$ROLE_CONST")"
            fi
            gemini -m gemini-2.5-flash-lite -i "$combined_const"
            if [ -f "GEMINI.md" ]; then
                echo -e "\n--- SESSION APPENDED: $(date) [gemini] ---" >> "$DIARY"
                cat "GEMINI.md" >> "$DIARY"
                rm "GEMINI.md"
                echo -e "${GREEN}📝 Session saved to Diary ($TODAY.md).${NC}"
            fi
            ;;
        none)
            echo -e "${RED}❌ Error: No AI CLI engine found.${NC}"
            echo "   Install bare-ai-cli  OR  gemini-cli, or set BARE_ENGINE."
            return 1
            ;;
    esac
}

# Explicit engine overrides (useful for testing both engines side-by-side)
alias bare-gemini='BARE_ENGINE=gemini bare'
alias bare-sovereign='BARE_ENGINE=bare bare'

alias bare-status='echo "🔍 Local Telemetry Audit:"; bare-audit | jq .'
alias bare-cd='source ~/.bare-ai/agent.env 2>/dev/null && cd "$BARE_AI_REPO" || echo "agent.env not found"'
alias bare-engine='_bare_detect_engine && echo "Current engine: $(_bare_detect_engine) (override with: export BARE_ENGINE=bare|gemini)"'
BashrcEOF

# Idempotent append to .bashrc
if ! grep -q "BARE-AI DEV TOOLS" "$HOME/.bashrc"; then
    echo -e "${YELLOW}Adding tools to .bashrc...${NC}"
    cat "$BARE_AI_DIR/dev_aliases" >> "$HOME/.bashrc"
    echo -e "${GREEN}✓ .bashrc updated${NC}"
else
    echo -e "${YELLOW}⚠️  BARE-AI DEV TOOLS already in .bashrc, skipping${NC}"
fi
rm "$BARE_AI_DIR/dev_aliases"

echo -e "\n${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ ARCHITECT SETUP COMPLETE${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "1. ${YELLOW}Reload:${NC}    source ~/.bashrc"
echo -e "2. ${YELLOW}Verify:${NC}    bare-status"
echo -e "3. ${YELLOW}Engine:${NC}    bare-engine"
echo -e "4. ${YELLOW}Use:${NC}       bare (auto-detects engine)"
echo -e "5. ${YELLOW}Override:${NC}  bare-gemini  or  bare-sovereign"
echo -e "   ${YELLOW}Or set:${NC}    export BARE_ENGINE=bare|gemini"