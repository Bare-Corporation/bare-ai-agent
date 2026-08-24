# Bare-AI-Agent

Bare-AI-Agent is a multi-node, self-healing agent framework for autonomous
infrastructure management on Linux hosts. It pairs a sovereign, local-first CLI
(Bare-AI-CLI) with OpenBao for secrets management and SearXNG for private web
search, so agent workloads can run without a hard dependency on cloud services.

The system supports two AI engines:

- **Bare-AI-CLI** — sovereign, local-first
- **Gemini-CLI** — Google Cloud

**Author:** Cian Egan
**Created:** 2026-02-02
**CLI repository:** [github.com/Bare-Corporation/bare-ai-cli](https://github.com/Bare-Corporation/bare-ai-cli)

---

## Architecture

The architecture separates three concerns:

- **Inference** — one or more hosts running an LLM server (Ollama, vLLM, or
  llama.cpp) serve the models.
- **Workers** — lightweight nodes running Bare-AI-Agent and Bare-AI-CLI perform
  telemetry reporting and task execution.
- **Secrets** — a central OpenBao server holds model endpoints and API keys
  across the fleet.

### Workers

Workers operate under two constitutions:

- **Technical Constitution** (`~/.bare-ai/technical-constitution.md`) — read-only;
  defines tool-use boundaries, resource limits, and operational style.
- **Functional Constitution** (`~/.bare-ai/role.md`) — user-defined; defines the
  agent persona and functional scope.

### Secrets (OpenBao)

OpenBao is the Linux Foundation's open-source fork of HashiCorp Vault. Workers
authenticate to OpenBao via AppRole and receive endpoint URLs, model names, and
API keys at runtime. Credentials are injected into the session environment and
are not written to shell history or plaintext configuration files.

### Hybrid engine support

| Engine | Type | Use case |
| --- | --- | --- |
| Bare-AI-CLI | Sovereign, local-first | Air-gapped environments, OpenBao integration |
| Gemini-CLI | Cloud-based | Google Cloud models |

The `bare` entrypoint auto-detects the installed engine (priority:
Bare-AI-CLI, then Gemini-CLI). Override with `BARE_ENGINE=bare` or
`BARE_ENGINE=gemini`.

---

## Features

- Autonomous agentic execution on Linux hosts.
- OpenBao AppRole secrets injection (no plaintext keys in the shell).
- Hybrid routing between local models and cloud models.
- Sovereign web search via a self-hosted SearXNG instance.
- Deterministic host toolkit (`bare-necessities`) for hardware, thermal, and
  resource audits.
- Persistent session diary for context continuity across sessions.
- AST-based code mapping for navigating large codebases.

---

## Naming convention

| Type | Rule | Example |
| --- | --- | --- |
| Installers | `.sh` extension | `setup_bare-ai-worker.sh` |

Tools have no extension so the underlying implementation (Bash, Python, Go) can
change without breaking call sites.

---

## Security notes

- Workers run with minimal permissions — telemetry reporting and task execution.
- Telemetry is logged locally in JSON format.
- No data leaves the network unless the Gemini-CLI engine is selected.

See [SECURITY.md](SECURITY.md) for the full security policy.

---

## Dependencies

| Component | Requirement | Notes |
| --- | --- | --- |
| Ollama | Latest | Or llama.cpp (adjust port in OpenBao secrets accordingly) |
| Bare-AI-CLI | Node.js, npm | `npm install -g bare-ai-cli` |
| Gemini-CLI | Node.js, npm | `sudo npm install -g @google/gemini-cli` |
| SSH | OpenSSH client | Required for `bare-enroll` |
| jq | JSON processor | Required for `bare-status` |

---

## Secrets management (OpenBao)

The installer provisions OpenBao and seeds a set of pre-mapped model endpoints
with placeholder API keys. To enable a cloud model, patch the corresponding
secret with a real key:

```bash
# Gemini example
bao kv patch secret/gemini-2.5-flash-lite/config api_key="YOUR_REAL_KEY_STARTS_WITH:AI"

# OpenAI example
bao kv patch secret/gpt-5.5/config api_key="YOUR_REAL_KEY_STARTS_WITH:sk"

# Anthropic example
bao kv patch secret/claude-sonnet-4-6/config api_key="YOUR_REAL_KEY_STARTS_WITH:sk"

# DeepSeek example
bao kv patch secret/deepseek-v4-pro/config api_key="YOUR_REAL_KEY_STARTS_WITH:sk"
```

The exact secret path for each model is shown in the Sovereign Switchboard menu,
between square brackets (`[modelName]`).

### Unsealing after reboot

OpenBao seals itself on reboot. After a restart, unseal it with:

```bash
bao operator unseal
```

The root token and unseal keys are written to
`~/.bare-ai/config/vault-recovery-keys.txt` during installation. Move these into
a password manager and delete the plaintext file.

### Remote OpenBao

To connect a worker to a remote OpenBao server, edit
`~/.bare-ai/config/vault.env`:

```bash
export VAULT_ADDR=https://<OPENBAO_IP>:8200
export VAULT_ROLE_ID=<APPROLE_ID>
export VAULT_SECRET_ID=<SECRET_ID>
```

---

## Networking

- **LAN** — use the standard LAN IP for the lowest latency.
- **Tailscale / Headscale** — use the overlay network to reach workers from
  outside the LAN. Tailscale must be installed and authenticated on each node.

The inference server must listen on the network so workers can reach it:

```bash
export OLLAMA_HOST=0.0.0.0
```

Fleet traffic over Tailscale/Headscale is encapsulated in an encrypted WireGuard
tunnel. For environments that require application-layer TLS, terminate at a
reverse proxy (NGINX, Caddy, or Traefik); the proxy and the overlay network are
deployed separately from this project.

---

## bare-necessities toolkit

The installer deploys global symlinks for deterministic host management:

| Alias | Function | Target |
| --- | --- | --- |
| `cpu-temp` | Thermal audit | Tctl/Tdie priority |
| `pve-check` | Resource monitor | Proxmox VM/CT |
| `ai-monitor` | Memory pressure | RAM/VRAM |
| `code-map` | AST mapping | Code analysis |

---

## Installation

The repository can be cloned to any directory; scripts detect their location at
runtime.

### Option 1 — worker node only

```bash
curl -fsSL https://bare-ai.me/install.sh | bash
```

### Option 2 — worker and inference on one node

```bash
curl -fsSL https://ollama.com/install.sh | sh
curl -fsSL https://bare-ai.me/install.sh | bash
```

The installer prompts for the AI engine (Bare-AI-CLI or Gemini-CLI).

---

## Usage

```bash
# Start a session (auto-detects engine)
bare

# Update
bare-update

# Uninstall
bare-uninstall

# Launch with a specific model (bypass the switchboard)
bare <LLMName>
```

Session logs are written to `~/.bare-ai/diary/YYYY-MM-DD.md` with engine tagging.

---

## Repository structure

```
bare-ai-agent/
├── ARCHITECTURE.md
├── README.md
├── SECURITY.md
├── role.md
├── install.sh
├── install-pro.sh
└── scripts/
    ├── bare-necessities/
    ├── templates/
    ├── worker/
    └── windows_alpha/
```

After installation, runtime configuration is created at `~/.bare-ai/`:

```
~/.bare-ai/
├── bin/              # Installed tools (added to PATH)
├── diary/            # Session logs
├── logs/             # JSON telemetry logs
├── config/           # Agent configuration
├── agent.env         # Repository path (set at install time)
└── technical-constitution.md
```

---

## Troubleshooting

**Ollama 500 / out of memory**

Linux may hold memory in the page cache, causing the pre-flight check to fail
for large models. Flush the cache:

```bash
sudo sync; sudo bash -c "echo 3 > /proc/sys/vm/drop_caches"
```

**OpenBao service fails to start**

Stop the service, clear corrupted state, and re-run the setup script:

```bash
sudo systemctl stop bao
sudo rm -rf /opt/openbao/data/*
```

---

## Sovereign Brain (Coordinator)

The Sovereign Brain is a deterministic orchestrator written in Go, designed as
the supervisory control plane for the Bare-AI ecosystem. It provides a
high-concurrency loop for agents, CLIs, and AI engines (CPU/GPU), and works out
of the box with Bare-AI-Agent, Bare-AI-CLI, and Bare-AI-Engines.

It is distributed as a separate project for production multi-node deployments.
Contact the Bare-Corporation team for details.

---

## License

Apache-2.0.

This project is a derivative work of the Google Gemini CLI. Redistribution must
credit the original Gemini CLI authors and the Bare-Corporation (Bare-AI)
maintainers.
