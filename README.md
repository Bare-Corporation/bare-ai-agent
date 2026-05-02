# 🦾 Bare-AI-Agent: Autonomous Infrastructure Management (The Sovereign Mesh)

Bare-AI-Agent is a multi-node, self-healing architecture designed to manage data pipelines and infrastructure integrity across Linux and Windows environments. The system supports **dual AI engines** — choose between the sovereign Bare-AI-CLI or Google's Gemini-CLI.

Bare-ai-agent is a local-first, privacy-hardened autonomous agent framework designed for Debian-based Linux hosts. It bridges the gap between high-performance cloud models and sovereign local infrastructure using HashiCorp Vault for secrets management and SearXNG for private web grounding.

Note: a very early alpha of bare-ai-agent for windows is pre bundled but only works with gemini cli at present. as bare-ai-cli has only been built tested for debian based systems currently. This is not further documented for now but the intention is for it to follow the same design principles as bare-ai-agent / bare-ai-cli for linux.

**Author:** Cian Egan  (CEO & Chief Architect at the Cloud Integration Corporation)
**Created Date:** 2026-02-02
**Main CLI Repository:** [github.com/Bare-Corporation/bare-ai-cli](https://github.com/Bare-Corporation/bare-ai-cli)

---

## 🛠 Features
- **Identity:** Autonomous Linux Agent with Level 4 Autonomy.
- **Security:** Zero-Knowledge local secret injection via Vault AppRole.
- **Intelligence:** Hybrid routing between Google Gemma 4 (31B) and local optimised models (DeepSeek, Granite).
- **Grounding:** Sovereign search via SearXNG (Chinese & Global results).
- **Telemetry:** Real-time hardware audits via the `bare-necessities` toolkit.
- **Self-Healing Data Pipelines:** Autonomous error detection and log sniping.
- **Context Management:** High-speed AST mapping for large codebases.
- **Thermal Awareness:** Real-time system Tctl monitoring and load balancing.
- **AI Diary:** Persistent session memory to prevent hallucination loops.

## 🏛️ System Architecture
This project is designed to mimic the cloud AI terminal Command line interface (CLI) experience (like Google Gemini or Claude Code etc) but entirely on your own hardware.

The AI model(s): 1 centralised High-Performance VM or PC running an LLM (e.g., Ollama/vLLM with Granite 4).

The Worker Hands: Unlimited lightweight VMs running bare-ai-agent and bare-ai-cli.

The Memory: A centralised HashiCorp Vault server to manage endpoints and keys securely across the fleet.

The fleet follows a strict role-based hierarchy to ensure safety and scalability:

### 1. The Workers (Fleet Nodes)
**Hosts:** Any enrolled Linux node  
**Role:** Telemetry reporting and payload execution.  

## 1a. ⚖️ Bare-ai-agent Workers Technical Constitution
The worker agent(s) operates under a read-only Technical Constitution found in ~/.bare-ai/technical-constitution.md. This defines tool-use boundaries, resource limits, and sovereign operational style.

## 1b. ⚖️ Bare-ai-agent Workers Functional Constitution
The worker agent(s) operates under a configurable (end user defined) Functional Constitution found in ~/.bare-ai/role.md. This defines the ai persona, skill set, functional boundaries, examples: You are a developer, doctor, lawyer, CEO, etc, the limits are endless.

### 2. The Brain (Coordinator written in Golang) - seperate premium git hub project for businesses serious about sovereign ai.
The Sovereign Brain is a lightweight, deterministic orchestrator written entirely in Golang and is designed to be the "Kubernetes of the Bare-AI Ecosystem." It provides a high-concurrency supervisory loop for Agents, CLIs, and high-performance AI Engines (GPU/CPU). It works out of the box with the bare-ai-agent, bare-ai-cli and bare-ai-engines. Contact us for more details.

---

## 🤖 Hybrid Engine Support

You can deploy the bare-ai-agent on different Command Line Interfaces (CLIs) namely:

| Engine | Type | Use Case |
|--------|------|----------|
| **Bare-AI-CLI** | Sovereign, local-first | Air-gapped environments, Vault integration, maximum control |
| **Gemini-CLI** | Cloud-based | Google Cloud integration, latest models |

**Auto-detection:** `bare` detects which engine is installed and routes automatically (priority: Bare-AI-CLI → Gemini-CLI).  
**Override:** `export BARE_ENGINE=bare` or `export BARE_ENGINE=gemini`

---

## 📜 Naming Convention

| Type | Rule | Example |
|------|------|---------|
| Installers | Must have `.sh` extension | `setup_bare-ai-worker.sh` |

Tools have no extension so the underlying implementation (Bash, Python, Go) can change without breaking system calls.

---

## 🔒 Security Notes

- Workers operate with minimal permissions — telemetry reporting and reflex execution only
- All telemetry is logged locally in JSON format
- No data leaves your network unless you choose the Google Gemini engine (option 2).

See [SECURITY.md](SECURITY.md) for the full security policy.

---

## 📦 Dependencies

| Component | Requirement | Notes |
|-----------|-------------|-------|
| Ollama | Ollama Latest | `or llm.cpp but different port numbers will be needed in vault secrets if used. Start with Ollama.` |
| Bare-AI-CLI | Node.js, npm | `npm install -g bare-ai-cli` |
| Gemini-CLI | Node.js, npm | `sudo npm install -g @google/gemini-cli` |
| SSH | OpenSSH client | Required for `bare-enroll` |
| jq | JSON processor | Required for `bare-status` |

---

## 🔐 Managing API Keys with HashiCorp Vault (Mandatory)

Bare-AI uses a locally hosted HashiCorp Vault to secure your API keys and model configurations. You do **not** need to store plain-text API keys in your `.bashrc` or environment variables. The setup script automatically configures the Vault and creates a "Sovereign Switchboard" of pre-mapped models with dummy API keys. 

To activate a Premium Cloud model, you simply need to "patch" the existing secret with your real API key.

### 1. Patching Cloud API Keys
Use the `vault kv patch` command. This safely updates *only* the `api_key` field while leaving the pre-configured routing URLs intact. Replace the placeholder with your actual key:

```bash
# Gemini 2.5 flash-lite Example Patch: 
vault kv patch secret/gemini-2.5-flash-lite/config api_key="YOUR_REAL_KEY_STARTS_WITH:AI"

# GPT-5.5 Example Patch: 
vault kv patch secret/gpt-5.5/config api_key="YOUR_REAL_KEY_STARTS_WITH:sk"

# Claude-Sonnet-4.6 Example Patch: 
vault kv patch secret/claude-sonnet-4-6/config api_key="YOUR_REAL_KEY_STARTS_WITH:sk"

# DeepSeek-V4-Pro Example Patch: 
vault kv patch secret/deepseek-v4-pro/config api_key="YOUR_REAL_KEY_STARTS_WITH:sk"
```
Tip: To find the exact path for other models, look at the model slug in brackets [modelName] within the Sovereign Switchboard menu.

2. Vault Recovery & Maintenance (CRITICAL)
During installation, your Vault Root Token and Unseal Key were generated and saved locally to:
```bash
~/.bare-ai/config/vault-recovery-keys.txt
```
The Reboot Reality: HashiCorp Vault automatically seals itself every time the system reboots. If your machine restarts, the Bare-AI engine will fail to load. You must manually unseal the Vault by running:

```bash
vault operator unseal
```
# (Paste the Unseal Key from your recovery text file when prompted)
Security Recommendation: For true Enterprise security, copy the contents of vault-recovery-keys.txt into a secure password manager (like Bitwarden) and then delete the file from the machine entirely. Leaving the master keys in plain text on the hard drive is a security risk.

3. Agent Connection Configuration
If you are connecting an agent to a remote Vault server, edit your credentials:
```bash
nano ~/.bare-ai/config/vault.env
```

Fill in your details:

```bash
# Note this will be provided as part of a new install most probably will be local host for single machine users.
export VAULT_ADDR=https://<YOUR_VAULT_IP>:8200

# Note by default the Role ID and secret will be stored as an env variable that only the installation user can access, however, for increased security do not store in this env variable, instead, keep somewhere else secure (like Bitwarden) and only load into the machine when using the AI agent. IE Export injection. 

export VAULT_ROLE_ID=<YOUR_APPROLE_ID>
export VAULT_SECRET_ID=<YOUR_SECRET_ID>
```

## 🌐 Networking & ConnectivityLAN vs. TailscaleLAN (Recommended): 
Use the standard LAN IP (192.168.x.x) for the lowest latency.Tailscale (Optional): To call your "agents" from outside your home network, use Tailscale IPs. Note: You must install and authenticate Tailscale on the VM manually.
Inference Server Setup. To allow your agents to talk to the bare-ai-engines. Your Ollama/Inference server must be listening on the network:export OLLAMA_HOST=0.0.0.0

## 📝 Architecture Note Regarding the Optional TailscaleLAN: 

Transport Layer Security While the SearXNG endpoint utilises standard HTTP, it is vital to note that all fleet communication occurs over a Tailscale/Headscale (CGNAT) overlay network where you elect to use tailscale/headscale (highly recommended by the Cloud Integration Corporation). 

Encapsulated Encryption: All traffic within the 100.x.x.x range is automatically encapsulated within an encrypted WireGuard tunnel. This provides robust transport-layer security across both local and public networks, regardless of the application-layer protocol (HTTP or HTTPS).

Cosmetic SSL Termination: For environments requiring end-to-end TLS for compliance or cosmetic consistency, a reverse proxy (e.g., NGINX, Caddy, or Traefik) can be implemented to provide an HTTPS head-end. Please note: Reverse proxys are not provided as part of this project though. Please also remember that headscale/tailscale is not either but we use it in our own impelmentation by default and inside/Outside our own LAN and accept the slight latency delay (adds 30/40ms in our testing but worth it for security).

Sovereign Privacy: By leveraging the Tailscale VPN layer, the mesh ensures that search queries and vault secrets remain invisible to the underlying ISP or local network sniffers.

## 🧰 The Bare-Necessities AI Deterministic Toolkit (put simply: Saves tokens) 
The installer deploys global symlinks in bash or python3 for optimised host management:

| Alias | Function | Target |
|--------|------|----------|
| cpu-temp | Thermal Audit | Debian based system / Tctl Priority |
| pve-check | Resource Monitor | Proxmox VM/CT Logic |
| ai-monitor | Memory Pressure | RAM/VRAM Check |
| code-map | AST Mapping  | Deep Code Analysis |

---

## 🚀 Quick Start (with two options depeneding on your requirements):

> **Note:** The repo can be cloned to any directory. All scripts detect their location automatically.

*Sample installation Video (hosted on youtube): https://youtu.be/4EYMQWYJskU

### Option 1. Setting Up a Worker Node Only (IE where Ollama is installed on another machine in the same network, ie like a Gaming PC / GPU Server).

Run this on the target worker machine:

```bash

# 1 This launches the bare-ai-agent worker Installer
# Note: The installer will prompt you to select your AI engine (Bare-AI-CLI or Gemini-CLI).

curl -fsSL https://bare-ai.me/install.sh | bash

```

## OR ##

### Option 2. Setting Up a Worker Node and AI Engine Node (ie where you will run the bare-ai agent, bare-ai-cli and Ollma or llama.cpp in one machine.)

Run this on the target worker / AI Engine machine:

```bash

# 0 You must install an AI Engine like Ollama (Default Engine)
curl -fsSL https://ollama.com/install.sh | sh

# 1 Then launch the bare-ai-agent worker Installer
# Note: The installer will prompt you to select your AI engine (Bare-AI-CLI or Gemini-CLI) Ensure you select Option 1.
curl -fsSL https://bare-ai.me/install.sh | bash

```

## 🔧 Daily Usage (bare commands)

```bash
# Start an AI session (auto-detects engine)

bare

1) Admin Comamands:

# Update
bare-update

# Uninstall
bare-uninstall

--
2) Model Comamands:

# Switchboard ByPass
bare <LLMName> launch bare with given model (if available) ie, without launching switchboard

3) Bare AI Diary

Session logs are automatically saved to `~/.bare-ai/diary/YYYY-MM-DD.md` with engine tagging (🤖 Bare-AI / ✨ Gemini).

```

## 📁 Hashi Corp Vault Important Notes for Cloud AIs (Gemini, GPT, Claude, Deepseek, Grok etc)

*1) Vault has been pre-injected with every model in the sovereign swithboard automatically as part of the installation script including the api end point for the given cloud models.

*2) All you need to do is to PATCH the given secrets with your actual API keys. Note each model has its own secret so you can have different api keys for different vendors models ie for different cost center etc or simply reuse the same api key for the same vendor (only).

*3) Patching Examples: 

```bash

# 3.1) Gemini 2.5 flash-lite Example Patch: 
vault kv patch secret/gemini-2.5-flash-lite/config api_key="YOUR_REAL_KEY_STARTS_WITH:AI"

# 3.2) gpt-5.5 Example Patch: 
vault kv patch secret/gpt-5.5/config api_key="YOUR_REAL_KEY_STARTS_WITH:sk"

# 3.3) claude-sonnet-4-6 Example Patch: 
vault kv patch secret/claude-sonnet-4-6/config api_key="YOUR_REAL_KEY_STARTS_WITH:sk"

# 3.4) deepseek-v4-pro Example Patch: 
vault kv patch secret/deepseek-v4-pro/config api_key="YOUR_REAL_KEY_STARTS_WITH:sk"

## Reminder replace for all the AI models from these vendors, tip: see the exact naming convention in the sovereign switchboard between the square brackets: [modelName]

```

## 📁 Repository Structure

```
bare-ai-agent/
├── ARCHITECTURE.md
├── README.md
├── SECURITY.md
├── constitution.md
├── fleet.conf
└── scripts/
    │── bare-necessities/
    │   ├── bare-bash-scripts
    │   ├── bare-python3-scripts
    │   ├── worker/
    │   └── setup_bare-ai-worker.sh
    └── windows_alpha/
```

After installation, runtime config is auto-created at `~/.bare-ai/`:

```
~/.bare-ai/
├── bin/              # Installed tools (added to PATH)
│   ├── bare-enroll
│   ├── bare-audit
├── diary/            # Daily AI conversation logs
├── logs/             # JSON telemetry logs
├── config            # Agent config (AGENT_ID, ENGINE_TYPE)
├── agent.env         # Repo path (set at install time)
└── constitution.md   # Core identity and operational rules
```

---

## What's New in v5.5.2

This release introduces the Sovereign Switchboard, seamlessly bridging the gap between zero-cost local execution and premium cloud intelligence while maintaining strict operational isolation.

- ✅ Premium Cloud Multi-Tenant Routing - Expanded the Sovereign Menu with a 3-digit switchboard to support distinct 1:1 Vault secret paths for granular billing and access control across Google, Anthropic, and OpenAI models.
- ✅ Dual-Engine Conditional Rendering - Implemented strict execution wrappers to isolate the comprehensive Sovereign menu from the standard Gemini CLI, completely preventing execution crashes when switching backends.
- ✅ Standardised Tool-State Awareness - Hardcoded precise tool-use flags (true/false) across all 19 model endpoints, ensuring only capable "Doer" or reasoning models attempt function calling.
- ✅ Provisioned endpoints for GPT-5.5 (OpenAI) and DeepSeek V4 (Flash & Pro).
- ✅ Added a pidof systemd guard around service commands. This prevents the script from crashing in minimal environments or restricted containers where systemctl isn't available.
- ✅ Added x3 new models for Deepseek R4 Premium, Flash and Open AIs Chat GPT5.5 Cloud AIs.

## What's New in v5.4.0
This release focuses on rapid fleet management and unified identity protocols, solidifying the agent's core operational logic and upgrading the default model hierarchy.

- ✅ Rapid Fleet Deployment - Introduced the --fast flag in the worker setup to bypass NPM rebuilds, enabling lightning-fast (~3s) configuration and menu updates across all active nodes.
- ✅ Unified Identity Injection - The Bash wrapper now dynamically concatenates both technical-constitution.md and role.md into the $BARE_AI_SYSTEM_PROMPT at runtime for flawless persona and rule adherence.
- ✅ Dynamic Persona Resolution - Resolved the "Self-Healing" persona hardcode override by ensuring the Sovereign Engine strictly respects injected dynamic environment variables over static defaults.
- ✅ Next-Gen "Doer" Promotion - Elevated Alibaba Qwen 2.5 Coder (32B) to the primary Doer role, officially replacing IBM Granite 3.3 for advanced local tool execution and coding tasks.
- ✅ iGPU Vulkan Hardening - Corrected Vault syntax and IP formatting specifically targeting the Tir-Na-AI iGPU endpoints to ensure stable Vulkan acceleration.
- ✅ "Liege" UX Enforcement - Standardised node responses across the fleet by embedding the "Liege" protocol directly into the base technical constitution.

## What's New in v5.3.0

This release graduates the worker node to **Level 4 Autonomy**, enabling fully unchained, self-healing, and context-aware script execution directly on bare metal.

- ✅ Sovereign Autonomy Overrides (YOLA) - The agent now boots with `BARE_AI_YOLA_MODE` and `BARE_AI_DISABLE_WORKSPACE_TRUST` natively injected into the session environment. Zero execution prompts, zero jail warnings.
- ✅ Token-Optimised Global Symlinks - Restored `.sh` and `.py` extensions to the `bare-necessities` toolkit global binaries. This gives local LLMs critical environmental context *without* wasting tokens reading file headers.
- ✅ Flawless Multi-Tool Chaining - Hardened the directory mapping and permissions logic so the AI can successfully chain `write_file` -> `chmod +x` -> `execute` autonomously in a single generation.
- ✅ Strict Bash Compliance - Re-architected variable resolution and Git deployment ordering to survive strict `set -euo pipefail` OS conditions without crashing.
- ✅ Refined Terminal UX - Upgraded post-installation instructions to clearly differentiate between required commands and optional fleet-management tools.

## What's New in v5.2.0

- ✅ Sovereign Search Integration — Native SearXNG support via BARE_AI_SEARCH_URL with automatic Google fallback.
- ✅ Vault Credential Automation — Dynamic generation of vault.env featuring CIC architecture documentation and ASCII branding.
- ✅ Deterministic Tooling — Full deployment of the bare-necessities toolkit (cpu-temp, pve-check, etc.) with global symlink mapping.

## What's New in v5.1.0

- ✅ Dual engine support — Bare-AI-CLI and Gemini-CLI
- ✅ Automatic engine detection — no configuration needed
- ✅ Engine override aliases — `bare-gemini` and `bare-sovereign`
- ✅ Enhanced logging — engine-specific tagging in diary entries
- ✅ Repository renamed — `bare-ai-agent` (previously `Bare-ai`)
- ✅ Dynamic path detection — scripts work regardless of clone directory name

---

## 🚑 "3rd Party Troubleshooting"
1. Ollama 500 Error / Out of Memory (Model won't load)

The Cause: Linux hoards RAM in the buff/cache column, causing the pre-flight check to fail when loading massive models (like 32B+).

The Fix: Force Linux to flush the cache by running:
sudo sync; sudo bash -c "echo 3 > /proc/sys/vm/drop_caches"

2. Vault Service Fails to Start (Linux Mint / Ubuntu)

The Cause: Systemd initialization issues or broken partial installations.

The Fix: Wipe the corrupted Vault state and retry:
sudo systemctl stop vault
sudo rm -rf /opt/vault/data/*
Then run the setup script again.

## 📝 License

Apache-2.0

In short, these projects are free to copy and modify but you must credit google (orgianl gemini gli developers) and cloud integration corporation (bare-ai) in your work.

## by the Cloud Integration Corporation

```text
    ____ _                  _ _       _         ____       
   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   
  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \  
  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) | 
   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/
