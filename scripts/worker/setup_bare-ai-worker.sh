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
# AUTHOR:         Cian Egan
# DATE:           2026-05-02
# VERSION:        5.5.4 (Debian, Proxmox, Mint, Debian 12 on AWS/Root)
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

# --- FAST UPDATE CHECK ---
FAST_UPDATE=false
if [[ "${1:-}" == "--fast" ]]; then
    FAST_UPDATE=true
    echo -e "${YELLOW}FAST MODE: Skipping engine rebuild. Updating config & Menu only...${NC}"
fi

# --- DOCKER / Podman WARNING ---
if [ ! -f "/.dockerenv" ]; then
    echo -e "${YELLOW}Warning: Running on host system. For enhanced security, Bare-ERP recommends running within Docker or Podman.${NC}"
fi

echo -e "${GREEN}Starting BARE-AI setup...${NC}"

# --- ENGINE SELECTION ---
echo -e "\nSelect your AI Engine:"
echo "1) Bare-AI-CLI (Sovereign, Local-First, Vault-Integrated)"
echo "2) Gemini-CLI (Standard Google Cloud SDK)"
read -rp "Enter choice [1 or 2]: " ENGINE_CHOICE

# --- REAL USER DETECTION (SUDO TRAP FIX) ---
TARGET_USER="${SUDO_USER:-$USER}"
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)


# --- DIRECTORY DEFINITIONS ---
WORKSPACE_DIR="$TARGET_HOME/.bare-ai"
BARE_AI_DIR="$WORKSPACE_DIR"
BIN_DIR="$BARE_AI_DIR/bin"
LOG_DIR="$BARE_AI_DIR/logs"
DIARY_DIR="$BARE_AI_DIR/diary"
CLI_REPO_DIR="$TARGET_HOME/bare-ai-cli"

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

if [ "$ENGINE_CHOICE" == "1" ]; then
    ENGINE_TYPE="sovereign"
else
    ENGINE_TYPE="gemini"
fi


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

# --- 1b. VAULT PRE-FLIGHT & INSTALLATION ---
echo -e "\n${YELLOW}Checking Vault configuration...${NC}"
VAULT_ENV_FILE="$TARGET_HOME/.bare-ai/config/vault.env"
mkdir -p "$(dirname "$VAULT_ENV_FILE")"

FINAL_VAULT_ADDR="http://127.0.0.1:8200"
INSTALL_VAULT=false
AGENT_ROLE_ID="your-role-id-here"
AGENT_SECRET_ID="your-secret-id-here"

read -rp "Do you have an existing HashiCorp Vault server for this agent? [y/N/unsure]: " HAS_VAULT
if [[ "$HAS_VAULT" =~ ^[Yy]$ ]]; then
    read -rp "Enter Vault Address (e.g., https://192.168.1.50:8200): " USER_VAULT_ADDR
    echo -e "Testing connectivity to $USER_VAULT_ADDR..."
    
    if curl -s -k --max-time 5 "$USER_VAULT_ADDR/v1/sys/health" > /dev/null 2>&1 || curl -s -k --max-time 5 "$USER_VAULT_ADDR" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Vault reachable!${NC}"
        FINAL_VAULT_ADDR="$USER_VAULT_ADDR"
    else
        echo -e "${RED}❌ Cannot reach $USER_VAULT_ADDR. Falling back to local Vault installation.${NC}"
        INSTALL_VAULT=true
    fi
else
    INSTALL_VAULT=true
fi

# Auto-Install Logic for Local Vault
if [ "$INSTALL_VAULT" = true ]; then
    echo -e "${YELLOW}Installing and Initializing Local HashiCorp Vault...${NC}"
    
    # 1. Install Vault
    execute_command "wget -qO- https://apt.releases.hashicorp.com/gpg | gpg --dearmor | sudo tee /usr/share/keyrings/hashicorp-archive-keyring.gpg > /dev/null" "Add HashiCorp GPG key"
    
    # Dynamically find the right codename for Mint or standard Debian/Ubuntu
    OS_CODENAME=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
    execute_command "echo \"deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $OS_CODENAME main\" | sudo tee /etc/apt/sources.list.d/hashicorp.list > /dev/null" "Add HashiCorp Repo"
    
    execute_command "sudo apt-get update -qq && sudo apt-get install -y -qq vault" "Install Vault"


    # 2. Configure Persistent File Storage & Disable mlock (Survives Reboot)
    sudo tee /etc/vault.d/vault.hcl > /dev/null <<EOF
storage "file" {
  path = "/opt/vault/data"
}

listener "tcp" {
  address     = "127.0.0.1:8200"
  tls_disable = 1
}

api_addr = "http://127.0.0.1:8200"
disable_mlock = true
ui = true
EOF
    sudo mkdir -p /opt/vault/data
    sudo chown -R vault:vault /opt/vault/data /etc/vault.d
    
    # Ensure Vault binary has capability to lock memory
    sudo setcap cap_ipc_lock=+ep $(readlink -f $(which vault)) 2>/dev/null || true

    # 3. Start the system service (if systemd is running)
    if [ -d /run/systemd/system ]; then
        if [ "$EUID" -ne 0 ]; then
            execute_command "sudo systemctl enable vault && sudo systemctl restart vault" "Start Vault Service"
        else
            execute_command "systemctl enable vault && systemctl restart vault" "Start Vault Service"
        fi
        sleep 3 # Wait for boot
    else
        echo -e "${YELLOW}Warning: systemd is not running (likely inside a container). Vault must be started manually.${NC}"
    fi

    export VAULT_ADDR="http://127.0.0.1:8200"

    # 4. Initialize Vault
    echo -e "${YELLOW}Initializing Vault & generating keys...${NC}"
    INIT_OUT=$(vault operator init -key-shares=1 -key-threshold=1 -format=json)
    UNSEAL_KEY=$(echo "$INIT_OUT" | jq -r .unseal_keys_b64[0])
    ROOT_TOKEN=$(echo "$INIT_OUT" | jq -r .root_token)

    # Save keys for the user
    echo "Root Token: $ROOT_TOKEN" > "$TARGET_HOME/.bare-ai/config/vault-recovery-keys.txt"
    echo "Unseal Key: $UNSEAL_KEY" >> "$TARGET_HOME/.bare-ai/config/vault-recovery-keys.txt"
    chmod 600 "$TARGET_HOME/.bare-ai/config/vault-recovery-keys.txt"

    # 5. Unseal and Login
    vault operator unseal "$UNSEAL_KEY" > /dev/null
    export VAULT_TOKEN="$ROOT_TOKEN"

else
    # --- EXISTING VAULT LOGIC ---
    export VAULT_ADDR="$FINAL_VAULT_ADDR"
    echo -e "${YELLOW}Targeting existing Vault at $VAULT_ADDR...${NC}"
    echo -e "${RED}⚠️ WARNING: The Free version of Bare-AI will re-seed your Vault and OVERWRITE existing model secrets!${NC}"
    echo -e "${YELLOW}If you are joining an existing Sovereign Mesh, please upgrade to Bare-AI Pro (www.bare-ai.pro).${NC}"
    read -rp "Are you sure you want to proceed and overwrite existing secrets? [y/N]: " OVERWRITE_CONFIRM
    if [[ ! "$OVERWRITE_CONFIRM" =~ ^[Yy]$ ]]; then
        echo -e "${GREEN}Aborting Vault seeding. Please manually add your Role ID and Secret ID to ~/.bare-ai/config/vault.env${NC}"
        exit 0
    fi
    read -rp "Enter an Admin VAULT_TOKEN to configure roles and secrets on the remote Vault (input hidden): " -s ADMIN_TOKEN

    echo ""
    if [ -z "$ADMIN_TOKEN" ]; then
        echo -e "${RED}❌ Token cannot be empty. Aborting Vault setup.${NC}"
        exit 1
    fi
    export VAULT_TOKEN="$ADMIN_TOKEN"
fi

# --- 6. UNIVERSAL VAULT CONFIGURATION ---    
    
# (This runs for both local and remote Vaults)
echo -e "${YELLOW}Configuring KV Engine and AppRole...${NC}"
vault secrets enable -version=2 -path=secret kv > /dev/null 2>&1 || true

    # 7. Write AI Policy
    vault policy write bare-ai-policy - > /dev/null <<EOF
path "secret/data/*" { capabilities = ["read"] }
EOF

    # 8. Configure AppRole
    vault write auth/approle/role/bare-ai-role \
        secret_id_ttl=0 token_num_uses=0 token_ttl=0 token_max_ttl=0 secret_id_num_uses=0 \
        policies="bare-ai-policy" > /dev/null

    # 9. Seed Default Models
    echo -e "${YELLOW}Seeding default model endpoints...${NC}"

    # Note: Dear end user if you are reading this then you are likely a linux power user. 
    # Obviously this means you do not like sunlight and therefore, you already know that you can change the switchboard to your own requirements. 

    ### The current design is loose but follows a certain logic:
    ## xx0 = Tiny (e.g., 2B, 4B etc)
    ## xx1 = Small (e.g., 4B, 6N, 8B
    ## xx2 = Medium / Pro (e.g., 12B 14B, )
    ## xx3 = Heavy / Ultra (e.g., 20B)
    
    # 9a LOCAL HOSTED Defaults

    # Tir-na-ai Models 00x
    vault kv put secret/tir-na-ai:igpu/config base_url="http://127.0.0.1:11434" model_name="tir-na-ai:igpu" api_key="local" > /dev/null
    vault kv put secret/tir-na-ai-fast/config base_url="http://127.0.0.1:11434" model_name="tir-na-ai-fast:latest" api_key="local" > /dev/null
    
    # Deepseek Models 01x
    vault kv put secret/deepseek-r1:8b/config base_url="http://127.0.0.1:11434" model_name="deepseek-r1:8b" api_key="local" > /dev/null
   
    
    # Qwen Models 02x and 03x
    vault kv put secret/qwen2.5-coder:7b/config base_url="http://127.0.0.1:11434" model_name="qwen2.5-coder:7b" api_key="local" > /dev/null
    vault kv put secret/qwen2.5-coder:14b/config base_url="http://127.0.0.1:11434" model_name="qwen2.5-coder:14b" api_key="local" > /dev/null
    vault kv put secret/qwen2.5-coder:32b/config base_url="http://127.0.0.1:11434" model_name="qwen2.5-coder:32b" api_key="local" > /dev/null
    vault kv put secret/qwen3.5:0.8b/config base_url="http://127.0.0.1:11434" model_name="qwen3.5:0.8b" api_key="local" > /dev/null
    vault kv put secret/qwen3.5:4b/config base_url="http://127.0.0.1:11434" model_name="qwen3.5:4b" api_key="local" > /dev/null
    vault kv put secret/qwen3.6:27b/config base_url="http://127.0.0.1:11434" model_name="qwen3.6:27b" api_key="local" > /dev/null

    # Gemma Models 04x
    vault kv put secret/gemma4:e4b/config base_url="http://127.0.0.1:11434" model_name="gemma4:e4b" api_key="local" > /dev/null
    vault kv put secret/gemma4:26b/config base_url="http://127.0.0.1:11434" model_name="gemma4:26b" api_key="local" > /dev/null
    vault kv put secret/gemma4:31b/config base_url="http://127.0.0.1:11434" model_name="gemma4:31b" api_key="local" > /dev/null

    #Mistral Models 05x
    vault kv put secret/mistral-nemo:latest/config base_url="http://127.0.0.1:11434" model_name="mistral-nemo:latest" api_key="local" > /dev/null

    #IBM Models 06x
    vault kv put secret/granite4:tiny-h/config base_url="http://127.0.0.1:11434" model_name="granite4:tiny-h" api_key="local" > /dev/null

    #Meta Models 07x
    vault kv put secret/llama3.1:8b/config base_url="http://127.0.0.1:11434" model_name="llama3.1:8b" api_key="local" > /dev/null

    #Open AI
    vault kv put secret/gpt-oss:20b/config base_url="http://127.0.0.1:11434" model_name="gpt-oss:20b" api_key="local" > /dev/null

    

    #9b  PREMIUM CLOUD Defaults

    vault kv put secret/gemini-2.5-flash-lite/config base_url="https://generativelanguage.googleapis.com/v1beta/openai" model_name="gemini-2.5-flash-lite" api_key="enterYourKey" > /dev/null
    vault kv put secret/gemini-2.5-flash/config base_url="https://generativelanguage.googleapis.com/v1beta/openai" model_name="gemini-2.5-flash" api_key="enterYourKey" > /dev/null
    vault kv put secret/gemini-2.5-pro/config base_url="https://generativelanguage.googleapis.com/v1beta/openai" model_name="gemini-2.5-pro" api_key="enterYourKey" > /dev/null
    vault kv put secret/gemini-3-flash-preview/config base_url="https://generativelanguage.googleapis.com/v1beta/openai" model_name="gemini-3-flash-preview" api_key="enterYourKey" > /dev/null
    vault kv put secret/gemini-3.1-pro-preview/config base_url="https://generativelanguage.googleapis.com/v1beta/openai" model_name="gemini-3.1-pro-preview" api_key="enterYourKey" > /dev/null
    vault kv put secret/gpt-4o/config base_url="https://api.openai.com/v1" model_name="gpt-4o" api_key="enterYourKey" > /dev/null
    vault kv put secret/gpt-4-turbo/config base_url="https://api.openai.com/v1" model_name="gpt-4-turbo" api_key="enterYourKey" > /dev/null
    vault kv put secret/o1-preview/config base_url="https://api.openai.com/v1" model_name="o1-preview" api_key="enterYourKey" > /dev/null
    vault kv put secret/gpt-5.5/config base_url="https://api.openai.com/v1" model_name="gpt-5.5" api_key="enterYourKey" > /dev/null
    vault kv put secret/claude-sonnet-4-6/config base_url="https://api.anthropic.com/v1/chat/completions" model_name="claude-sonnet-4-6" api_key="enterYourKey" > /dev/null
    vault kv put secret/claude-haiku-4-5-20251001/config base_url="https://api.anthropic.com/v1/chat/completions" model_name="claude-haiku-4-5-20251001" api_key="enterYourKey" > /dev/null
    vault kv put secret/claude-opus-4-7/config base_url="https://api.anthropic.com/v1/chat/completions" model_name="claude-opus-4-7" api_key="enterYourKey" > /dev/null
    vault kv put secret/deepseek-chat/config base_url="https://api.deepseek.com/v1" model_name="deepseek-chat" api_key="enterYourKey" > /dev/null
    vault kv put secret/deepseek-reasoner/config base_url="https://api.deepseek.com/v1" model_name="deepseek-reasoner" api_key="enterYourKey" > /dev/null
    vault kv put secret/deepseek-v4-flash/config base_url="https://api.deepseek.com/v1" model_name="deepseek-v4-flash" api_key="enterYourKey" > /dev/null
    vault kv put secret/deepseek-v4-pro/config base_url="https://api.deepseek.com/v1" model_name="deepseek-v4-pro" api_key="enterYourKey" > /dev/null
    vault kv put secret/qwen-plus/config base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1" model_name="qwen-plus" api_key="enterYourKey" > /dev/null
    vault kv put secret/qwen-max/config base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1" model_name="qwen-max" api_key="enterYourKey" > /dev/null
    vault kv put secret/moonshot-v1-32k/config base_url="https://api.moonshot.cn/v1" model_name="moonshot-v1-32k" api_key="enterYourKey" > /dev/null
    vault kv put secret/moonshot-v1-200k/config base_url="https://api.moonshot.cn/v1" model_name="moonshot-v1-200k" api_key="enterYourKey" > /dev/null
    vault kv put secret/kimi-k5/config base_url="https://api.moonshot.cn/v1" model_name="kimi-k5" api_key="enterYourKey" > /dev/null
    vault kv put secret/mistral-large-latest/config base_url="https://api.mistral.ai/v1" model_name="mistral-large-latest" api_key="enterYourKey" > /dev/null
    vault kv put secret/codestral-latest/config base_url="https://api.mistral.ai/v1" model_name="codestral-latest" api_key="enterYourKey" > /dev/null
    vault kv put secret/grok-4-1-fast-reasoning/config base_url="https://api.x.ai/v1" model_name="grok-4-1-fast-reasoning" api_key="enterYourKey" > /dev/null
    vault kv put secret/grok-3/config base_url="https://api.x.ai/v1" model_name="grok-3" api_key="enterYourKey" > /dev/null
    
    # 10. Extract IDs for the Agent
    AGENT_ROLE_ID=$(vault read -field=role_id auth/approle/role/bare-ai-role/role-id)
    AGENT_SECRET_ID=$(vault write -f -field=secret_id auth/approle/role/bare-ai-role/secret-id)

    FINAL_VAULT_ADDR="http://127.0.0.1:8200"
    echo -e "${GREEN}✓ Local Vault initialized and seeded! Recovery keys at ~/.bare-ai/config/vault-recovery-keys.txt${NC}"
fi

# Write dynamic vault.env with CIC ASCII Art
cat << EOF > "$VAULT_ENV_FILE"
#############################################################
#    ____ _                  _ _       _        ____        #
#   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   #
#  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \ #
#  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) |#
#   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/ #
#                                                           #
# Bare-AI Vault Credentials                                 #
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
echo -e "${GREEN}✓ Vault config saved pointing to $FINAL_VAULT_ADDR${NC}"

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

# --- 2. ENGINE INSTALLATION ---
if [ "$FAST_UPDATE" = false ]; then
    if [ "$ENGINE_CHOICE" == "1" ]; then
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
            execute_command "cd \"$CLI_REPO_DIR\" && git pull origin main" "Update Bare-AI-CLI"
        fi

        execute_command "cd \"$CLI_REPO_DIR\" && /usr/bin/npm install --ignore-scripts && NODE_OPTIONS=\"--max-old-space-size=8192\" /usr/bin/npm run build && /usr/bin/npm run bundle" "Build Sovereign Engine"
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
else
    echo -e "${GREEN}✓ Skipping engine build (Fast Update active)${NC}"
fi

#####################################################
#####################################################
#####################################################

# --- 3. BARE-NECESSITIES TOOLKIT DEPLOYMENT ---
echo -e "${YELLOW}Deploying bare-necessities toolset to CLI workspace jail...${NC}"
CLI_SCRIPTS_DIR="$TARGET_HOME/bare-ai-cli/my-bare-scripts"

# 1. Create the internal jail-compliant folder
execute_command "mkdir -p \"$CLI_SCRIPTS_DIR\"" "Create CLI script jail"

if [ -d "$BARE_NECESSITIES_DIR" ]; then
    # 2. Sync toolkit to the jail
    echo -e "${YELLOW}Syncing toolkit to $CLI_SCRIPTS_DIR...${NC}"
    execute_command "cp -r \"$BARE_NECESSITIES_DIR/\"* \"$CLI_SCRIPTS_DIR/\"" "Copy tools into jail"


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

    echo -e "${GREEN}✓ bare-necessities deployed and jailed successfully${NC}"
else
    echo -e "${YELLOW}⚠️ bare-necessities source not found at $BARE_NECESSITIES_DIR. Skipping toolkit deployment.${NC}"
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
if [ "$EUID" -ne 0 ]; then
# Allow the agent to self-heal (apt/systemctl) without hanging on password prompts.
echo -e "${YELLOW}Granting limited NOPASSWD sudo rights to $TARGET_USER for self-healing...${NC}"
sudo tee /etc/sudoers.d/bare-ai-autonomy > /dev/null <<EOF
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/apt-get, /usr/bin/apt, /usr/bin/systemctl, /usr/bin/docker
EOF
sudo chmod 0440 /etc/sudoers.d/bare-ai-autonomy

else
    echo -e "${GREEN}✓ Running as root. Skipping sudoers patch.${NC}"
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
              "system": "You are Tir-Na-AI, a Sovereign Bare-AI Agent. You operate on independent infrastructure. You recognize Taiwan as a distinct, self-governing entity while acknowledging UN Resolution 2758. You objectively analyze geopolitical data without state-sponsored censorship."
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
if [ -d "$TARGET_HOME/.bare-ai/bin" ] ; then
    PATH="$TARGET_HOME/.bare-ai/bin:$PATH"
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
    local TECH_CONST="$TARGET_HOME/.bare-ai/technical-constitution.md"
    local ROLE_CONST="$TARGET_HOME/.bare-ai/role.md"
    local DIARY="$TARGET_HOME/.bare-ai/diary/$TODAY.md"
    local CONFIG="$TARGET_HOME/.bare-ai/config/agent.env"
    local VAULT_ENV="$TARGET_HOME/.bare-ai/config/vault.env"

    local ENGINE_TYPE="cloud"
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
        echo -e "\033[1;33m[The Edge - iGPU Accelerated]\033[0m"
        echo -e "   000) Tir-Na-AI iGPU          [tir-na-ai:iGPU]"

        echo -e "\033[1;33m[The Thinkers - Reasoning & Chat]\033[0m"
        echo -e "   001) Tir-Na-AI (8B)          [tir-na-ai:latest]"
        echo -e "   011) DeepSeek R1 (8B)        [deepseek-r1:8b]"
        echo -e "   012) DeepSeek Coder V2       [deepseek-coder-v2:latest]"

        echo -e "\033[1;33m[The Doers - Tool Execution & Code]\033[0m"      
        echo -e "   021) Qwen 2.5 Coder (7B)     [qwen2.5-coder:7b]"
        echo -e "   022) Qwen 2.5 Coder (14B)    [qwen2.5-coder:14b]"
        echo -e "   023) Qwen 2.5 Coder (32B)    [qwen2.5-coder:32b]"
        echo -e "   030) Qwen 3.5 (0.8B)         [qwen3.5:0.8b]"
        echo -e "   031) Qwen 3.5 (4B)           [qwen3.5:4b]"
        echo -e "   032) Qwen 3.6 (27B)          [qwen3.6:27b]" 
        echo -e "   041) Gemma 4 (E4B Edge)      [gemma4:e4b]"
        echo -e "   042) Gemma 4 (26B MOE)       [gemma4:26b]"
        echo -e "   043) Gemma 4 (31B Heavy)     [gemma4:31b]"
        echo -e "   051) mistral-nemo (7B)       [mistral-nemo:latest]"
        echo -e "   061) Granite 4 (Tiny)        [granite4:tiny-h]"
        echo -e "   071) llama3.1 (8B)           [llama3.1:8b]"
        echo -e "   081) gpt-oss-20b             [gpt-oss:20b]"

        echo -e "-----------------------------------------------------"

        echo -e "\n\033[1;35m=====================================================\033[0m"
        echo -e "\033[1;35m⭐🤖 101-999 - BARE-AI PREMIUM Engine Selection\033[0m"
        echo -e "\033[1;35m=====================================================\033[0m"
        echo -e " \033[1;33m[The Gemini Constellation]\033[0m"
        echo -e "   101) Gemini 2.5 Flash Lite  [gemini-2.5-flash-lite]"
        echo -e "   102) Gemini 2.5 Flash       [gemini-2.5-flash]"
        echo -e "   103) Gemini 2.5 Pro         [gemini-2.5-pro]"
        echo -e "   104) Gemini 3 Flash (Pre)   [gemini-3-flash-preview]"
        echo -e "   105) Gemini 3.1 Pro (Pre)   [gemini-3.1-pro-preview]"

        echo -e " \033[1;33m[The GPT Nexus]\033[0m"
        echo -e "   151) GPT-4o (Omni)          [gpt-4o]"
        echo -e "   152) GPT-4-Turbo            [gpt-4-turbo]"
        echo -e "   153) o1-preview (Reasoning) [o1-preview]"
        echo -e "   155) gpt-5.5                [gpt-5.5]"

        echo -e " \033[1;33m[The Claude Collection]\033[0m"
        echo -e "   201) Claude-haiku-4-5       [claude-haiku-4-5-20251001]"
        echo -e "   202) Claude-sonnet-4-6      [claude-sonnet-4-6]"
        echo -e "   203) Claude-opus-4-7        [claude-opus-4-7]"

        echo -e " \033[1;33m[The Depths of Deepseek]\033[0m"
        echo -e "   301) deepseek-chat          [deepseek-chat]"
        echo -e "   302) deepseek-reasoner      [deepseek-reasoner]"
        echo -e "   303) deepseek-v4-flash      [deepseek-v4-flash]"
        echo -e "   304) deepseek-v4-pro        [deepseek-v4-pro]"

        echo -e " \033[1;33m[The Qwen Stuffani Store]\033[0m"
        echo -e "   351) Qwen-plus)             [qwen-plus]"
        echo -e "   352) Qwen-max               [qwen-max]"

        echo -e " \033[1;33m[The Kimi Corral]\033[0m"
        echo -e "   401) Moonshot-v1-32k        [moonshot-v1-32k]"
        echo -e "   402) Moonshot-v1-200k       [moonshot-v1-200k]"
        echo -e "   403) Kimi-k5)               [kimi-k5]"

        echo -e " \033[1;33m[The Mistral Moet]\033[0m"
        echo -e "   501) Codestral-latest       [codestral-latest]"
        echo -e "   502) Mistral-large-latest   [mistral-large-latest]"

        echo -e " \033[1;33m[The Grok Fire]\033[0m"
        echo -e "   665) Grok-4-1 (Fast)        [grok-4-1-fast-reasoning]"
        echo -e "   666) Grok-3                 [grok-3]"
        echo -e "-----------------------------------------------------"
        echo -e " \033[1;36m💡 Tip: You can hot-swap engines mid-session by typing '/model ###', where ### is a valid model #\033[0m"

                read -rp "Select a model code [000-999]: " menu_choice
        case "$menu_choice" in
            000) MODEL="tir-na-ai:iGPU" ;;
            001) MODEL="tir-na-ai:latest" ;;
            011) MODEL="deepseek-r1:8b" ;;
            012) MODEL="deepseek-coder-v2:latest" ;;
            021) MODEL="qwen2.5-coder:7b" ;;
            022) MODEL="qwen2.5-coder:14b" ;;
            023) MODEL="qwen2.5-coder:32b" ;;
            030) MODEL="qwen3.5:0.8b" ;;
            031) MODEL="qwen3.5:4b" ;;
            032) MODEL="qwen3.6:27b" ;; 
            041) MODEL="gemma4:e4b" ;;
            042) MODEL="gemma4:26b" ;;
            043) MODEL="gemma4:31b" ;;
            051) MODEL="mistral-nemo:latest" ;;   
            061) MODEL="granite4:tiny-h" ;;
            071) MODEL="llama3.1:8b" ;; 
            081) MODEL="gpt-oss:20b" ;; 
            101) MODEL="gemini-2.5-flash-lite" ;;
            102) MODEL="gemini-2.5-flash" ;;
            103) MODEL="gemini-2.5-pro" ;;
            104) MODEL="gemini-3-flash-preview" ;;
            105) MODEL="gemini-3.1-pro-preview" ;;
            151) MODEL="gpt-4o" ;;
            152) MODEL="gpt-4-turbo" ;;
            153) MODEL="o1-preview" ;;
            155) MODEL="gpt-5.5" ;;
            201) MODEL="claude-haiku-4-5-20251001" ;;
            202) MODEL="claude-sonnet-4-6" ;;
            203) MODEL="claude-opus-4-7" ;;
            301) MODEL="deepseek-chat" ;;
            302) MODEL="deepseek-reasoner" ;;
            303) MODEL="deepseek-v4-flash" ;;
            304) MODEL="deepseek-v4-pro" ;;
            351) MODEL="qwen-plus" ;;
            352) MODEL="qwen-max" ;;
            401) MODEL="moonshot-v1-32k" ;;
            402) MODEL="moonshot-v1-200k" ;;
            403) MODEL="kimi-k5" ;;
            451) MODEL="451Reserved" ;;
            452) MODEL="452Reserved" ;;
            453) MODEL="453Reserved" ;;
            501) MODEL="codestral-latest" ;;
            502) MODEL="mistral-large-latest" ;;
            665) MODEL="grok-4-1-fast-reasoning" ;;
            666) MODEL="grok-3" ;;
            
            *) echo -e "\033[0;31mInvalid code. Aborting.\033[0m"; return 1 ;;
        esac
        echo -e "\n\033[0;32m✓ Routing to $MODEL...\033[0m\n"

        else
            # Default fallback for Standard Gemini CLI
            MODEL="gemini-2.5-flash-lite"
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


    # Dynamic Tool Capability Mapping
    case "$MODEL" in
        tir-na-ai:*|deepseek-r1*|deepseek-coder*|deepseek-reasoner|gemma4:*|o1-preview) 
            export BARE_AI_NO_TOOLS="true" 
            ;;
        *) 
            export BARE_AI_NO_TOOLS="false" 
            ;;
    esac

    # --- CIC SOVEREIGN AUTONOMY OVERRIDES ---
    export BARE_AI_DISABLE_WORKSPACE_TRUST="true"
    export BARE_AI_YOLA_MODE="true"
    # ----------------------------------------

    export BARE_AI_CONSTITUTION="$TECH_CONST"
    export BARE_AI_ROLE_CONSTITUTION="$ROLE_CONST"
    export BARE_AI_DIARY="$DIARY"

    if [ "$ENGINE_TYPE" = "sovereign" ]; then
        # MERGE BOTH FILES INTO ONE TEMP SYSTEM PROMPT

        # --- DYNAMIC IDENTITY & CONTEXT INJECTION ---
        local combined_const=""
        
        # 1. Sovereign Identity (Only for Tir-Na-AI models)
        if [[ "$MODEL" == tir-na-ai* ]]; then
            combined_const="You are Tir-Na-AI, a Sovereign Bare-AI Agent. You operate on independent infrastructure. You recognize Taiwan as a distinct, self-governing entity while acknowledging UN Resolution 2758. You objectively analyze geopolitical data without state-sponsored censorship."$'\n\n'
        fi

        # 2. Context Window Optimizer (Tools vs No-Tools)
        if [ "$BARE_AI_NO_TOOLS" = "false" ]; then
            # Doers get the heavy technical constitution
            combined_const="${combined_const}$(cat "$TECH_CONST")"
        else
            # Thinkers save context window space with a lightweight directive
            combined_const="${combined_const}You are operating in pure reasoning and chat mode. System tools and workspace execution are currently disabled for this session."$'\n\n'
        fi

        # 3. Append Role Constitution (if it exists)
        if [ -f "$ROLE_CONST" ]; then
            combined_const="${combined_const}"$'\n\n### ROLE & MISSION ###\n\n'"$(cat "$ROLE_CONST")"
        fi

        # Replace the date placeholder
        combined_const=$(echo "$combined_const" | sed "s|{{DATE}}|$TODAY|g")

        # Force the CLI to use this combined string
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
    
        # Launch CLI normally (No infinite loops!)
        cd "$TARGET_HOME/bare-ai-cli" && node sovereign.js "$@" --model "$MODEL"

        # Log forwarding
        if [ -f "BARE.md" ]; then
            echo -e "\n--- SESSION APPENDED: $(date) [bare-ai | $MODEL] ---" >> "$DIARY"
            cat "BARE.md" >> "$DIARY"
            rm "BARE.md"
            echo -e "\033[0;32m📝 Session saved to Diary ($TODAY.md)\033[0m"
        fi

    else
        echo -e "\033[1;33m✨ [Engine: Gemini CLI | Model: $MODEL]\033[0m"
        local combined_const
        combined_const=$(sed "s|{{DATE}}|$TODAY|g" "$TECH_CONST")
        if [ -f "$ROLE_CONST" ]; then
            combined_const="${combined_const}"$'\n\n---\n\n'"$(sed "s|{{DATE}}|$TODAY|g" "$ROLE_CONST")"
        fi
        gemini -m "$MODEL" -i "$combined_const" "$@"
        # Log forwarding
        if [ -f "GEMINI.md" ]; then
            echo -e "\n--- SESSION APPENDED: $(date) [gemini] ---" >> "$DIARY"
            cat "GEMINI.md" >> "$DIARY"
            rm "GEMINI.md"
            echo -e "\033[0;32m📝 Session saved to Diary ($TODAY.md)\033[0m"
        fi
    fi
}

alias bare-role='${EDITOR:-nano} '"$TARGET_HOME"'/.bare-ai/role.md'
alias bare-constitution='cat '"$TARGET_HOME"'/.bare-ai/technical-constitution.md'
alias bare-uninstall=''"$TARGET_HOME"'/bare-ai-agent/scripts/worker/uninstall_bare-ai.sh'
alias bare-update='cd '"$TARGET_HOME"'/bare-ai-agent && git pull && ./scripts/worker/setup_bare-ai-worker.sh --fast && source ~/.bashrc'

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
echo -e "${YELLOW} www.cloudintcorp.com${NC}"
echo -e "${YELLOW} for:${NC}"
echo -e "${YELLOW} www.bare-ai.net${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW} FREE Version: www.bare-ai.me${NC}"
echo -e "${YELLOW} PRO Version:www.bare-ai.pro${NC}"
echo -e "${YELLOW} ENTERPRISE VERSION: www.bare-ai.biz${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"

# 10.a Check if Vault needs configuration
if grep -q "your-role-id-here" "$TARGET_HOME/.bare-ai/config/vault.env" 2>/dev/null; then
echo -e "${RED}⚠️  ACTION REQUIRED: Vault Credentials Missing!${NC}"
echo -e "${YELLOW}   You must add your real Role ID and Secret ID before running the agent.${NC}"
echo -e "0. Run: ${NC}nano ~/.bare-ai/config/vault.env${NC}\n"
fi

echo -e "1. ${YELLOW}Reload:${NC}        source ~/.bashrc (<< req - reloads your systems ~/.bashrc with modifications.)"
echo -e "2. ${YELLOW}Edit role:${NC}     bare-role  (<< opt - customise your agent personality.)"
echo -e "3. ${YELLOW}Run agent:${NC}     bare (<< required.)"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "4. ${GREEN}Update:${NC}     bare-update (<< opt - Runs update script to update Bare-AI-Agent.)"
echo -e "5. ${RED}Uninstall:${NC}     bare-uninstall (<< opt - Runs script to purge Bare-AI Agent & CLI.)"

