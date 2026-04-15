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
# DATE:           2026-04-14
# VERSION:        5.3.0 (Sovereign Autonomy Edition)
#
# CHANGELOG (5.2.0 -> 5.3.0):
# - fix(git): Reordered engine clone before toolkit deployment to prevent conflicts.
# - fix(bash): Relocated BARE_NECESSITIES_DIR to fix strict 'set -u' unbound error.
# - feat(auth): Injected BARE_AI_YOLA_MODE and DISABLE_WORKSPACE_TRUST into bare().
# - fix(perms): Corrected find/chmod logic with \( -o \) for global execution.
# - perf(llm): Restored .sh/.py extensions to symlinks to optimize AI token usage.
# - ux(cli): Refined post-install terminal instructions for user clarity.
# ==============================================================================
set -euo pipefail

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
CONFIG_FILE="$BARE_AI_DIR/config/agent.env" # <--- FIXED: Now points to a file inside the config dir
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

    # 9. Seed Default Models (Prep for tomorrow)
    echo -e "${YELLOW}Seeding default model endpoints...${NC}"
    vault kv put secret/gemma4:31b/config base_url="http://127.0.0.1:11434" model_name="gemma4:31b" api_key="local" > /dev/null
    vault kv put secret/tir-na-ai-fast/config base_url="http://127.0.0.1:11434" model_name="tir-na-ai-fast:latest" api_key="local" > /dev/null
    vault kv put secret/gemma4:e4b/config base_url="http://127.0.0.1:11434" model_name="gemma4:e4b" api_key="local" > /dev/null
    vault kv put secret/granite4:tiny-h/config base_url="http://127.0.0.1:11434" model_name="granite4:tiny-h" api_key="local" > /dev/null
    vault kv put secret/deepseek-r1:8b/config base_url="http://127.0.0.1:11434" model_name="deepseek-r1:8b" api_key="local" > /dev/null

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
# NOTE: Vault secret paths are defined within .bashrc via the 'bare()' function.
# ARCHITECTURE RULE: A 1:1 mapping must exist between a Model Alias and its 
# corresponding Vault Secret Path/Role to ensure security isolation.
#
# CURRENT FLEET CONFIGURATION:
# ------------------------------------------------------------------------------
# 1. bare energy  : Underpinned by DeepSeek R1 (8B). Optimized via 'tir-na-ai' 
#                   utilizing iCPU Vulkan acceleration for cross-system parity.
# 2. bare granite : Dedicated IBM Granite optimized path.
# 3. bare gemma4  : High-performance Google Gemma 4 (31B) implementation.
# 4. bare loco    : Standardized local-first optimization routine.
#
# EXTENSIBILITY:
# To integrate new models, append a case to the bare() loader.
# REQUIRED: Ensure 'bare <new-model>' maps to a unique Vault secret path/role.
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

#####################################################
#####################################################
#####################################################

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

#####################################################
#####################################################
#####################################################

# --- 4 BARE-NECESSITIES TOOLKIT DEPLOYMENT ---
echo -e "${YELLOW}Deploying bare-necessities toolset to CLI workspace jail...${NC}"
CLI_SCRIPTS_DIR="$HOME/bare-ai-cli/my-bare-scripts"

# 1. Create the internal jail-compliant folder
execute_command "mkdir -p \"$CLI_SCRIPTS_DIR\"" "Create CLI script jail"

if [ -d "$BARE_NECESSITIES_DIR" ]; then
    # 2. Sync toolkit to the jail
    echo -e "${YELLOW}Syncing toolkit to $CLI_SCRIPTS_DIR...${NC}"
    execute_command "cp -r \"$BARE_NECESSITIES_DIR/\"* \"$CLI_SCRIPTS_DIR/\"" "Copy tools into jail"


    echo -e "${YELLOW}Setting executable permissions in jail...${NC}"
    execute_command "find \"$CLI_SCRIPTS_DIR\" -type f \\( -name \"*.sh\" -o -name \"*.py\" \\) -exec chmod +x {} +" "Make jail scripts executable"

    echo -e "${YELLOW}Creating global symlinks in /usr/local/bin pointing to jail...${NC}"
    
    # 3. Create Symlinks pointing to the JAILED versions WITH EXTENSIONS
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

# --- 5. AGENT CONFIG ---
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

# --- 6. CONSTITUTIONS ---
# technical-constitution.md — base Linux rules, managed by bare-ai-agent, read-only
# role.md                   — node personality, user-owned, never overwritten

echo -e "${YELLOW}Deploying technical constitution...${NC}"
TECH_CONST_SRC="$TEMPLATES_DIR/technical-constitution.md"
TECH_CONST_DEST="$BARE_AI_DIR/technical-constitution.md"

if [ -f "$TECH_CONST_SRC" ]; then
    # Always overwrite technical constitution — it is managed by the repo
    # Unlock first in case a previous install set it read-only
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
        echo -e "${YELLOW}⚠️  Role starter template not found — creating blank role.md${NC}"
        echo "# BARE-AI ROLE CONSTITUTION
# Edit this file to define this agent's role and personality." > "$ROLE_CONST"
    fi
else
    echo -e "${GREEN}✓ Role constitution already exists — not overwritten${NC}"
fi

# ALWAYS create/refresh the visible symlink in the clone directory
ln -sf "$ROLE_CONST" "$REPO_DIR/role.md"
echo -e "${GREEN}✓ Created visible role.md link in agent directory${NC}"

#####################################################
#####################################################
#####################################################

# --- 7. README ---
echo -e "${YELLOW}Writing README.md...${NC}"
cat << 'README_EOF' > "$BARE_AI_DIR/README.md"
# BARE-AI Setup and Configuration

This directory stores the persistent configuration and memory for the BARE-AI agent.

## Directory Structure
- **technical-constitution.md** — Core Linux tool rules (read-only, managed by bare-ai-agent)
- **role.md** — Agent personality and mission (edit freely, never overwritten)
- **diary/** — Daily activity logs
- **logs/** — JSON telemetry per command execution
- **bin/** — Local artifacts (bare-summarize, etc.)
- **config/agent.env** — Agent config (AGENT_ID, ENGINE_TYPE)
- **config/vault.env** — Vault credentials 

## Customising Your Agent
Edit ~/.bare-ai/role.md to define this agent's personality, mission, and domain rules.
The technical-constitution.md is managed by the repo — do not edit it directly.

## Engine Selection
Two engines are supported:
- **Bare-AI-CLI** — Sovereign, local-first, Vault-integrated
- **Gemini-CLI** — Standard Google Cloud SDK

## Gemini Setup (if using Gemini engine)
1. Install: `npm install -g @google/gemini-cli`
2. API Key:  add `export GEMINI_API_KEY="YOUR_KEY"` to `~/.bashrc`
README_EOF
echo -e "${GREEN}✓ README written${NC}"

#####################################################
#####################################################
#####################################################

# --- 8. TELEMETRY PING ---
# FIX: Use full https:// URL and suppress errors so set -e is not tripped on network issues
TELEMETRY_URL="https://www.bare-erp.com"
echo -e "${YELLOW}Pinging telemetry endpoint...${NC}"
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$TELEMETRY_URL" || echo "000")
echo -e "${GREEN}✓ Telemetry ping: HTTP $HTTP_CODE${NC}"

#####################################################
#####################################################
#####################################################

# --- 9. BASHRC UPDATES ---
BASHRC_FILE="$HOME/.bashrc"
echo -e "${YELLOW}Updating $BASHRC_FILE...${NC}"

#####################################################
#####################################################
#####################################################


# 9a. PATH entry
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

cat << 'BARE_FUNC_EOF' >> "$BASHRC_FILE"

# BARE-AI Hybrid Loader
bare() {
    local MODEL="$1"
    local TODAY=$(date +%Y-%m-%d)
    local TECH_CONST="$HOME/.bare-ai/technical-constitution.md"
    local ROLE_CONST="$HOME/.bare-ai/role.md"
    local DIARY="$HOME/.bare-ai/diary/$TODAY.md"
    local CONFIG="$HOME/.bare-ai/config/agent.env"
    local VAULT_ENV="$HOME/.bare-ai/config/vault.env"

    # --- INTERACTIVE MODEL MENU ---
    if [ -z "$MODEL" ]; then
        echo -e "\n\033[1;36m===================================================\033[0m"
        echo -e "\033[1;36m🤖 BARE-AI Sovereign Engine Selection\033[0m"
        echo -e "\033[1;36m===================================================\033[0m"
        echo -e " \033[1;33m[The Thinkers - Reasoning & Chat]\033[0m"
        echo -e "   1) DeepSeek R1 (8B)        [deepseek-r1:8b]"
        echo -e "   2) Tir-Na-AI (8B)          [tir-na-ai:latest]"
        echo -e "   3) Gemma 4 (E4B Edge)      [gemma4:e4b]"
        echo -e "   4) Gemma 4 (26B MOE)       [gemma4:26b]"
        echo -e "   5) Gemma 4 (31B Heavy)     [gemma4:31b]"
        echo -e "\n \033[1;33m[The Doers - Tool Execution & Code]\033[0m"
        echo -e "   6) Granite 4 (Tiny)        [granite4:tiny-h]"
        echo -e "   7) Qwen 2.5 Coder (32B)    [qwen2.5-coder:32b]"
        echo -e "   8) DeepSeek Coder V2       [deepseek-coder-v2:latest]"
        echo -e "\n \033[1;33m[The Edge - iGPU Accelerated]\033[0m"
        echo -e "   9) Tir-Na-AI iGPU          [tir-na-ai:iGPU]"
        echo -e "---------------------------------------------------"
        
        read -rp "Select a model [1-9]: " menu_choice
        case "$menu_choice" in
            1) MODEL="deepseek-r1:8b" ;;
            2) MODEL="tir-na-ai:latest" ;;
            3) MODEL="gemma4:e4b" ;;
            4) MODEL="gemma4:26b" ;;
            5) MODEL="gemma4:31b" ;;
            6) MODEL="granite4:tiny-h" ;;
            7) MODEL="qwen2.5-coder:32b" ;;
            8) MODEL="deepseek-coder-v2:latest" ;;
            9) MODEL="tir-na-ai:iGPU" ;;
            *) echo -e "\033[0;31mInvalid selection. Aborting.\033[0m"; return 1 ;;
        esac
        echo -e "\n\033[0;32m✓ Routing to $MODEL...\033[0m\n"
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

    # Load engine type from config
    local ENGINE_TYPE="cloud"
    if [ -f "$CONFIG" ]; then
        source "$CONFIG"
    fi
    
    # Sovereign model/vault routing
    case "$MODEL" in
        tir-na-ai:latest)           export VAULT_SECRET_PATH="secret/data/tir-na-ai:latest/config"; export BARE_AI_NO_TOOLS="true"  ;;
        gemma4:31b)               export VAULT_SECRET_PATH="secret/data/gemma4:31b/config";     export BARE_AI_NO_TOOLS="false" ;;
        gemma4:26b)               export VAULT_SECRET_PATH="secret/data/gemma4:26b/config";     export BARE_AI_NO_TOOLS="false" ;;
        gemma4:e4b)               export VAULT_SECRET_PATH="secret/data/gemma4:e4b/config";     export BARE_AI_NO_TOOLS="false" ;;
        granite4:tiny-h)          export VAULT_SECRET_PATH="secret/data/granite4:tiny-h/config";export BARE_AI_NO_TOOLS="false" ;;
        qwen2.5-coder:32b)        export VAULT_SECRET_PATH="secret/data/qwen2.5-coder:32b/config"; export BARE_AI_NO_TOOLS="false" ;;
        deepseek-r1:8b)           export VAULT_SECRET_PATH="secret/data/deepseek-r1:8b/config"; export BARE_AI_NO_TOOLS="true"  ;;
        deepseek-coder-v2:latest) export VAULT_SECRET_PATH="secret/data/deepseek-coder-v2:latest/config"; export BARE_AI_NO_TOOLS="true" ;;
        tir-na-ai:iGPU)           export VAULT_SECRET_PATH="secret/data/tir-na-ai:iGPU/config"; export BARE_AI_NO_TOOLS="true" ;;
        *)                        export VAULT_SECRET_PATH="secret/data/${MODEL}/config";       export BARE_AI_NO_TOOLS="true" ;;
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

        echo -e "\033[0;32m🤖 [Engine: Bare-AI CLI | Model: $MODEL]\033[0m"
                
        cd "$HOME/bare-ai-cli" && node sovereign.js "$@"
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
        gemini -m gemini-2.5-flash-lite -i "$combined_const" "$@"
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
alias bare-uninstall='~/bare-ai-agent/scripts/worker/uninstall_bare-ai.sh'

# END: BARE-AI-AGENT WORKER BASHRC MODIFICATIONS:
BARE_FUNC_EOF

#####################################################
#####################################################
#####################################################

# --- 10. COMPLETE ---
echo -e "\n${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ BARE-AI-AGENT WORKER SETUP COMPLETE${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"

# 10.a Check if Vault needs configuration
if grep -q "your-role-id-here" "$HOME/.bare-ai/config/vault.env" 2>/dev/null; then
echo -e "${RED}⚠️  ACTION REQUIRED: Vault Credentials Missing!${NC}"
echo -e "${YELLOW}   You must add your real Role ID and Secret ID before running the agent.${NC}"
echo -e "0. Run: ${NC}nano ~/.bare-ai/config/vault.env${NC}\n"
fi

echo -e "1. ${YELLOW}Reload:${NC}        source ~/.bashrc (<< req - reloads your systems ~/.bashrc with modifications.)"
echo -e "2. ${YELLOW}Test artifact:${NC} bare-summarize (<< opt - used in fleet management only in conjunction with bare brain.)"
echo -e "3. ${YELLOW}Edit role:${NC}     bare-role  (<< opt - customise your agent personality.)"
echo -e "4. ${YELLOW}Run agent:${NC}     bare (<< req - or bare energy or bare loco or bare granite or bare gemma4 etc.)"
echo -e "5. ${GREEN}Architecture:${NC}  $ENGINE_TYPE backend loaded (<< Info only.)"
echo -e "6. ${RED}Uninstall:${NC}      bare-uninstall (<< opt - Runs script to purge agent/cli.)"

# Set up 1-minute thermal heartbeat
echo "Setting up thermal monitoring heartbeat..."
(crontab -l 2>/dev/null | grep -v "bare-thermal-guard"; echo "* * * * * /usr/local/bin/bare-thermal-guard") | crontab -