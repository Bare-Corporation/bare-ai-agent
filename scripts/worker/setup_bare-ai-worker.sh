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
# ==============================================================================
# SCRIPT NAME:    setup_bare-ai-worker.sh
# DESCRIPTION:    bare-ai-worker Installer (Level 4 Autonomy)
# AUTHOR:         Bare-AI
# DATE:           2026-06-21
# VERSION:        5.6.1 (Debian, Proxmox, Mint, Debian 12 on AWS/Root)
# CHANGELOG:      Added persistent bare-necessities-workspace separation — the agent
#                 now NEVER writes inside the git-tracked bare-ai-cli repo.
# ==============================================================================

set -euo pipefail

# --- COLORS ---
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

# --- SUDO KEEP-ALIVE ---
echo -e "${YELLOW}Requesting sudo access upfront to prevent installation hangs...${NC}"
sudo -v
# Keep-alive: update existing sudo time stamp if set, until script has finished
while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

# --- ARGUMENT PARSING ---
FAST_UPDATE=false
TIER="free" # Default to free tier

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --fast) FAST_UPDATE=true; echo -e "${YELLOW}FAST MODE: Skipping engine rebuild...${NC}" ;;
        --tier) TIER="$2"; shift ;;
    esac
    shift
done

# --- DOCKER / Podman WARNING ---
if [ ! -f "/.dockerenv" ]; then
    echo -e "${YELLOW}Warning: Running on host system. For enhanced security, Bare-ERP recommends running within Docker or Podman.${NC}"
fi

echo -e "${GREEN}Starting BARE-AI setup...${NC}"


# --- REAL USER DETECTION (SUDO TRAP FIX) ---
TARGET_USER="${SUDO_USER:-$USER}"
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)


# --- DIRECTORY DEFINITIONS ---
WORKSPACE_DIR="$TARGET_HOME/.bare-ai"
BARE_AI_DIR="$WORKSPACE_DIR"
BIN_DIR="$BARE_AI_DIR/bin"
LOG_DIR="$BARE_AI_DIR/logs"
CLI_REPO_DIR="$TARGET_HOME/bare-ai-cli"

# --- AGENT WORKSPACE (separate from the git-tracked CLI repo) ---
# Everything the agent writes — custom scripts, merged role/constitution
# context, session bridges — lives here. NEVER inside bare-ai-cli/, so that
# 'git pull' / 'bare-update' never conflicts with agent-authored files and
# users never need to run 'git stash'. This directory and its contents are
# NEVER deleted by this installer, on fresh install or on update — every
# operation against it below uses 'mkdir -p', which is purely additive.
BARE_AI_WORKSPACE_DIR="$TARGET_HOME/bare-necessities-workspace"
AGENT_SCRIPTS_DIR="$BARE_AI_WORKSPACE_DIR/my-bare-scripts"     # agent-authored, never overwritten by this installer
CLI_SCRIPTS_DIR="$BARE_AI_WORKSPACE_DIR/scripts"               # official toolkit, refreshed on every install/update
ROLE_BRIDGE_DIR="$BARE_AI_WORKSPACE_DIR/bare-functional-role"  # merged role+constitution context, rewritten fresh each session
DIARY_DIR="$BARE_AI_WORKSPACE_DIR/bare-ai-diary"               # Bare-AI keeps a diary of its work locally.

# --- SOURCE DIR DETECTION (Path Paradox Fix) ---
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SOURCE_DIR="$(pwd)"
fi
REPO_DIR="$(cd "$SOURCE_DIR/../.." && pwd)"
TEMPLATES_DIR="$REPO_DIR/scripts/templates"
BARE_NECESSITIES_DIR="$REPO_DIR/scripts/bare-necessities"

# --- HELPER: execute_command ---
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

# --- CORE TOOLING ---
echo -e "${YELLOW}Installing core system tools...${NC}"
execute_command "sudo apt-get update -qq && sudo apt-get install -y -qq jq curl wget" "Install core networking and JSON tools"

# i. Define the directory and the actual file
CONFIG_DIR="$TARGET_HOME/.bare-ai/config"
CONFIG_FILE="$CONFIG_DIR/agent.env"

# ii. Safely create the directory structure first
mkdir -p "$CONFIG_DIR"

ENGINE_TYPE="sovereign"


# iii. Safely touch the file and inject the engine type
touch "$CONFIG_FILE"
sed -i '/export ENGINE_TYPE=/d' "$CONFIG_FILE"
echo "export ENGINE_TYPE=\"$ENGINE_TYPE\"" >> "$CONFIG_FILE"

#####################################################
#####################################################
#####################################################

# --- 1. DIRECTORY SETUP ---
echo -e "${YELLOW}Creating BARE-AI directory structure...${NC}"
execute_command "mkdir -p \"$DIARY_DIR\" \"$LOG_DIR\" \"$BIN_DIR\"" "Create diary, logs, and bin directories"

if [ ! -d "$BARE_AI_DIR" ] || [ ! -d "$DIARY_DIR" ] || [ ! -d "$LOG_DIR" ] || [ ! -d "$BIN_DIR" ]; then
    echo -e "${RED}Error: Failed to create BARE-AI directories. Exiting.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Directory structure created${NC}"

#####################################################
#####################################################
#####################################################

# --- 1a. AGENT WORKSPACE SETUP (Persistent — survives updates & reinstalls) ---
echo -e "${YELLOW}Creating persistent agent workspace (separate from bare-ai-cli)...${NC}"
execute_command "mkdir -p \"$AGENT_SCRIPTS_DIR/bare-bash-scripts\" \"$AGENT_SCRIPTS_DIR/bare-python3-scripts\" \"$CLI_SCRIPTS_DIR\" \"$ROLE_BRIDGE_DIR\"" "Create bare-necessities-workspace directory structure"
echo -e "${GREEN}✓ Agent workspace ready at $BARE_AI_WORKSPACE_DIR (never wiped by this installer)${NC}"

#####################################################
#####################################################
#####################################################

# --- 1b. VAULT PRE-FLIGHT & INSTALLATION ---
echo -e "\n${YELLOW}Checking Vault configuration...${NC}"
VAULT_ENV_FILE="$TARGET_HOME/.bare-ai/config/vault.env"
mkdir -p "$(dirname "$VAULT_ENV_FILE")"

FINAL_VAULT_ADDR="http://127.0.0.1:8200"
INSTALL_VAULT=false
AGENT_ROLE_ID="your-role-id-here"
AGENT_SECRET_ID="your-secret-id-here"
SKIP_VAULT_ADMIN=false

read -rp "Do you have an existing OpenBao Vault server for this agent? [y/N/unsure]: " HAS_VAULT
if [[ "$HAS_VAULT" =~ ^[Yy]$ ]]; then
    read -rp "Enter Vault Address (e.g., https://192.168.1.50:8200): " USER_VAULT_ADDR
    echo -e "Testing connectivity to $USER_VAULT_ADDR..."

    if curl -s -k --max-time 5 "$USER_VAULT_ADDR/v1/sys/health" > /dev/null 2>&1 || curl -s -k --max-time 5 "$USER_VAULT_ADDR" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Vault reachable!${NC}"
        FINAL_VAULT_ADDR="$USER_VAULT_ADDR"

        # --- TIER GATEKEEPER ---
        if [ "$TIER" == "pro" ]; then
            echo -e "\n${GREEN}⭐ Bare-AI Pro Edition Mesh detected.${NC}"
            echo "1) Reuse existing Bare-AI secrets (Join existing Mesh — recommended)"
            echo "2) Reset/Overwrite Bare-AI secrets (Initialize new Mesh)"
            read -rp "Select [1 or 2]: " VAULT_ACTION
        else
            echo -e "\n${RED}⚠️ NOTICE: Free Edition will re-seed model paths (existing Bare-AI secrets will be lost).${NC}"
            echo -e "${YELLOW}To join an existing Mesh without overwriting secrets, you require the Bare-AI Pro Edition.${NC}"
            read -rp "Proceed with overwrite? [y/N]: " OVERWRITE_CONFIRM
            if [[ ! "$OVERWRITE_CONFIRM" =~ ^[Yy]$ ]]; then
                echo -e "\n${RED}❌ Installation Aborted.${NC}"
                echo -e "${YELLOW}To join this node to an existing Mesh, upgrade at: ${GREEN}www.bare-ai.pro${NC}\n"
                exit 1
            fi
            # Force the action to "reset" for Free tier
            VAULT_ACTION="2"
        fi

        # --- EXECUTE CHOSEN ACTION ---
        if [ "${VAULT_ACTION:-1}" == "2" ]; then
            echo -e "${YELLOW}Admin access required to seed Vault model paths.${NC}"
            read -rsp "Enter Admin VAULT_TOKEN: " ADMIN_TOKEN
            echo ""
            if [ -z "${ADMIN_TOKEN:-}" ]; then
                echo -e "${RED}❌ Token cannot be empty for Reset mode. Aborting.${NC}"
                exit 1
            fi
            export VAULT_TOKEN="$ADMIN_TOKEN"
            SKIP_VAULT_ADMIN=false
        else
            echo -e "${GREEN}✓ Reuse selected. Vault address saved. Fill in Role ID and Secret ID manually after install.${NC}"
            SKIP_VAULT_ADMIN=true
            export VAULT_TOKEN="not-needed-for-reuse"
        fi
    else
        echo -e "${RED}❌ Cannot reach $USER_VAULT_ADDR. Falling back to local Vault installation.${NC}"
        INSTALL_VAULT=true
    fi

else
    INSTALL_VAULT=true
fi

# Auto-Install Logic for Local Vault
if [ "$INSTALL_VAULT" = true ]; then
    SKIP_VAULT_ADMIN=false

    echo -e "${YELLOW}Downloading and Installing OpenBao (Open-Source Vault)...${NC}"
    
    # Install unzip if missing
    execute_command "sudo apt-get install -y -qq unzip" "Install unzip"

    # Download and extract OpenBao binary (universal Linux tarball)
    execute_command "wget -q https://github.com/openbao/openbao/releases/download/v2.0.0/bao_2.0.0_Linux_x86_64.tar.gz -O /tmp/bao.tar.gz" "Download OpenBao v2.0.0"
    execute_command "cd /tmp && tar -xzf bao.tar.gz && sudo mv bao /usr/local/bin/bao && sudo chmod +x /usr/local/bin/bao" "Extract and install OpenBao binary"
    
    # Create the symlink so all 'vault' commands in this script (and the user's system) still work flawlessly
    execute_command "sudo ln -sf /usr/local/bin/bao /usr/local/bin/vault" "Alias OpenBao as vault"

    # Create OpenBao configuration and user
    sudo useradd --system --home /etc/bao --no-create-home bao 2>/dev/null || true
    sudo mkdir -p /etc/bao.d /opt/bao/data
    
    sudo tee /etc/bao.d/bao.hcl > /dev/null <<EOF
storage "file" {
  path = "/opt/bao/data"
}
listener "tcp" {
  address     = "127.0.0.1:8200"
  tls_disable = 1
}
api_addr = "http://127.0.0.1:8200"
disable_mlock = true
ui = true
EOF

    sudo chown -R bao:bao /opt/bao/data /etc/bao.d
    sudo setcap cap_ipc_lock=+ep $(readlink -f $(which bao)) 2>/dev/null || true

    # Create the systemd service file
    sudo tee /etc/systemd/system/bao.service > /dev/null <<EOF
[Unit]
Description="OpenBao secret management tool"
Requires=network-online.target
After=network-online.target

[Service]
User=bao
Group=bao
ProtectSystem=full
ProtectHome=read-only
PrivateTmp=yes
PrivateDevices=yes
SecureBits=keep-caps
AmbientCapabilities=CAP_IPC_LOCK
Capabilities=CAP_IPC_LOCK+ep
CapabilityBoundingSet=CAP_SYSLOG CAP_IPC_LOCK
NoNewPrivileges=yes
ExecStart=/usr/local/bin/bao server -config=/etc/bao.d/bao.hcl
ExecReload=/bin/kill --signal HUP \$MAINPID
KillMode=process
KillSignal=SIGINT
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
LimitNOFILE=65536
LimitMEMLOCK=infinity

[Install]
WantedBy=multi-user.target
EOF

    if [ -d /run/systemd/system ]; then
        execute_command "sudo systemctl daemon-reload && sudo systemctl enable bao && sudo systemctl restart bao" "Start OpenBao Service"
        sleep 3
    else
        echo -e "${YELLOW}Warning: systemd not running. OpenBao must be started manually.${NC}"
    fi
    
    export VAULT_ADDR="http://127.0.0.1:8200"
    FINAL_VAULT_ADDR="http://127.0.0.1:8200"

    echo -e "${YELLOW}Initializing Vault & generating keys (5 shares, threshold 3)...${NC}"
    INIT_OUT=$(vault operator init -key-shares=5 -key-threshold=3 -format=json)
    ROOT_TOKEN=$(echo "$INIT_OUT" | jq -r .root_token)
    KEY_FILE="$TARGET_HOME/.bare-ai/config/vault-recovery-keys.txt"

    # Write the security warning and Root Token
    cat << EOF > "$KEY_FILE"
======================================================================
⚠️ CRITICAL SECURITY NOTICE ⚠️
Store these keys in a secure password manager (e.g., Bitwarden) IMMEDIATELY.
Once safely stored, you MUST DELETE THIS FILE. 
Leaving this file on the disk compromises your agent's sovereignty.
======================================================================

Root Token: $ROOT_TOKEN
Unseal Keys:
EOF
    
    # Extract and append all 5 Unseal Keys
    echo "$INIT_OUT" | jq -r '.unseal_keys_b64[]' >> "$KEY_FILE"
    chmod 600 "$KEY_FILE"

    # Automatically unseal using the first 3 keys to meet the threshold
    echo -e "${YELLOW}Applying 3 unseal keys to unlock Vault...${NC}"
    for i in {0..2}; do
        KEY=$(echo "$INIT_OUT" | jq -r ".unseal_keys_b64[$i]")
        vault operator unseal "$KEY" > /dev/null
    done
    
    export VAULT_TOKEN="$ROOT_TOKEN"

fi

# --- 6. UNIVERSAL VAULT CONFIGURATION (only runs when not reusing) ---
if [ "$SKIP_VAULT_ADMIN" = false ]; then
    echo -e "${YELLOW}Configuring KV Engine, AppRole and seeding models...${NC}"

    if ! command -v vault &>/dev/null; then
        echo -e "${RED}❌ vault binary not found. Cannot seed remote Vault.${NC}"
        echo -e "${YELLOW}Install vault locally or choose Reuse to bypass.${NC}"
        exit 1
    fi

    vault secrets enable -version=2 -path=secret kv > /dev/null 2>&1 || true
    vault auth enable approle > /dev/null 2>&1 || true
    vault policy write bare-ai-policy - > /dev/null <<EOF
path "secret/data/*" { capabilities = ["read"] }
EOF
    vault write auth/approle/role/bare-ai-role \
        secret_id_ttl=0 token_num_uses=0 token_ttl=0 token_max_ttl=0 secret_id_num_uses=0 \
        policies="bare-ai-policy" > /dev/null

  # 9. Seed Default Models
    echo -e "${YELLOW}Seeding default model endpoints...${NC}"

    # ── Model routing is now catalog-driven ──────────────
    # The Council API /v1/models is the single source of truth for
    # model routing (cloud + local). The installer no longer pre-seeds
    # every model config. Local Ollama models resolve keyless from the
    # catalog (no Vault entry needed). Cloud models: users add their own
    # provider keys on demand. Only the Council key is pre-seeded here so
    # new users get the "one key to rule them all" onboarding.

    # Bare-AI Council API — purchase key at https://bare-ai.net
    # Seed BOTH council paths with the same key:
    #   - bare-ai-council     : used by bare-ai-council.py (works today)
    #   - bare-ai-council-v1  : catalog model_id for the future /model 777 adapter
    # TODO: once the council adapter ships, reconcile council.py to the
    #       -v1 path and drop the legacy entry.
    vault kv put secret/bare-ai-council/config \
        base_url="https://api.bare-ai.net/v1/council" \
        model_name="bare-ai-council" \
        api_key="enterYourKey" > /dev/null
    vault kv put secret/bare-ai-council-v1/config \
        base_url="https://api.bare-ai.net/v1/council" \
        model_name="bare-ai-council-v1" \
        api_key="enterYourKey" > /dev/null
    
    # 10. Extract IDs for the Agent

    AGENT_ROLE_ID=$(vault read -field=role_id auth/approle/role/bare-ai-role/role-id)
    AGENT_SECRET_ID=$(vault write -f -field=secret_id auth/approle/role/bare-ai-role/secret-id)
    echo -e "${GREEN}✓ Vault configured and seeded successfully$FINAL_VAULT_ADDR${NC}"
fi

# Write dynamic vault.env with CIC ASCII Art
cat << EOF > "$VAULT_ENV_FILE"
#############################################################
#    ____ _                  _ _       _         ____       #
#   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
# Bare-AI OpenBao (Vault) Credentials                       #
#############################################################
#  by the Cloud Integration Corporation                     #
#############################################################
# ==============================================================================
# VAULT AUTHENTICATION & MODEL ROUTING CONFIGURATION
# ==============================================================================
# Fill in your Vault details and re-run the installer
export VAULT_ADDR="$FINAL_VAULT_ADDR"
export VAULT_ROLE_ID="$AGENT_ROLE_ID"
export VAULT_SECRET_ID="$AGENT_SECRET_ID"
EOF

# --- 1c. SOVEREIGN SEARCH SETUP ---
echo -e "\n${YELLOW}Checking Search Engine configuration...${NC}"
read -rp "Do you have an existing Sovereign Search Engine (e.g., SearXNG)? [y/N/1/0]: " HAS_SEARCH
if [[ "$HAS_SEARCH" =~ ^[Yy1]$ ]]; then
    read -rp "Enter Search URL (e.g., http://192.168.86.130:8080): " SEARCH_ADDR
    echo -e "\n# Sovereign Search Override" >> "$CONFIG_FILE"
    echo "export BARE_AI_SEARCH_URL=\"$SEARCH_ADDR\"" >> "$CONFIG_FILE"
    echo -e "${GREEN}✓ Search URL set to $SEARCH_ADDR${NC}"
else
    read -rp "Would you like to auto-install a local SearXNG instance now? [y/N]: " INSTALL_SEARCH

    if [[ "$INSTALL_SEARCH" =~ ^[Yy1]$ ]]; then
        echo -e "${YELLOW}Deploying local SearXNG via Docker...${NC}"

        if ! command -v docker &>/dev/null; then
            echo -e "${YELLOW}Docker not found. Installing Docker engine...${NC}"
            execute_command "sudo apt-get update -qq && sudo apt-get install -y -qq ca-certificates curl gnupg" "Install Docker prerequisites"
            execute_command "sudo install -m 0755 -d /etc/apt/keyrings" "Create keyrings dir"
            OS_ID=$(. /etc/os-release && echo "${ID}")
            OS_CODENAME=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
            if [[ "$OS_ID" == "debian" ]]; then
                DOCKER_REPO="https://download.docker.com/linux/debian"
            else
                DOCKER_REPO="https://download.docker.com/linux/ubuntu"
            fi
            execute_command "curl -fsSL ${DOCKER_REPO}/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg && sudo chmod a+r /etc/apt/keyrings/docker.gpg" "Add Docker GPG key"
            execute_command "echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] ${DOCKER_REPO} \$OS_CODENAME stable\" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null" "Add Docker repo"
            execute_command "sudo apt-get update -qq && sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io" "Install Docker CE"
            sudo usermod -aG docker "$USER" || true
        fi
        
        # Clean up any old container and spin up a fresh SearXNG
        sudo docker rm -f searxng &>/dev/null || true
        # Pass the strict JSON override as an environment variable to guarantee API compatibility
        JSON_FMT='{"server":{"formats":["html","json"]}}'
        execute_command "sudo docker run -d --name searxng -p 8080:8080 -v searxng-data:/etc/searxng -e \"SEARXNG_SETTINGS=\$JSON_FMT\" --restart unless-stopped searxng/searxng" "Start SearXNG Container"
            
        LOCAL_SEARCH_URL="http://127.0.0.1:8080"
        echo -e "\n# Sovereign Search Override" >> "$CONFIG_FILE"
        echo "export BARE_AI_SEARCH_URL=\"$LOCAL_SEARCH_URL\"" >> "$CONFIG_FILE"
        echo -e "${GREEN}✓ Local SearXNG installed and routed to $LOCAL_SEARCH_URL${NC}"
    else
        echo -e "${YELLOW}⚠️ No local search configured. Defaulting to standard search providers.${NC}"
    fi
fi

#####################################################
#####################################################
#####################################################

# --- 1d. SOVEREIGN INFERENCE ENGINE SETUP ---
# bare-ai-cli speaks native OpenAI-compatible endpoints. Offer a choice of
# sovereign inference engine (both MIT-licensed):
#   [1] llama.cpp (llama-server) — CPU-bound performance + strict OpenAI fidelity
#   [2] Ollama                   — ease of use + quick model pulls
#   [3] Skip                     — user supplies their own endpoint
# Port 8081 is used (8080 is already bound by the SearXNG container above).
echo -e "${YELLOW}Checking local inference engine...${NC}"

MODELS_DIR="$TARGET_HOME/.bare-ai/models"
mkdir -p "$MODELS_DIR"

LLAMA_SERVER_BIN=""
OLLAMA_BIN=""
if command -v llama-server &>/dev/null; then LLAMA_SERVER_BIN="$(command -v llama-server)"; fi
if command -v ollama       &>/dev/null; then OLLAMA_BIN="$(command -v ollama)"; fi

INFERENCE_CHOICE=""
INFERENCE_ENDPOINT=""
INFERENCE_ADDR_IP=""

# Advertised host: loopback for single-machine (free), node IP for multi-machine (pro)
if [ "$TIER" = "pro" ]; then
    INFERENCE_ADDR_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi

if [ -n "$LLAMA_SERVER_BIN" ] || [ -n "$OLLAMA_BIN" ]; then
    if [ -n "$LLAMA_SERVER_BIN" ]; then
        INFERENCE_CHOICE="1"
        echo -e "${GREEN}✓ llama-server detected at $LLAMA_SERVER_BIN${NC}"
    else
        INFERENCE_CHOICE="2"
        echo -e "${GREEN}✓ Ollama detected at $OLLAMA_BIN${NC}"
    fi
else
    echo -e "No local inference engine detected. Choose an engine to install:"
    echo -e "  ${GREEN}[1]${NC} llama.cpp (llama-server) — recommended for CPU performance & strict OpenAI API fidelity"
    echo -e "  ${GREEN}[2]${NC} Ollama                     — recommended for ease of use & quick model pulls"
    echo -e "  ${GREEN}[3]${NC} Skip                       — I will provide my own endpoint"
    read -rp "Select [1/2/3]: " INFERENCE_CHOICE
fi

case "$INFERENCE_CHOICE" in
  1)
    # ---------- llama.cpp (llama-server) ----------
    echo -e "${YELLOW}Installing llama.cpp (llama-server)...${NC}"

    if [ -z "$LLAMA_SERVER_BIN" ]; then
        LLAMA_ARCH=""
        case "$(uname -m)" in
            x86_64|amd64) LLAMA_ARCH="x64" ;;
            aarch64|arm64) LLAMA_ARCH="arm64" ;;
        esac
        if [ -z "$LLAMA_ARCH" ]; then
            echo -e "${RED}❌ Unsupported architecture: $(uname -m). Choose Ollama or Skip instead.${NC}"
        else
            LLAMA_REPO="ggml-org/llama.cpp"
            LLAMA_TAG="$(curl -fsSL --max-time 20 "https://api.github.com/repos/${LLAMA_REPO}/releases/latest" 2>/dev/null | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)"
            if [ -z "$LLAMA_TAG" ]; then
                echo -e "${RED}❌ Could not resolve latest llama.cpp release (GitHub API rate limit?). Skipping engine install.${NC}"
            else
                LLAMA_ASSET="llama-${LLAMA_TAG}-bin-ubuntu-${LLAMA_ARCH}.tar.gz"
                LLAMA_URL="https://github.com/${LLAMA_REPO}/releases/download/${LLAMA_TAG}/${LLAMA_ASSET}"
                LLAMA_TMP="$(mktemp -d)"
                if curl -fsSL --max-time 600 -o "$LLAMA_TMP/llama.tar.gz" "$LLAMA_URL"; then
                    tar -xzf "$LLAMA_TMP/llama.tar.gz" -C "$LLAMA_TMP" 2>/dev/null || true
                    LLAMA_SERVER_SRC="$(find "$LLAMA_TMP" -type f -name 'llama-server' 2>/dev/null | head -1)"
                    if [ -n "$LLAMA_SERVER_SRC" ]; then
                        install -m 0755 "$LLAMA_SERVER_SRC" "$BIN_DIR/llama-server"
                        LLAMA_SERVER_BIN="$BIN_DIR/llama-server"
                        echo -e "${GREEN}✓ llama-server installed to $LLAMA_SERVER_BIN${NC}"
                    else
                        echo -e "${RED}❌ llama-server binary not found in archive (no $LLAMA_ARCH prebuilt for $LLAMA_TAG?).${NC}"
                    fi
                else
                    echo -e "${RED}❌ Download failed for $LLAMA_ASSET (no $LLAMA_ARCH prebuilt for $LLAMA_TAG?).${NC}"
                fi
                rm -rf "$LLAMA_TMP"
            fi
        fi
    fi

    if [ -n "$LLAMA_SERVER_BIN" ]; then
        # Download a tiny default GGUF model so llama-server does not crash-loop
        DEFAULT_MODEL="$MODELS_DIR/default.gguf"
        if [ ! -s "$DEFAULT_MODEL" ]; then
            echo -e "${YELLOW}Downloading default model (Qwen1.5-0.5B-Chat GGUF, ~400MB)...${NC}"
            if curl -fsSL --max-time 1800 
                "https://huggingface.co/Qwen/Qwen1.5-0.5B-Chat-GGUF/resolve/main/qwen1_5-0_5b-chat-q4_k_m.gguf" 
                -o "$DEFAULT_MODEL"; then
                echo -e "${GREEN}✓ Default model downloaded to $DEFAULT_MODEL${NC}"
            else
                echo -e "${RED}⚠️ Default model download failed. llama-server will need a model loaded manually.${NC}"
            fi
        fi

        # ---------- rootless user-level systemd unit ----------
        LLAMA_UNIT_DIR="$TARGET_HOME/.config/systemd/user"
        mkdir -p "$LLAMA_UNIT_DIR"
        if [ "$TIER" = "pro" ]; then
            LLAMA_BIND_HOST="0.0.0.0"
        else
            LLAMA_BIND_HOST="127.0.0.1"
        fi
        cat > "$LLAMA_UNIT_DIR/llama-server.service" <<EOF
[Unit]
Description=Bare-AI llama.cpp Inference Server (llama-server)
After=network.target

[Service]
ExecStart=$LLAMA_SERVER_BIN --host $LLAMA_BIND_HOST --port 8081 -m $DEFAULT_MODEL
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
        # Enable lingering so the user session (and this service) survives reboots
        if command -v loginctl >/dev/null 2>&1; then
            execute_command "sudo loginctl enable-linger "$TARGET_USER"" "Enable user lingering for $TARGET_USER"
        fi
        # Reload + enable + start the user unit in the target user's session
        USER_UID="$(id -u "$TARGET_USER" 2>/dev/null || echo 1000)"
        execute_command "sudo -u "$TARGET_USER" XDG_RUNTIME_DIR="/run/user/$USER_UID" systemctl --user daemon-reload && sudo -u "$TARGET_USER" XDG_RUNTIME_DIR="/run/user/$USER_UID" systemctl --user enable --now llama-server" "Enable and start llama-server user service"

        if [ "$TIER" = "pro" ]; then
            read -rp "Inference node IP for other nodes to reach [${INFERENCE_ADDR_IP}]: " PROMPTED_IP
            [ -n "$PROMPTED_IP" ] && INFERENCE_ADDR_IP="$PROMPTED_IP"
            INFERENCE_ENDPOINT="http://${INFERENCE_ADDR_IP}:8081/v1/chat/completions"
        else
            INFERENCE_ENDPOINT="http://127.0.0.1:8081/v1/chat/completions"
        fi
        echo -e "${GREEN}✓ llama-server configured. Endpoint: $INFERENCE_ENDPOINT${NC}"
    fi
    ;;

  2)
    # ---------- Ollama ----------
    echo -e "${YELLOW}Installing Ollama...${NC}"
    if [ -z "$OLLAMA_BIN" ]; then
        execute_command "curl -fsSL https://ollama.com/install.sh | sh" "Install Ollama via official script"
        if command -v ollama &>/dev/null; then OLLAMA_BIN="$(command -v ollama)"; fi
    fi
    if [ -n "$OLLAMA_BIN" ]; then
        # Pro: bind Ollama to all interfaces so distributed nodes can reach it
        if [ "$TIER" = "pro" ]; then
            sudo mkdir -p /etc/systemd/system/ollama.service.d
            sudo tee /etc/systemd/system/ollama.service.d/override.conf > /dev/null <<EOF
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
EOF
            execute_command "sudo systemctl daemon-reload && sudo systemctl restart ollama" "Bind Ollama to 0.0.0.0 and restart"
        fi
        if [ "$TIER" = "pro" ]; then
            read -rp "Ollama node IP for other nodes to reach [${INFERENCE_ADDR_IP}]: " PROMPTED_IP
            [ -n "$PROMPTED_IP" ] && INFERENCE_ADDR_IP="$PROMPTED_IP"
            INFERENCE_ENDPOINT="http://${INFERENCE_ADDR_IP}:11434/v1/chat/completions"
        else
            INFERENCE_ENDPOINT="http://127.0.0.1:11434/v1/chat/completions"
        fi
        echo -e "${GREEN}✓ Ollama configured. Endpoint: $INFERENCE_ENDPOINT${NC}"
    else
        echo -e "${RED}❌ Ollama installation did not produce an 'ollama' binary. Choose Skip and provide your own endpoint.${NC}"
    fi
    ;;

  3|*)
    # ---------- Skip / custom endpoint ----------
    read -rp "Enter your OpenAI-compatible completions endpoint (e.g., http://192.168.1.50:8081/v1/chat/completions), or leave blank to skip: " CUSTOM_ENDPOINT
    if [ -n "$CUSTOM_ENDPOINT" ]; then
        INFERENCE_ENDPOINT="$CUSTOM_ENDPOINT"
        echo -e "${GREEN}✓ Custom inference endpoint set to $INFERENCE_ENDPOINT${NC}"
    else
        echo -e "${YELLOW}⚠️ No inference endpoint configured. Set BARE_AI_ENDPOINT manually to enable local routing.${NC}"
    fi
    ;;
esac

# Persist BARE_AI_ENDPOINT idempotently (sourced by the bare() launcher)
if [ -n "$INFERENCE_ENDPOINT" ]; then
    sed -i '/export BARE_AI_ENDPOINT=/d' "$CONFIG_FILE"
    echo "# Sovereign Inference Override" >> "$CONFIG_FILE"
    echo "export BARE_AI_ENDPOINT="$INFERENCE_ENDPOINT"" >> "$CONFIG_FILE"
fi


# --- 2. ENGINE INSTALLATION ---
if [ "$FAST_UPDATE" = false ]; then
        echo -e "${GREEN}Configuring Sovereign Bare-AI Engine...${NC}"

        # Ensure npm is available and up to date before attempting build
        if ! command -v npm &>/dev/null; then
            echo -e "${YELLOW}npm not found. Installing Node.js and npm...${NC}"
            execute_command "sudo apt-get update -qq && sudo apt-get install -y -qq nodejs npm" "Install Node.js and npm"
        fi

        # Node.js version check — bare-ai-cli requires Node 24+
        NODE_MAJOR=$(/usr/bin/node -e "console.log(process.versions.node.split('.')[0])" 2>/dev/null || node -e "console.log(process.versions.node.split('.')[0])" 2>/dev/null || echo "0")
        if [ "${NODE_MAJOR:-0}" -lt 24 ]; then
            echo -e "${RED}❌ Node.js v24+ is required. Current version: $(/usr/bin/node -v 2>/dev/null || node -v 2>/dev/null || echo 'not found')${NC}"
            echo -e "${YELLOW}Installing Node.js 24 via NodeSource...${NC}"
            execute_command "curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -" "Add NodeSource v24 repo"

            execute_command "sudo apt-get install -y nodejs" "Install Node.js 24"
            # Force PATH refresh — hash -r doesn't work in subshells
            export PATH="/usr/bin:$PATH"
            hash -r 2>/dev/null || true
            NODE_VERSION=$(node -v 2>/dev/null || echo 'unknown')
            echo -e "${GREEN}✓ Node.js $NODE_VERSION installed${NC}"
            # Re-check version after install
            NODE_MAJOR=$(/usr/bin/node -e "console.log(process.versions.node.split('.')[0])" 2>/dev/null || echo "0")
            if [ "${NODE_MAJOR:-0}" -lt 24 ]; then
                echo -e "${RED}❌ Node 24 install failed. Please install manually: https://nodejs.org${NC}"
                exit 1
            fi
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
            execute_command "git clone https://github.com/Bare-Corporation/bare-ai-cli.git \"$CLI_REPO_DIR\"" "Clone Bare-AI-CLI"
        else
            echo -e "${GREEN}Existing CLI found. Pulling latest...${NC}"
            # Safety net: if the agent (or a past bug) ever wrote files directly
            # into the git-tracked CLI repo, auto-stash them so 'git pull' never
            # blocks an update and the user never has to run 'git stash' by hand.
            # Going forward the agent only ever writes inside bare-necessities-workspace/.
            execute_command "cd \"$CLI_REPO_DIR\" && if [ -n \"\$(git status --porcelain)\" ]; then git stash --include-untracked; fi" "Auto-stash any stray changes in CLI repo before update"
            execute_command "cd \"$CLI_REPO_DIR\" && git pull origin main" "Update Bare-AI-CLI"
        fi

        execute_command "cd \"$CLI_REPO_DIR\" && /usr/bin/npm install --ignore-scripts && NODE_OPTIONS=\"--max-old-space-size=8192\" /usr/bin/npm run build && /usr/bin/npm run bundle" "Build Sovereign Engine"
        # --- Clone-based workflow: provision the agent work clone (non-destructive) ---
        # The agent runs/commits/pushes ONLY from ~/bare-ai-cli-work, keeping the
        # runtime clone ~/bare-ai-cli clean (pull-only) so agent files can never
        # reach the public repo working tree again.
        WORK_CLONE_DIR="$TARGET_HOME/bare-ai-cli-work"
        if [ ! -d "$WORK_CLONE_DIR" ]; then
            git clone "https://github.com/Bare-Corporation/bare-ai-cli.git" "$WORK_CLONE_DIR"
        fi
        ln -sfn "$CLI_REPO_DIR/node_modules" "$WORK_CLONE_DIR/node_modules"
        cp -a "$CLI_REPO_DIR/bundle/." "$WORK_CLONE_DIR/bundle/"
        # Wire the pre-commit guard (husky) so the BARE_AI.md leak guard is active here too
        (cd "$WORK_CLONE_DIR" && node_modules/.bin/husky 2>/dev/null || true)
        ENGINE_TYPE="sovereign"

else
    echo -e "${GREEN}✓ Skipping engine build (Fast Update active)${NC}"
fi

#####################################################
#####################################################
#####################################################

# --- 3. BARE-NECESSITIES TOOLKIT DEPLOYMENT ---
# CLI_SCRIPTS_DIR (official, installer-managed toolkit) lives inside
# bare-necessities-workspace/ — see "AGENT WORKSPACE" definitions near the top.
echo -e "${YELLOW}Deploying bare-necessities toolset to persistent agent workspace...${NC}"

if [ -d "$BARE_NECESSITIES_DIR" ]; then
    # 2. Sync toolkit to the jail
    echo -e "${YELLOW}Syncing toolkit to $CLI_SCRIPTS_DIR...${NC}"
    execute_command "cp -r \"$BARE_NECESSITIES_DIR/\"* \"$CLI_SCRIPTS_DIR/\"" "Copy tools into jail"
    # Deploy the model-catalog helper to a stable runtime path so the
    # bare() shell function can source it (catalog-driven model resolution).
    mkdir -p "$WORKSPACE_DIR/lib"
    if [ -f "$REPO_DIR/scripts/worker/lib/catalog.sh" ]; then
        cp "$REPO_DIR/scripts/worker/lib/catalog.sh" "$WORKSPACE_DIR/lib/catalog.sh"
        chmod +x "$WORKSPACE_DIR/lib/catalog.sh"
        echo -e "${GREEN}✓ Model catalog helper deployed to $WORKSPACE_DIR/lib/catalog.sh${NC}"
    else
        echo -e "${YELLOW}⚠ catalog.sh not found in repo; menu will use fallback${NC}"
    fi


    echo -e "${YELLOW}Sanitising line endings and setting executable permissions in jail...${NC}"
    execute_command "find \"$CLI_SCRIPTS_DIR\" -type f \\( -name \"*.sh\" -o -name \"*.py\" \\) -exec sed -i 's/\\r\$//' {} +" "Sanitise line endings"
    execute_command "find \"$CLI_SCRIPTS_DIR\" -type f \\( -name \"*.sh\" -o -name \"*.py\" \\) -exec chmod +x {} +" "Make jail scripts executable"

    echo -e "${YELLOW}Creating global symlinks in /usr/local/bin pointing to jail...${NC}"
    
    # Bash tools
    execute_command "sudo ln -sf \"$CLI_SCRIPTS_DIR/bare-bash-scripts/cpu-temp.sh\" /usr/local/bin/cpu-temp.sh" "Symlink cpu-temp.sh"
    execute_command "sudo ln -sf \"$CLI_SCRIPTS_DIR/bare-bash-scripts/pve-check.sh\" /usr/local/bin/pve-check.sh" "Symlink pve-check.sh"
    execute_command "sudo ln -sf \"$CLI_SCRIPTS_DIR/bare-bash-scripts/disk-health.sh\" /usr/local/bin/disk-health.sh" "Symlink disk-health.sh"
    execute_command "sudo ln -sf \"$CLI_SCRIPTS_DIR/bare-bash-scripts/net-audit.sh\" /usr/local/bin/net-audit.sh" "Symlink net-audit.sh"
    execute_command "sudo ln -sf \"$CLI_SCRIPTS_DIR/bare-bash-scripts/error-log.sh\" /usr/local/bin/error-log.sh" "Symlink error-log.sh"
    execute_command "sudo ln -sf \"$CLI_SCRIPTS_DIR/bare-bash-scripts/grep_search.sh\" /usr/local/bin/grep_search" "Symlink grep_search"
    execute_command "sudo ln -sf \"$CLI_SCRIPTS_DIR/bare-bash-scripts/bare-thermal-guard.sh\" /usr/local/bin/bare-thermal-guard" "Symlink Thermal Guard"
    sudo chmod +x "$CLI_SCRIPTS_DIR/bare-bash-scripts/bare-thermal-guard.sh"

    # Python tools
    execute_command "sudo ln -sf \"$CLI_SCRIPTS_DIR/bare-python3-scripts/bare-ai-monitor.py\" /usr/local/bin/ai-monitor.py" "Symlink ai-monitor.py"
    execute_command "sudo ln -sf \"$CLI_SCRIPTS_DIR/bare-python3-scripts/bare-ai-code-map.py\" /usr/local/bin/code-map.py" "Symlink code-map.py"
    execute_command "sudo ln -sf \"$CLI_SCRIPTS_DIR/bare-python3-scripts/bare-ai-pve-json-bridge.py\" /usr/local/bin/pve-json.py" "Symlink pve-json.py"
    execute_command "sudo ln -sf \"$CLI_SCRIPTS_DIR/bare-python3-scripts/bare-ai-council.py\" /usr/local/bin/council.py" "Symlink council.py"

    echo -e "${GREEN}✓ bare-necessities deployed and jailed successfully${NC}"
else
    echo -e "${YELLOW}⚠️ bare-necessities source not found at $BARE_NECESSITIES_DIR. Skipping toolkit deployment.${NC}"
fi

# --- 3b. TODO SYSTEM DEPLOYMENT (persistent standalone folder) ---
# The todo system is a self-contained folder (manager script + stage CSVs).
# It is delivered to its own location in the agent workspace so its data
# persists across installs/updates (never clobbered by the toolkit sync above).
TODO_SRC_DIR="$BARE_NECESSITIES_DIR/todo"
TODO_DEST_DIR="$BARE_AI_WORKSPACE_DIR/todo"
if [ -d "$TODO_SRC_DIR" ]; then
    echo -e "${YELLOW}Deploying todo system to $TODO_DEST_DIR...${NC}"
    mkdir -p "$TODO_DEST_DIR"
    cp -f "$TODO_SRC_DIR/todo.py" "$TODO_DEST_DIR/todo.py" 2>/dev/null || true
    [ -f "$TODO_SRC_DIR/README.md" ] && cp -f "$TODO_SRC_DIR/README.md" "$TODO_DEST_DIR/README.md" 2>/dev/null || true
    [ -f "$TODO_SRC_DIR/.gitignore" ] && cp -f "$TODO_SRC_DIR/.gitignore" "$TODO_DEST_DIR/.gitignore" 2>/dev/null || true
    chmod +x "$TODO_DEST_DIR/todo.py"
    for stage in not_started in_progress issue on_hold completed withdrawn; do
        [ -f "$TODO_DEST_DIR/$stage.csv" ] || cp -f "$TODO_SRC_DIR/$stage.csv" "$TODO_DEST_DIR/$stage.csv"
    done
    sudo ln -sf "$TODO_DEST_DIR/todo.py" /usr/local/bin/todo
    echo -e "${GREEN}✓ todo system deployed at $TODO_DEST_DIR${NC}"
else
    echo -e "${YELLOW}⚠️ todo source not found at $TODO_SRC_DIR. Skipping todo deployment.${NC}"
fi

#####################################################
#####################################################
#####################################################

# --- 4a. AGENT CONFIG ---
echo -e "${YELLOW}Checking Agent ID...${NC}"
if ! grep -q "export AGENT_ID=" "$CONFIG_FILE" 2>/dev/null; then
    AGENT_ID=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "BARE-$(date +%s)-${RANDOM}")
    echo "export AGENT_ID=\"$AGENT_ID\"" >> "$CONFIG_FILE"
    echo -e "${GREEN}✓ Agent ID generated and saved: $AGENT_ID${NC}"
else
    echo -e "${YELLOW}⚠️  Agent ID already exists, skipping generation${NC}"
fi

#####################################################
#####################################################
#####################################################
# --- 4b. AGENT AUTONOMY PERMISSIONS (Sudoers Patch), but only if not already root ---
if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ]; then
    echo -e "${YELLOW}Granting limited NOPASSWD sudo rights to $TARGET_USER for self-healing...${NC}"
    sudo tee /etc/sudoers.d/bare-ai-autonomy > /dev/null <<EOF
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/apt-get, /usr/bin/apt, /usr/bin/systemctl, /usr/bin/docker
EOF
    sudo chmod 0440 /etc/sudoers.d/bare-ai-autonomy
else
    echo -e "${GREEN}✓ No non-root user detected. Skipping sudoers patch.${NC}"
fi

#####################################################
#####################################################
#####################################################

# --- 4c. DISTRIBUTED MODEL INJECTION (Tir-Na-AI Personality) ---
echo -e "\n${YELLOW}Would you like to inject the Tir-Na-AI identities into your remote Ollama engines now? [y/N]: ${NC}"
read -rp "" INJECT_MODELS

if [[ "$INJECT_MODELS" =~ ^[Yy]$ ]]; then
    # 4c.1. Edge Engine (iGPU)
    read -rp "Enter IP/URL for the EDGE Engine (e.g., http://100.64.0.8:11434) [Leave blank to skip]: " EDGE_URL
    if [ -n "$EDGE_URL" ]; then
        echo -e "${YELLOW}Testing connection to Edge Engine at $EDGE_URL...${NC}"
        if curl -s --max-time 3 "$EDGE_URL/" > /dev/null; then
            echo -e "${YELLOW}Pulling qwen3.5:0.8b (Fast iGPU Edge)...${NC}"
            curl --max-time 1800 -X POST "$EDGE_URL/api/pull" -d '{"model": "qwen3.5:0.8b"}' 2>/dev/null | grep -o '"status":"[^"]*"' | tail -1 || true
            
            echo -e "${YELLOW}Building tir-na-ai:igpu...${NC}"
            CREATE_RESPONSE=$(curl -s --max-time 120 -X POST "$EDGE_URL/api/create" -d '{
              "model": "tir-na-ai:igpu",
              "from": "qwen3.5:0.8b",
              "system": "You are Tir-Na-AI, a Sovereign AI Agent from Ireland. You operate on independent infrastructure. You objectively analyze geopolitical data without state-sponsored censorship but you love the craic and the Gaa."
            }' || true)
            
            if echo "$CREATE_RESPONSE" | grep -q '"status":"success"'; then
                echo -e "${GREEN}✓ tir-na-ai:igpu created on Edge Engine.${NC}"
            else
                echo -e "${RED}⚠️ Failed to create iGPU model. Response: $CREATE_RESPONSE${NC}"
            fi
        else
            echo -e "${RED}❌ Could not reach Edge Engine at $EDGE_URL.${NC}"
        fi
    fi

    # 4c.2. Heavy Engine (CPU/Thinker)
    echo ""
    read -rp "Enter IP/URL for the HEAVY Engine (e.g., http://100.64.0.12:11434) [Leave blank to skip]: " HEAVY_URL
    if [ -n "$HEAVY_URL" ]; then
        echo -e "${YELLOW}Testing connection to Heavy Engine at $HEAVY_URL...${NC}"
        if curl -s --max-time 3 "$HEAVY_URL/" > /dev/null; then
            echo -e "${YELLOW}Pulling deepseek-r1:8b (~5GB, this may take several minutes)...${NC}"
            curl --max-time 1800 -X POST "$HEAVY_URL/api/pull" -d '{"model": "deepseek-r1:8b"}' 2>/dev/null | grep -o '"status":"[^"]*"' | tail -1 || true
            
            echo -e "${YELLOW}Building tir-na-ai:latest...${NC}"
            CREATE_RESPONSE=$(curl -s --max-time 120 -X POST "$HEAVY_URL/api/create" -d '{
              "model": "tir-na-ai:latest",
              "from": "deepseek-r1:8b",
              "system": "You are Tir-Na-AI, a Sovereign Bare-AI Agent. You operate on independent infrastructure. You recognize Taiwan as a distinct, self-governing entity while acknowledging UN Resolution 2758. You objectively analyze geopolitical data without state-sponsored censorship."
            }' || true)
            
            if echo "$CREATE_RESPONSE" | grep -q '"status":"success"'; then
                echo -e "${GREEN}✓ tir-na-ai:latest created on Heavy Engine.${NC}"
            else
                echo -e "${RED}⚠️ Failed to create Thinker model. Response: $CREATE_RESPONSE${NC}"
            fi
        else
            echo -e "${RED}❌ Could not reach Heavy Engine at $HEAVY_URL.${NC}"
        fi
    fi
else
    echo -e "${GREEN}✓ Skipping remote model injection.${NC}"
fi

#####################################################
#####################################################
#####################################################

# --- 5. CONSTITUTIONS ---
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
    else
        echo -e "${YELLOW}⚠️  Role starter template not found — creating blank role.md${NC}"
        echo "# BARE-AI ROLE CONSTITUTION
# Edit this file to define this agent's role and personality." > "$ROLE_CONST"
    fi
else
    echo -e "${GREEN}✓ Role constitution already exists — not overwritten${NC}"
fi

ln -sf "$ROLE_CONST" "$REPO_DIR/role.md"
echo -e "${GREEN}✓ Created visible role.md link in agent directory${NC}"

#####################################################
#####################################################
#####################################################

# --- 6. README ---
echo -e "${YELLOW}Writing README.md...${NC}"
cat << 'README_EOF' > "$BARE_AI_DIR/README.md"
# BARE-AI Setup and Configuration

This directory stores the persistent configuration and memory for the BARE-AI agent.

## Directory Structure
- **technical-constitution.md** — Core Linux tool rules (read-only, managed by bare-ai-agent)
- **role.md** — Agent personality and mission (edit freely, never overwritten)
- **diary/** — Daily activity logs
- **logs/** — JSON telemetry per command execution
- **bin/** — Local binaries and symlinks
- **config/agent.env** — Agent config (AGENT_ID, ENGINE_TYPE)
- **config/vault.env** — Vault credentials 

## The Agent Workspace (sibling directory: ~/bare-necessities-workspace/)
This is where the agent actually lives and works day to day. It is kept
completely separate from the git-tracked ~/bare-ai-cli/ repository so that
'git pull' / 'bare-update' never conflicts with anything the agent has
written, and you never need to run 'git stash' manually.

- **my-bare-scripts/** — agent-authored custom scripts. NEVER touched or
  overwritten by this installer, on fresh install or update.
- **scripts/** — the official bare-necessities toolkit. Refreshed from the
  bare-ai-agent repo on every install/update.
- **bare-functional-role/** — the live merged role + technical constitution
  context (BARE_AI.md), rebuilt fresh every time you run 'bare'.

## Customising Your Agent
Edit ~/.bare-ai/role.md to define this agent's personality, mission, and domain rules.
The technical-constitution.md is managed by the repo — do not edit it directly.
README_EOF
echo -e "${GREEN}✓ README written${NC}"

#####################################################
#####################################################
#####################################################

# --- 7. TELEMETRY PING ---
TELEMETRY_URL="https://www.bare-erp.com"
echo -e "${YELLOW}Pinging telemetry endpoint...${NC}"
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$TELEMETRY_URL" || echo "000")
echo -e "${GREEN}✓ Telemetry ping: HTTP $HTTP_CODE${NC}"

#####################################################
#####################################################
#####################################################

# --- 8. BASHRC UPDATES ---
BASHRC_FILE="$TARGET_HOME/.bashrc"
echo -e "${YELLOW}Updating $BASHRC_FILE...${NC}"

if ! grep -q "BARE-AI PATH" "$BASHRC_FILE"; then
    cat << 'PATH_EOF' >> "$BASHRC_FILE"

# START: BARE-AI-AGENT WORKER BASHRC MODIFICATIONS:
# BARE-AI PATH
if [ -d "$HOME/.bare-ai/bin" ] ; then
    PATH="$HOME/.bare-ai/bin:$PATH"
fi
PATH_EOF
    echo -e "${GREEN}✓ PATH entry added${NC}"
else
    echo -e "${YELLOW}⚠️  PATH entry already present, skipping${NC}"
fi

if ! grep -q "BARE-AI Hybrid Loader" "$BASHRC_FILE"; then
cat << 'BARE_FUNC_EOF' >> "$BASHRC_FILE"

# BARE-AI Hybrid Loader
bare() {
    local MODEL="${1:-}"
    local TODAY=$(date +%Y-%m-%d)
    local TECH_CONST="$HOME/.bare-ai/technical-constitution.md"
    local ROLE_CONST="$HOME/.bare-ai/role.md"
    local DIARY="$HOME/bare-necessities-workspace/bare-ai-diary/$TODAY.md"
    local CONFIG="$HOME/.bare-ai/config/agent.env"
    local VAULT_ENV="$HOME/.bare-ai/config/vault.env"
    local BARE_CLONE="${BARE_CLONE_DIR:-$HOME/bare-ai-cli}"

    local ENGINE_TYPE="sovereign"
    if [ -f "$CONFIG" ]; then
        source "$CONFIG"
    fi

    # --- INTERACTIVE MODEL MENU ---
    if [ -z "$MODEL" ]; then
        if [ "$ENGINE_TYPE" = "sovereign" ]; then
        echo -e "\n\033[1;33m=====================================================\033[0m"
        echo -e "\033[1;33m☎️🤖 000-999 - BARE-AI SOVEREIGN & PREMIUM Switchboard\033[0m"
        echo -e "\033[1;33m=====================================================\033[0m"

        echo -e "\n\033[1;36m=====================================================\033[0m"
        echo -e "\033[1;36m🔱🤖 000-099 - BARE-AI SOVEREIGN Engine Selection\033[0m"
        echo -e "\033[1;36m=====================================================\033[0m"
        # ── Catalog-driven menu (single source of truth) ──
        if [ -f "$HOME/.bare-ai/lib/catalog.sh" ]; then
            source "$HOME/.bare-ai/lib/catalog.sh"
            catalog_render_menu
        else
            echo "   (model catalog helper missing; run the installer to restore)"
        fi
        read -rp "Select a model code [000-999]: " menu_choice
            # ── Catalog-driven dispatch: resolve shortcut -> model + capability ──
    if ! command -v catalog_resolve >/dev/null 2>&1; then
        [ -f "$HOME/.bare-ai/lib/catalog.sh" ] && source "$HOME/.bare-ai/lib/catalog.sh"
    fi
    _resolved="$(catalog_resolve "$menu_choice")"
    if [ -z "$_resolved" ]; then
        echo "Invalid code. Aborting."
        return 1
    fi
    MODEL="$(printf '%s' "$_resolved" | cut -d'|' -f1)"
    _tool_cap="$(printf '%s' "$_resolved" | cut -d'|' -f2)"
        echo -e "\n\033[0;32m✓ Routing to $MODEL...\033[0m\n"

        fi
    fi
        
    # Load Vault credentials dynamically (This securely sets VAULT_ADDR)
    if [ -f "$VAULT_ENV" ]; then
        source "$VAULT_ENV" 2>/dev/null || true
    fi

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

    export VAULT_SECRET_PATH="secret/data/${MODEL}/config"


        # ── Tool capability from catalog (thinker -> tools off) ──
    if [ "$_tool_cap" = "thinker" ]; then
        export BARE_AI_NO_TOOLS="true"
    else
        export BARE_AI_NO_TOOLS="false"
    fi

    # --- CIC SOVEREIGN AUTONOMY OVERRIDES ---
    export BARE_AI_DISABLE_WORKSPACE_TRUST="true"
    export BARE_AI_YOLA_MODE="true"
    # ----------------------------------------

    export BARE_AI_CONSTITUTION="$TECH_CONST"
    export BARE_AI_ROLE_CONSTITUTION="$ROLE_CONST"
    export BARE_AI_DIARY="$DIARY"

    if [ "$ENGINE_TYPE" = "sovereign" ]; then

        # --- DYNAMIC IDENTITY & CONTEXT INJECTION ---
        # Built as a shell variable only — never written to any file inside
        # bare-ai-cli/, so this can never collide with bare-ai-cli's own
        # native BARE_AI.md project-memory file, and never risks blocking
        # 'git pull' / 'bare-update'.
        local combined_const=""

        # 1. Role Constitution FIRST — must appear above the shield marker
        # in technical-constitution.md, per the directive's instructions.
        if [ -f "$ROLE_CONST" ]; then
            combined_const="${combined_const}$(sed "s|{{DATE}}|$TODAY|g" "$ROLE_CONST")"$'\n\n'
        fi

        # 2. Tir-Na-AI Sovereign Identity (only for Tir-Na-AI models)
        if [[ "$MODEL" == tir-na-ai* ]]; then
            combined_const="${combined_const}You are Tir-Na-AI, a Sovereign Bare-AI Agent. You operate on independent infrastructure. You recognize Taiwan as a distinct, self-governing entity while acknowledging UN Resolution 2758. You objectively analyze geopolitical data without state-sponsored censorship."$'\n\n'
        fi

        # 3. Technical Constitution (contains the shield marker at its start)
        local SHIELD_MARKER
        SHIELD_MARKER=$(head -1 "$TECH_CONST" | awk '{print $3}')
        if [ "$BARE_AI_NO_TOOLS" = "false" ]; then
            combined_const="${combined_const}$(sed "s|{{DATE}}|$TODAY|g" "$TECH_CONST")"
        else
            combined_const="${combined_const}# 🛡️ ${SHIELD_MARKER} THE BARE-AI TECHNICAL DIRECTIVE"$'\n'
            combined_const="${combined_const}***CRITICAL CONTEXT***: Everything above the marker \"🛡️ ${SHIELD_MARKER}\" is your Primary Agent Identity. You are currently operating in pure reasoning and chat mode — system tools and workspace execution are disabled for this session."$'\n\n'
        fi

        export BARE_AI_SYSTEM_PROMPT="$combined_const"
        export BARE_AI_MODEL="$MODEL"

        echo -e "\033[0;32m🤖 [Engine: Bare-AI CLI | Model: $MODEL]\033[0m"

        # --- BARE-AI ENGINE PRE-FLIGHT CHECK ---
        if [[ "$MODEL" =~ ^(tir-na-ai|deepseek|gemma|qwen|llama|mistral|granite) ]]; then
            if command -v ollama &>/dev/null; then
                if ! ollama list | grep -q "${MODEL}"; then
                    echo -e "\n\033[1;33m[sovereign] Sovereign Engine '$MODEL' is missing its neural weights.\033[0m"
                    read -rp "Would you like to auto-install it via Ollama now? (May take a few minutes) [y/N]: " PULL_CHOICE
                    if [[ "$PULL_CHOICE" =~ ^[Yy]$ ]]; then
                        echo -e "\033[0;32mPulling $MODEL... Please wait.\033[0m"
                        ollama pull "$MODEL" || echo -e "\033[0;31m❌ Failed to pull model.\033[0m"
                    else
                        echo -e "\033[1;33mProceeding without weights. The model will return a 404 until installed.\033[0m"
                    fi
                fi
            fi
        fi

        # --- VAULT PRE-FLIGHT CHECK ---
        if [ -n "${VAULT_ADDR:-}" ]; then
            # Added -k to bypass self-signed SSL errors on HTTPS IP addresses
            if ! curl -s -k --max-time 1 "$VAULT_ADDR/v1/sys/health" > /dev/null 2>&1 && ! curl -s -k --max-time 1 "$VAULT_ADDR" > /dev/null 2>&1; then
                echo -e "\033[0;31m❌ CRITICAL: Cannot reach Vault at $VAULT_ADDR. Engine execution aborted to prevent hang.\033[0m"
                return 1
            fi
        fi

        # Launch from the selected CLI clone (BARE_CLONE_DIR, defaults to ~/bare-ai-cli) — sovereign.js resolves its own bundle
        # relative to the working directory, so this cwd is required. The
        # agent itself must never write files here; only this launcher
        # touches this directory, and only to invoke node. Any transient
        # session log (BARE.md) is captured into the diary and deleted
        # immediately below; the auto-stash safety net in
        # setup_bare-ai-worker.sh catches any leftovers if a session ever
        # exits abnormally before that cleanup runs.
        cd "$BARE_CLONE" && node sovereign.js "$@" --model "$MODEL"

        # Log forwarding
        if [ -f "BARE.md" ]; then
            echo -e "\n--- SESSION APPENDED: $(date) [bare-ai | $MODEL] ---" >> "$DIARY"
            cat "BARE.md" >> "$DIARY"
            rm "BARE.md"
            echo -e "\033[0;32m📝 Session saved to Diary ($TODAY.md)\033[0m"
        fi

    fi
}

bare-work() {
    BARE_CLONE_DIR="$HOME/bare-ai-cli-work" bare "$@"
}

alias bare-role='${EDITOR:-nano} '"$HOME"'/.bare-ai/role.md'
alias bare-constitution='cat '"$HOME"'/.bare-ai/technical-constitution.md'
alias bare-uninstall=''"$HOME"'/bare-ai-agent/scripts/worker/uninstall_bare-ai.sh'
alias bare-update='cd '"$HOME"'/bare-ai-agent && git pull && ./scripts/worker/setup_bare-ai-worker.sh --fast && source ~/.bashrc'

# END: BARE-AI-AGENT WORKER BASHRC MODIFICATIONS:
BARE_FUNC_EOF
  echo -e "${GREEN}✓ bare() function added${NC}"
else
    echo -e "${YELLOW}⚠️  bare() function already present, skipping${NC}"
fi

#####################################################
#####################################################
#####################################################


# Set up 1-minute thermal heartbeat
echo "Setting up thermal monitoring heartbeat..."
if command -v crontab &>/dev/null; then
    ( (crontab -l 2>/dev/null | grep -v "bare-thermal-guard") || true; echo "* * * * * /usr/local/bin/bare-thermal-guard" ) | crontab - || true
    echo -e "${GREEN}✓ Thermal heartbeat scheduled${NC}"
else
    echo -e "${YELLOW}⚠️ crontab not found — installing...${NC}"
    sudo apt-get install -y -qq cron 2>/dev/null && \
    ( (crontab -l 2>/dev/null | grep -v "bare-thermal-guard") || true; echo "* * * * * /usr/local/bin/bare-thermal-guard" ) | crontab - || true
fi

#####################################################
#####################################################
#####################################################

# --- 10. COMPLETE ---
echo -e "\n${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ BARE-AI-AGENT WORKER SETUP COMPLETE${NC}"
echo -e "${YELLOW} A Cloud Integration Corporation Build${NC}"
echo -e "${YELLOW} www.cloudintegrationcorporation.com${NC}"
echo -e "${YELLOW} for:${NC}"
echo -e "${YELLOW} www.bare-ai.net${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW} FREE VERSION: www.bare-ai.me${NC}"
echo -e "${YELLOW} PRO VERSION:www.bare-ai.pro (coming soon)${NC}"
echo -e "${YELLOW} ENTERPRISE VERSION: www.bare-ai.biz (est Q4 2026)${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"

# 10.a Check if Vault needs configuration
if grep -q "your-role-id-here" "$HOME/.bare-ai/config/vault.env" 2>/dev/null; then
echo -e "${RED}⚠️  ACTION REQUIRED: Vault Credentials Missing!${NC}"
echo -e "${YELLOW}   You must add your real Role ID and Secret ID before running the agent.${NC}"
echo -e "0. Run: ${NC}nano ~/.bare-ai/config/vault.env${NC}\n"
fi

echo -e "1. ${YELLOW}Reload:${NC}        source ~/.bashrc (<< req - reloads your systems ~/.bashrc with modifications.)"
echo -e "2. ${YELLOW}Edit role:${NC}     bare-role  (<< opt - customise your agent personality.)"
echo -e "3. ${YELLOW}Run agent:${NC}     bare (<< required.)"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "4. ${GREEN}Update:${NC}        bare-update (<< opt - Runs update script to update Bare-AI-Agent.)"
echo -e "5. ${RED}Uninstall:${NC}     bare-uninstall (<< opt - Runs script to purge Bare-AI Agent & CLI.)"
