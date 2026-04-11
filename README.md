# 🦾 Bare-AI-Agent: Autonomous Infrastructure Management (The Sovereign Mesh)
bare-ai-agent is the autonomous "Hands" of the Bare-AI ecosystem. It is designed to be installed on any Linux VM, container, or physical machine to provide agentic capabilities, while calling upon a centralized "Brain" for intelligence.

**Version:** 5.1.2- Enterprise (Hybrid Architect Edition)  
**Author:** Cian Egan  
**Date:** 2026-04-11 
**Repository:** [github.com/Cian-CloudIntCorp/bare-ai-agent](https://github.com/Cian-CloudIntCorp/bare-ai-agent)

Bare-AI-Agent is a multi-node, self-healing architecture designed to manage data pipelines and infrastructure integrity across Linux and Windows environments. The system supports **dual AI engines** — choose between the sovereign Bare-AI-CLI or Google's Gemini-CLI.

---

## 🏛️ System Architecture
This project is designed to mimic the cloud AI experience (like Chat GPT or Google Gemini etc) but entirely on your own hardware:

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

---

## 🤖 Hybrid Engine Support

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

## 📁 Repository Structure

```
bare-ai-agent/
├── ARCHITECTURE.md
├── README.md
├── SECURITY.md
├── constitution.md
├── fleet.conf
└── scripts/
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

## 🚀 Quick Start

> **Note:** The repo can be cloned to any directory. All scripts detect their location automatically.

### 1. Setting Up a Worker Node

Run this on the target worker machine:

```bash
# 1. Clone the repository
git clone https://github.com/Cian-CloudIntCorp/bare-ai-agent.git ~/bare-ai-agent

# 2. Run the worker installer
cd ~/bare-ai-agent/scripts/worker
chmod +x setup_bare-ai-worker.sh
./setup_bare-ai-worker.sh

# 3. Reload your shell
source ~/.bashrc

# 4. Verify
bare-summarize
```

The installer will prompt you to select your AI engine (Bare-AI-CLI or Gemini-CLI).

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

---

## 🔒 Security Notes

- The Architect Console runs on your local dev machine — **never on production servers**
- The Brain's Vault credentials are **never stored in this repository**
- Workers operate with minimal permissions — telemetry reporting and reflex execution only
- All telemetry is logged locally in JSON format
- No data leaves your network unless you choose the Gemini engine

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
🔐 Refined Vault Section (Copy/Paste this fix)Replace the text from your "Vault Configuration" section with this formatted version:Markdown---

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

## 🆕 What's New in v5.1.0

- ✅ Dual engine support — Bare-AI-CLI and Gemini-CLI
- ✅ Automatic engine detection — no configuration needed
- ✅ Engine override aliases — `bare-gemini` and `bare-sovereign`
- ✅ Enhanced logging — engine-specific tagging in diary entries
- ✅ Repository renamed — `bare-ai-agent` (previously `Bare-ai`)
- ✅ Dynamic path detection — scripts work regardless of clone directory name

---

## 📝 License

Apache-2.0