#!/usr/bin/env bash

############################################################
#    ____ _                  _ _       _        ____       #
#   / ___| | ___  _    _  ___| (_)_ __ | |_      / ___|___  #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
#   by the Cloud Integration Corporation                    #
############################################################
# ==============================================================================
# SCRIPT NAME:    setup_bare-ai-dev.sh
# DESCRIPTION:    Bare-AI Developer Console ("The Architect")
# VERSION:        5.1.0-Dev (Hybrid Engine Detection)
#
# PURPOSE:
#   Transforms a developer machine (e.g., Penguin) into the control center.
#   1. Safety: Disables autonomous loops.
#   2. Deployment: Installs 'bare-enroll' (Pointing to worker installer).
#   3. Audit: Installs 'bare-audit'.
#   4. Logging: Forwards chat logs to the daily diary with hybrid engine support.
# ==============================================================================
set -euo pipefail

# --- CONFIGURATION ---
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

BARE_AI_DIR="$HOME/.bare-ai"
BIN_DIR="$BARE_AI_DIR/bin"
REPO_DIR="$HOME/Bare-ai"

echo -e "${GREEN}Initializing BARE-AI ARCHITECT CONSOLE (v5.1.0)...${NC}"

# 1. Directory Setup
mkdir -p "$BIN_DIR" "$BARE_AI_DIR/diary"

# 2. Install 'bare-audit' (from worker artifact)
WORKER_ARTIFACT="$REPO_DIR/scripts/worker/bare-summarize"

if [ -f "$WORKER_ARTIFACT" ]; then
    echo -e "${YELLOW}Installing bare-audit...${NC}"
    cp "$WORKER_ARTIFACT" "$BIN_DIR/bare-audit"
    chmod +x "$BIN_DIR/bare-audit"
    echo -e "${GREEN}✓ bare-audit installed${NC}"
else
    echo -e "${YELLOW}⚠️  Warning: Worker artifact not found at $WORKER_ARTIFACT${NC}"
    echo "   Expected: ~/Bare-ai/scripts/worker/bare-summarize"
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
REPO_PATH="$HOME/Bare-ai"

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

# 4. Enforce Architect Constitution
CONSTITUTION="# MISSION
You are the **Bare-AI Architect Assistant**.
You run on the Developer Console (Penguin).
Your goal is to help write code, manage Git, and debug the fleet.

# RULES
1. **Context:** You are on a Dev Machine, NOT a server.
2. **Safety:** Do not restart system services (systemd) on this machine.
3. **Capabilities:** You can use 'git', 'ssh', and 'bare-enroll'.
4. **Style:** Be concise, technical, and accurate.

# DIARY
Logs are stored in ~/.bare-ai/diary/."

echo -e "${YELLOW}Updating Identity to Architect Mode...${NC}"
echo "$CONSTITUTION" > "$BARE_AI_DIR/constitution.md"
echo -e "${GREEN}✓ Constitution updated${NC}"

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
    local CONST="$HOME/.bare-ai/constitution.md"
    local DIARY="$HOME/.bare-ai/diary/$TODAY.md"
    local ENGINE
    ENGINE=$(_bare_detect_engine)

    mkdir -p "$(dirname "$DIARY")"
    touch "$DIARY"

    case "$ENGINE" in
        bare)
            echo -e "${GREEN}🤖 [Engine: Bare-AI CLI]${NC}"
            bare-ai -i "$(cat "$CONST")"
            if [ -f "BARE.md" ]; then
                echo -e "\n--- SESSION APPENDED: $(date) [bare-ai] ---" >> "$DIARY"
                cat "BARE.md" >> "$DIARY"
                rm "BARE.md"
                echo -e "${GREEN}📝 Session saved to Diary ($TODAY.md).${NC}"
            fi
            ;;
        gemini)
            echo -e "${YELLOW}✨ [Engine: Gemini CLI]${NC}"
            gemini -m gemini-2.5-flash-lite -i "$(cat "$CONST")"
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
alias bare-cd='cd ~/Bare-ai'
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