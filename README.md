# 🦾 Bare-AI-Agent: Autonomous Infrastructure Management (The Sovereign Mesh)

Bare-AI-Agent is a multi-node, self-healing architecture designed to manage data pipelines and infrastructure integrity across Linux and Windows environments. The system supports **dual AI engines** — choose between the sovereign Bare-AI-CLI or Google's Gemini-CLI.

Bare-ai-agent is a local-first, privacy-hardened autonomous agent framework designed for Debian-based Linux hosts. It bridges the gap between high-performance cloud models and sovereign local infrastructure using HashiCorp Vault for secrets management and SearXNG for private web grounding. The project also comes pre installed with bare-brain which is a centralized "Brain" written in bash for agentic fleet intelligence. Note: a premium version of the brain exists seperatly (for a fee) which is written in Golang.

Note: a very early alpha of bare-ai-agent for windows is pre bundled but only works with gemini cli at present. as bare-ai-cli has only been built tested for debian based systems currently. This is not further documented for now but the intention is for it to follow the same design principles as bare-ai-agent / bare-ai-cli for linux.

**Version:** 5.3.0 - Enterprise (Hybrid Architect Edition)  
**Author:** Cian Egan  (CEO & Chief Architect at the Cloud Integration Corporation)
**Created Date:** 2026-02-02
**Updated Date:** 2026-04-14
**Repository:** [github.com/Cian-CloudIntCorp/bare-ai-agent](https://github.com/Cian-CloudIntCorp/bare-ai-agent)

---

## 🛠 Features
- **Identity:** Autonomous Linux Agent with Level 4 Autonomy.
- **Security:** Zero-Knowledge local secret injection via Vault AppRole.
- **Intelligence:** Hybrid routing between Google Gemma 4 (31B) and local optimized models (DeepSeek, Granite).
- **Grounding:** Sovereign search via SearXNG (Chinese & Global results).
- **Telemetry:** Real-time hardware audits via the `bare-necessities` toolkit.
- **Self-Healing Data Pipelines:** Autonomous error detection and log sniping.
- **Context Management:** High-speed AST mapping for large codebases.
- **Thermal Awareness:** Real-time system Tctl monitoring and load balancing.
- **AI Diary:** Persistent session memory to prevent hallucination loops.

## 🏛️ System Architecture
This project is designed to mimic the cloud AI terminal Command line interface (CLI) experience (like Google Gemini or Claude Code etc) but entirely on your own hardware.

The AI model(s): 1 centralized High-Performance VM or PC running an LLM (e.g., Ollama/vLLM with Granite 4).

The Brain: Optional but used to manage many agents, kubernetes for ai, (note purely expieremental) 

The Worker Hands: Unlimited lightweight VMs running bare-ai-agent and bare-ai-cli.

The Memory: A centralized HashiCorp Vault server to manage endpoints and keys securely across the fleet.

The fleet follows a strict role-based hierarchy to ensure safety and scalability:

### 1. The Architect (Dev Console)
**Primary Host:** `penguin` (Chromebook/Debian)  
**Role:** Central command & deployment.  
**Key Tools:**
- `bare` — Hybrid AI assistant (auto-detects Bare-AI-CLI or Gemini-CLI)
- `bare-gemini` — Force Gemini engine
- `bare-sovereign` — Force Bare-AI-CLI engine
- `bare-engine` — Show current active engine
- `bare-enroll` — Deploy worker logic to remote nodes via SSH
- `bare-status` — Local telemetry audit

### 2. The Brain (Coordinator)
**Primary Host:** `bare-dc` (User: `bare-ai`)  
**Role:** Autonomous fleet monitoring and self-healing decisions.  
**Logic:** Runs the MAPE-K loop — harvests telemetry from workers, analyzes with an LLM, executes reflex commands via SSH.

### 3. The Workers (Fleet Nodes)
**Hosts:** Any enrolled Linux node  
**Role:** Telemetry reporting and payload execution.  
**Core Tool:** `bare-summarize` — outputs structured JSON telemetry for the Brain.

## 3a. ⚖️ Bare-ai-agent Workers Technical Constitution
The worker agent(s) operates under a read-only Technical Constitution found in ~/.bare-ai/technical-constitution.md. This defines tool-use boundaries, resource limits, and sovereign operational style.

## 3b. ⚖️ Bare-ai-agent Workers Functional Constitution
The worker agent(s) operates under a configurable (end user defined) Functional Constitution found in ~/.bare-ai/role.md. This defines the ai persona, skill set, functional boundaries, examples: You are a developer, doctor, lawyer, CEO, etc, the limits are endless.

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
| Tools/Artifacts | No extension | `bare-summarize`, `bare-enroll` |

Tools have no extension so the underlying implementation (Bash, Python, Go) can change without breaking system calls.

---

## 🔒 Security Notes

- The Architect Console runs on your local dev machine — **never on production servers**
- The Brain's Vault credentials are **never stored in this repository**
- Workers operate with minimal permissions — telemetry reporting and reflex execution only
- All telemetry is logged locally in JSON format
- No data leaves your network unless you choose the Google Gemini engine (option 2).

See [SECURITY.md](SECURITY.md) for the full security policy.

---

## 📦 Dependencies

| Component | Requirement | Notes |
|-----------|-------------|-------|
| Bare-AI-CLI | Node.js, npm | `npm install -g bare-ai-cli` |
| Gemini-CLI | Node.js, npm | `sudo npm install -g @google/gemini-cli` |
| SSH | OpenSSH client | Required for `bare-enroll` |
| jq | JSON processor | Required for `bare-status` |

---

## 🔐 Vault Configuration (Mandatory)

The agent remains "Sovereign" by fetching its own connection details from your centralized Vault server. 

1. Configure the Agent's Vault Access
After installation, edit your local credentials to allow the agent to talk to your Vault server:
`nano ~/.bare-ai/config/vault.env`

The installer generates this file with `export` keywords. Simply fill in your details:
```bash
export VAULT_ADDR=https://<YOUR_VAULT_IP>:8200
export VAULT_ROLE_ID=<YOUR_APPROLE_ID>
export VAULT_SECRET_ID=<YOUR_SECRET_ID>
```

2. Configure the Secret Path in VaultThe agent fetches its intelligence endpoint from a secret path (default: secret/data/granite/config).Required Keys in your Vault Secret:KeyValue ExampleDescriptionBARE_AI_ENDPOINThttp://192.168.86.130:11434/v1/chat/completionsThe LAN IP of your Inference Server.BARE_AI_MODELgranite4:3bThe specific model name running on the Brain.

## 🌐 Networking & ConnectivityLAN vs. TailscaleLAN (Recommended): 
Use the standard LAN IP (192.168.x.x) for the lowest latency.Tailscale (Optional): To call your "Brain" from outside your home network, use Tailscale IPs. Note: You must install and authenticate Tailscale on the VM manually.Inference Server Setup (The Brain)To allow your agents to talk to the brain, your Ollama/Inference server must be listening on the network:export OLLAMA_HOST=0.0.0.0

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

## 🚀 Quick Start

> **Note:** The repo can be cloned to any directory. All scripts detect their location automatically.

### 1. Setting Up a Worker Node

Run this on the target worker machine:

```bash
# 1 Clone the repository
git clone https://github.com/Cian-CloudIntCorp/bare-ai-agent.git ~/bare-ai-agent

# 2 Launch the Installer
# Note: The installer will prompt you to select your AI engine (Bare-AI-CLI or Gemini-CLI).

cd ~/bare-ai-agent/scripts/worker
chmod +x setup_bare-ai-worker.sh
./setup_bare-ai-worker.sh

# 3. Reload your shell
source ~/.bashrc

# 4. Verify
bare-summarize
```

---

### 2. Setting Up the Architect Console (Penguin / Dev Machine)

Run this on your developer machine:

```bash
# 1. Clone the repository
git clone https://github.com/Cian-CloudIntCorp/bare-ai-agent.git ~/bare-ai-agent

# 2. Run the Architect setup
cd ~/bare-ai-agent/scripts/dev
chmod +x setup_bare-ai-dev.sh
./setup_bare-ai-dev.sh

# 3. Reload your shell
source ~/.bashrc

# 4. Verify
bare-status
bare-engine
```

---

### 3. Setting Up the Brain (bare-dc)

Run this on your central coordinator machine:

```bash
# 1. Clone the repository
git clone https://github.com/Cian-CloudIntCorp/bare-ai-agent.git ~/bare-ai-agent

# 2. Run the Brain installer
cd ~/bare-ai-agent/scripts/brain
chmod +x setup_bare-brain.sh
./setup_bare-brain.sh

# 3. Reload your shell
source ~/.bashrc
```

> ⚠️ The Brain uses HashiCorp Vault for secure credential management. Ensure Vault is accessible and your AppRole credentials are configured before running.

---

### 4. Enrolling a New Worker from the Architect Console

Once the Architect Console is set up on Penguin, deploy to any remote node:

```bash
bare-enroll <user@host_or_ip>
```

Example:

```bash
bare-enroll bare-ai@10.0.0.25
```

The worker node will be staged, uploaded, and installed automatically.

---

## 🔧 Daily Usage (Architect Console)

```bash
# Start an AI session (auto-detects engine)
bare

# Force a specific engine
bare-gemini       # Use Gemini
bare-sovereign    # Use Bare-AI-CLI

# Check which engine is active
bare-engine

# Deploy to a new worker
bare-enroll bare-ai@10.0.0.25

# Check local telemetry
bare-status

# Navigate to repo
bare-cd
```

Session logs are automatically saved to `~/.bare-ai/diary/YYYY-MM-DD.md` with engine tagging (🤖 Bare-AI / ✨ Gemini).

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
    ├── brain/
    │   ├── bare-brain-compiled
    │   └── setup_bare-brain.sh
    ├── dev/
    │   └── setup_bare-ai-dev.sh
    ├── worker/
    │   ├── bare-summarize
    │   └── setup_bare-ai-worker.sh
    └── windows_alpha/
```

After installation, runtime config is auto-created at `~/.bare-ai/`:

```
~/.bare-ai/
├── bin/              # Installed tools (added to PATH)
│   ├── bare-enroll
│   ├── bare-audit
│   └── bare-summarize
├── diary/            # Daily AI conversation logs
├── logs/             # JSON telemetry logs
├── config            # Agent config (AGENT_ID, ENGINE_TYPE)
├── agent.env         # Repo path (set at install time)
└── constitution.md   # Core identity and operational rules
```

---

## What's New in v5.3.0

This release graduates the worker node to **Level 4 Autonomy**, enabling fully unchained, self-healing, and context-aware script execution directly on bare metal.

- ✅ Sovereign Autonomy Overrides (YOLA) - The agent now boots with `BARE_AI_YOLA_MODE` and `BARE_AI_DISABLE_WORKSPACE_TRUST` natively injected into the session environment. Zero execution prompts, zero jail warnings.
- ✅ Token-Optimized Global Symlinks - Restored `.sh` and `.py` extensions to the `bare-necessities` toolkit global binaries. This gives local LLMs critical environmental context *without* wasting tokens reading file headers.
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

## 📝 License

Apache-2.0

## by the Cloud Integration Corporation

```text
    ____ _                  _ _       _         ____       
   / ___| | ___  _   _  ___| (_)_ __ | |_      / ___|___   
  | |   | |/ _ \| | | |/ __| | | '_ \| __|     | |   / _ \  
  | |___| | (_) | |_| | (__| | | | | | |_      | |__| (_) | 
   \____|_|\___/ \__,_|\___|_|_|_| |_|\__|      \____\___/
