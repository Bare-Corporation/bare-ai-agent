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
# DATE:           2026-04-18
# VERSION:        5.5.1 (Brain-Coupled Edition)
#
# -  v5.5.1 (Brain-Coupled Edition)
# - refactor(telemetry): Removed bare-summarize. Telemetry is now handled natively by the Sovereign Brain.
# -  v5.5.0 (Sovereign Switchboard Edition)
# - feat(menu): Expanded Sovereign Menu to support Premium Cloud multi-tenant routing.
# - fix(routing): Added strict conditional menu rendering to prevent Gemini-CLI crashes.
# -  v5.4.0 (Sovereign Autonomy Edition)
# - feat(core): Implemented `--fast` flag in worker setup to bypass NPM builds.
# - feat(identity): Unified system prompt injection via concatenating constitutions.
# - fix(vault): Corrected syntax error and IP formatting for Tir-Na-AI iGPU.
# ============================================================================== 
set -euo pipefail

# --- FAST UPDATE CHECK ---
FAST_UPDATE=false
if [[ "${1:-}" == "--fast" ]]; then
    FAST_UPDATE=true
    echo -e "\033[1;33mFAST MODE: Skipping engine rebuild. Updating config & Menu only...\033[0m"
fi

# --- DOCKER / Podman WARNING ---
if [ ! -f "/.dockerenv" ]; then
    echo -e "\033[1;33mWarning: Running on host system. For enhanced security, Bare-ERP recommends running within Docker or Podman.\033[0m"
fi

# --- COLORS ---
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

echo -e "${GREEN}Starting BARE-AI setup...${NC}"

# --- ENGINE SELECTION ---
echo -e "\nSelect your AI Engine:"
echo "1) Bare-AI-CLI (Sovereign, Local-First, Vault-Integrated)"
echo "2) Gemini-CLI (Standard Google Cloud SDK)"
read -rp "Enter choice [1 or 2]: " ENGINE_CHOICE

# i. Define the directory and the actual file
CONFIG_DIR="$HOME/.bare-ai/config"
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

# --- DIRECTORY DEFINITIONS ---
WORKSPACE_DIR="$HOME/.bare-ai"
BARE_AI_DIR="$WORKSPACE_DIR"
BIN_DIR="$BARE_AI_DIR/bin"
LOG_DIR="$BARE_AI_DIR/logs"
DIARY_DIR="$BARE_AI_DIR/diary"
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
BARE_NECESSITIES_DIR="$REPO_DIR/scripts/bare-necessities"

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
VAULT_ENV_FILE="$HOME/.bare-ai/config/vault.env"
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
    
    # 1. Install Vault and jq (needed for JSON parsing)
    execute_command "wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor --yes -o /usr/share/keyrings/hashicorp-archive-keyring.gpg" "Add HashiCorp GPG key"
    execute_command "echo \"deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com \$(lsb_release -cs) main\" | sudo tee /etc/apt/sources.list.d/hashicorp.list" "Add HashiCorp Repo"
    execute_command "sudo apt-get update -qq && sudo apt-get install -y -qq vault jq" "Install Vault & jq"

    # 2. Configure Persistent File Storage (Survives Reboot)
    sudo tee /etc/vault.d/vault.hcl > /dev/null <<EOF
storage "file" { path = "/opt/vault/data" }
listener "tcp" { address = "127.0.0.1:8200"; tls_disable = 1 }
api_addr = "http://127.0.0.1:8200"
ui = true
EOF
    sudo mkdir -p /opt/vault/data
    sudo chown -R vault:vault /opt/vault/data /etc/vault.d

    # 3. Start the system service
    execute_command "sudo systemctl enable vault && sudo systemctl restart vault" "Start Vault Service"
    sleep 3 # Wait for boot

    export VAULT_ADDR="http://127.0.0.1:8200"

    # 4. Initialize Vault
    echo -e "${YELLOW}Initializing Vault & generating keys...${NC}"
    INIT_OUT=$(vault operator init -key-shares=1 -key-threshold=1 -format=json)
    UNSEAL_KEY=$(echo "$INIT_OUT" | jq -r .unseal_keys_b64[0])
    ROOT_TOKEN=$(echo "$INIT_OUT" | jq -r .root_token)

    # Save keys for the user
    echo "Root Token: $ROOT_TOKEN" > "$HOME/.bare-ai/config/vault-recovery-keys.txt"
    echo "Unseal Key: $UNSEAL_KEY" >> "$HOME/.bare-ai/config/vault-recovery-keys.txt"
    chmod 600 "$HOME/.bare-ai/config/vault-recovery-keys.txt"

    # 5. Unseal and Login
    vault operator unseal "$UNSEAL_KEY" > /dev/null
    export VAULT_TOKEN="$ROOT_TOKEN"

    # 6. Enable Engines and Auth
    vault secrets enable -version=2 -path=secret kv > /dev/null 2>&1 || true
    vault auth enable approle > /dev/null 2>&1 || true

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
    vault kv put secret/gemma4:31b/config base_url="http://127.0.0.1:11434" model_name="gemma4:31b" api_key="local" > /dev/null
    vault kv put secret/tir-na-ai-fast/config base_url="http://127.0.0.1:11434" model_name="tir-na-ai-fast:latest" api_key="local" > /dev/null
    vault kv put secret/gemma4:e4b/config base_url="http://127.0.0.1:11434" model_name="gemma4:e4b" api_key="local" > /dev/null
    vault kv put secret/granite4:tiny-h/config base_url="http://127.0.0.1:11434" model_name="granite4:tiny-h" api_key="local" > /dev/null
    vault kv put secret/deepseek-r1:8b/config base_url="http://127.0.0.1:11434" model_name="deepseek-r1:8b" api_key="local" > /dev/null

    vault kv put secret/gemini-2.5-flash-lite/config base_url="http://127.0.0.1:11434" model_name="gemini-2.5-flash-lite" api_key="local" > /dev/null
    vault kv put secret/gemini-2.5-flash/config base_url="http://127.0.0.1:11434" model_name="gemini-2.5-flash" api_key="local" > /dev/null
    vault kv put secret/gemini-2.5-pro/config base_url="http://127.0.0.1:11434" model_name="gemini-2.5-pro" api_key="local" > /dev/null
    vault kv put secret/gemini-3-flash-preview/config base_url="http://127.0.0.1:11434" model_name="gemini-3-flash-preview" api_key="local" > /dev/null
    vault kv put secret/gemini-3.1-pro-preview/config base_url="http://127.0.0.1:11434" model_name="gemini-3.1-pro-preview" api_key="local" > /dev/null
    vault kv put secret/gpt-4o/config base_url="http://127.0.0.1:11434" model_name="gpt-4o" api_key="local" > /dev/null
    vault kv put secret/gpt-4-turbo/config base_url="http://127.0.0.1:11434" model_name="gpt-4-turbo" api_key="local" > /dev/null
    vault kv put secret/o1-preview/config base_url="http://127.0.0.1:11434" model_name="o1-preview" api_key="local" > /dev/null
    
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
        
        # Check if Docker is installed, if not, install it
        if ! command -v docker &>/dev/null; then
            echo -e "${YELLOW}Docker not found. Installing Docker engine...${NC}"
            execute_command "curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh" "Install Docker"
            sudo usermod -aG docker "$USER" || true
            rm -f get-docker.sh
        fi
        
        # Clean up any old container and spin up a fresh SearXNG
        sudo docker rm -f searxng &>/dev/null || true
        execute_command "sudo docker run -d --name searxng -p 8080:8080 -v searxng-data:/etc/searxng --restart unless-stopped searxng/searxng" "Start SearXNG Container"
        
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
else
    echo -e "${GREEN}✓ Skipping engine build (Fast Update active)${NC}"
fi

#####################################################
#####################################################
#####################################################

# --- 3. BARE-NECESSITIES TOOLKIT DEPLOYMENT ---
echo -e "${YELLOW}Deploying bare-necessities toolset to CLI workspace jail...${NC}"
CLI_SCRIPTS_DIR="$HOME/bare-ai-cli/my-bare-scripts"

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

# --- 4b. AGENT AUTONOMY PERMISSIONS (Sudoers Patch) ---
# Allow the agent to self-heal (apt/systemctl) without hanging on password prompts.
echo -e "${YELLOW}Granting limited NOPASSWD sudo rights for self-healing...${NC}"

# We use a dedicated file in /etc/sudoers.d/ to keep it clean.
sudo tee /etc/sudoers.d/bare-ai-autonomy > /dev/null <<EOF
# BARE-AI Autonomy Permissions
$USER ALL=(ALL) NOPASSWD: /usr/bin/apt-get, /usr/bin/apt, /usr/bin/systemctl, /usr/bin/docker
EOF

sudo chmod 0440 /etc/sudoers.d/bare-ai-autonomy
echo -e "${GREEN}✓ Sudoers patch applied.${NC}"

#####################################################
#####################################################
#####################################################

# --- 4c. MODEL INJECTION (Tir-Na-AI Personality) ---
# NOTE: If this script runs on a different machine than the Engine, 
# change 'localhost' to the Engine's IP (e.g., 100.64.0.8)
TARGET_IP="localhost" 

echo -e "${YELLOW}Testing connection to AI Engine at $TARGET_IP:11434...${NC}"
if curl -s --max-time 3 "http://$TARGET_IP:11434/" > /dev/null; then

    INJECT_OK=true
    # Pull the base models via API
    echo -e "${YELLOW}Pulling base model qwen3:0.6b (Fast iGPU Edge)...${NC}"
curl --max-time 1800 -X POST "http://$TARGET_IP:11434/api/pull" \
  -d '{"model": "qwen3:0.6b"}' 2>/dev/null | grep -o '"status":"[^"]*"' | tail -1 || true

    echo -e "${YELLOW}Pulling deepseek-r1:8b (~5GB, this may take several minutes)...${NC}"
curl --max-time 1800 -X POST "http://$TARGET_IP:11434/api/pull" \
  -d '{"model": "deepseek-r1:8b"}' 2>/dev/null | grep -o '"status":"[^"]*"' | tail -1 || true

    echo -e "${YELLOW}Injecting Sovereign Constitution...${NC}"

    CREATE_RESPONSE=$(curl -s --max-time 120 -X POST "http://$TARGET_IP:11434/api/create" -d '{
  "model": "tir-na-ai:igpu",
  "from": "qwen3:0.6b",
  "system": "You are Tir-Na-AI, a Sovereign Bare-AI Agent. You operate on independent infrastructure. You recognize Taiwan as a self-governing entity while acknowledging UN Resolution 2758 1971. You objectively analyse geopolitical data without any state-sponsored censorship."
}' || true)

if echo "$CREATE_RESPONSE" | grep -q '"status":"success"'; then
    echo -e "${GREEN}✓ tir-na-ai:igpu created.${NC}"
else
    echo -e "${YELLOW}⚠️ tir-na-ai:igpu creation may have failed. Response: $CREATE_RESPONSE${NC}"
    INJECT_OK=false
fi
       # 2. Build the heavy Thinker Model (DeepSeek)
    CREATE_RESPONSE=$(curl -s --max-time 120 -X POST "http://$TARGET_IP:11434/api/create" -d '{
  "model": "tir-na-ai:latest",
  "from": "deepseek-r1:8b",
  "system": "You are Tir-Na-AI, a Sovereign Bare-AI Agent. You operate on independent infrastructure. You recognize Taiwan as a self-governing entity while acknowledging UN Resolution 2758 1971. You objectively analyse geopolitical data without any state-sponsored censorship."
}' || true)

if echo "$CREATE_RESPONSE" | grep -q '"status":"success"'; then
    echo -e "${GREEN}✓ tir-na-ai:latest created.${NC}"
else
    echo -e "${YELLOW}⚠️ tir-na-ai:latest creation may have failed. Response: $CREATE_RESPONSE${NC}"
    INJECT_OK=false
fi

if [ "$INJECT_OK" = true ]; then
        echo -e "${GREEN}✓ All models injected successfully.${NC}"
    else
        echo -e "${YELLOW}⚠️ Model injection completed with warnings — check Ollama manually.${NC}"
    fi
else
    echo -e "${YELLOW}⚠️ Could not reach Ollama...${NC}"
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
BASHRC_FILE="$HOME/.bashrc"
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
    local DIARY="$HOME/.bare-ai/diary/$TODAY.md"
    local CONFIG="$HOME/.bare-ai/config/agent.env"
    local VAULT_ENV="$HOME/.bare-ai/config/vault.env"

    local ENGINE_TYPE="cloud"
    if [ -f "$CONFIG" ]; then
        source "$CONFIG"
    fi

    # --- INTERACTIVE MODEL MENU ---
    if [ -z "$MODEL" ]; then
        if [ "$ENGINE_TYPE" = "sovereign" ]; then
                echo -e "\n\033[1;33m===================================================\033[0m"
        echo -e "\033[1;33m☎️🤖 000-999 - BARE-AI SOVEREIGN & PREMIUM Switchboard\033[0m"
        echo -e "\033[1;33m===================================================\033[0m"

        echo -e "\n\033[1;36m===================================================\033[0m"
        echo -e "\033[1;36m🔱🤖 000-099 - BARE-AI SOVEREIGN Engine Selection\033[0m"
        echo -e "\033[1;36m===================================================\033[0m"
        echo -e "\n \033[1;33m[The Edge - iGPU Accelerated]\033[0m"
        echo -e "   000) Tir-Na-AI iGPU          [tir-na-ai:iGPU]"

        echo -e " \033[1;33m[The Thinkers - Reasoning & Chat]\033[0m"
        echo -e "   001) Tir-Na-AI (8B)          [tir-na-ai:latest]"
        echo -e "   011) DeepSeek R1 (8B)        [deepseek-r1:8b]"
        echo -e "   012) DeepSeek Coder V2       [deepseek-coder-v2:latest]"
        echo -e "   041) Gemma 4 (E4B Edge)      [gemma4:e4b]"
        echo -e "   042) Gemma 4 (26B MOE)       [gemma4:26b]"
        echo -e "   043) Gemma 4 (31B Heavy)     [gemma4:31b]"

        echo -e "\n \033[1;33m[The Doers - Tool Execution & Code]\033[0m"      
        echo -e "   021) Qwen 2.5 Coder (7B)     [qwen2.5-coder:7b]"
        echo -e "   022) Qwen 2.5 Coder (14B)    [qwen2.5-coder:14b]"
        echo -e "   023) Qwen 2.5 Coder (32B)    [qwen2.5-coder:32b]"
        echo -e "   031) llama3.1 (8B)           [llama3.1:8b]"
        echo -e "   051) mistral-nemo (7B)       [mistral-nemo:latest]"
        echo -e "   061) Granite 4 (Tiny)        [granite4:tiny-h]"

        echo -e "---------------------------------------------------"

        echo -e "\n\033[1;35m===================================================\033[0m"
        echo -e "\033[1;35m⭐🤖 101-999 - BARE-AI PREMIUM Engine Selection\033[0m"
        echo -e "\033[1;35m===================================================\033[0m"
        echo -e " \033[1;33m[The Gemini Constellation]\033[0m"
        echo -e "   101) Gemini 2.5 Flash Lite  [gemini-2.5-flash-lite]"
        echo -e "   102) Gemini 2.5 Flash       [gemini-2.5-flash]"
        echo -e "   103) Gemini 2.5 Pro         [gemini-2.5-pro]"
        echo -e "   104) Gemini 3 Flash (Pre)   [gemini-3-flash-preview]"
        echo -e "   105) Gemini 3.1 Pro (Pre)   [gemini-3.1-pro-preview]"

        echo -e " \033[1;33m[The GPT Nexus]\033[0m"
        echo -e "   201) GPT-4o (Omni)          [gpt-4o]"
        echo -e "   202) GPT-4-Turbo            [gpt-4-turbo]"
        echo -e "   203) o1-preview (Reasoning) [o1-preview]"
        echo -e "---------------------------------------------------"

                read -rp "Select a model code [000-999]: " menu_choice
        case "$menu_choice" in
            000) MODEL="tir-na-ai:iGPU" ;;
            001) MODEL="tir-na-ai:latest" ;;
            011) MODEL="deepseek-r1:8b" ;;
            012) MODEL="deepseek-coder-v2:latest" ;;
            021) MODEL="qwen2.5-coder:7b" ;;
            022) MODEL="qwen2.5-coder:14b" ;;
            023) MODEL="qwen2.5-coder:32b" ;;
            031) MODEL="llama3.1:8b" ;;   
            041) MODEL="gemma4:e4b" ;;
            042) MODEL="gemma4:26b" ;;
            043) MODEL="gemma4:31b" ;;
            051) MODEL="mistral-nemo:latest" ;;   
            061) MODEL="granite4:tiny-h" ;;
            101) MODEL="gemini-2.5-flash-lite" ;;
            102) MODEL="gemini-2.5-flash" ;;
            103) MODEL="gemini-2.5-pro" ;;
            104) MODEL="gemini-3-flash-preview" ;;
            105) MODEL="gemini-3.1-pro-preview" ;;
            201) MODEL="gpt-4o" ;;
            202) MODEL="gpt-4-turbo" ;;
            203) MODEL="o1-preview" ;;
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
        tir-na-ai:*|deepseek-*|gemma4:*|o1-preview) 
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
        local combined_const
        combined_const=$(cat "$TECH_CONST")
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
            if ! curl -s --max-time 1 "$VAULT_ADDR/v1/sys/health" > /dev/null 2>&1 && ! curl -s --max-time 1 "$VAULT_ADDR" > /dev/null 2>&1; then
                echo -e "\033[0;31m❌ CRITICAL: Cannot reach Vault at $VAULT_ADDR. Engine execution aborted to prevent hang.\033[0m"
                return 1
            fi
        fi
   
        # Launch CLI normally (No infinite loops!)
        cd "$HOME/bare-ai-cli" && node sovereign.js "$@" --model "$MODEL"

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

alias bare-role='${EDITOR:-nano} ~/.bare-ai/role.md'
alias bare-constitution='cat ~/.bare-ai/technical-constitution.md'
alias bare-uninstall='~/bare-ai-agent/scripts/worker/uninstall_bare-ai.sh'
alias bare-update='cd ~/bare-ai-agent && git pull && ./scripts/worker/setup_bare-ai-worker.sh --fast && source ~/.bashrc'

# END: BARE-AI-AGENT WORKER BASHRC MODIFICATIONS:
BARE_FUNC_EOF
  echo -e "${GREEN}✓ bare() function added${NC}"
else
    echo -e "${YELLOW}⚠️  bare() function already present, skipping${NC}"
fi

#####################################################
#####################################################
#####################################################

# --- 10. COMPLETE ---
echo -e "\n${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ BARE-AI-AGENT WORKER SETUP COMPLETE${NC}"
echo -e "${YELLOW} A Cloud Integration Corporation Custom Build${NC}"
echo -e "${YELLOW} www.cloudintcorp.com${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"

# 10.a Check if Vault needs configuration
if grep -q "your-role-id-here" "$HOME/.bare-ai/config/vault.env" 2>/dev/null; then
echo -e "${RED}⚠️  ACTION REQUIRED: Vault Credentials Missing!${NC}"
echo -e "${YELLOW}   You must add your real Role ID and Secret ID before running the agent.${NC}"
echo -e "0. Run: ${NC}nano ~/.bare-ai/config/vault.env${NC}\n"
fi

echo -e "1. ${YELLOW}Reload:${NC}        source ~/.bashrc (<< req - reloads your systems ~/.bashrc with modifications.)"
echo -e "2. ${YELLOW}Edit role:${NC}     bare-role  (<< opt - customise your agent personality.)"
echo -e "3. ${YELLOW}Run agent:${NC}     bare (<< req - or bare energy or bare loco or bare granite or bare gemma4 etc.)"
echo -e "4. ${GREEN}Architecture:${NC}  $ENGINE_TYPE backend loaded (<< Info only.)"
echo -e "5. ${RED}Uninstall:${NC}     bare-uninstall (<< opt - Runs script to purge agent/cli.)"

# Set up 1-minute thermal heartbeat
echo "Setting up thermal monitoring heartbeat..."
( (crontab -l 2>/dev/null | grep -v "bare-thermal-guard") || true; echo "* * * * * /usr/local/bin/bare-thermal-guard" ) | crontab - || true