# Bare-AI-Agent

Bare-AI-Agent is a multi-node, self-healing agent framework for autonomous
infrastructure management on Linux hosts. It pairs a sovereign, local-first CLI
(Bare-AI-CLI) with OpenBao for secrets management and SearXNG for private web
search, so agent workloads can run without a hard dependency on cloud services.

The system runs a single AI engine:

- **Bare-AI-CLI** — sovereign, local-first

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

### Engine support

| Engine | Type | Use case |
| --- | --- | --- |
| Bare-AI-CLI | Sovereign, local-first client | Air-gapped environments, OpenBao integration |
| llama.cpp (`llama-server`) | Local inference | Strict OpenAI API/tool-calling fidelity |
| Ollama | Local inference | Ease of use, quick model pulls |

---

## Deployment topologies & inference engines

Bare-AI-Agent ships two installer entry points mapping to two deployment
topologies:

- **Standard** — single-machine. Everything (agent, OpenBao, SearXNG, and the
  inference engine) co-locates on one host and binds to `127.0.0.1`.

  ```bash
  curl -fsSL https://bare-ai.me/install.sh | bash
  ```

- **Pro** — multi-machine / container isolation (VM, LXC, Docker, Podman). Each responsibility is isolated onto
  its own node or container (OpenBao on one node, SearXNG on another,
  inference/Council on a dedicated CPU node). Long-lived services bind to
  `0.0.0.0` and the installer prompts for each remote node's IP.

  ```bash
  curl -fsSL https://bare-ai.pro/install-pro.sh | bash
  ```

### Choosing a sovereign inference engine

During setup you are offered three options (both engines are MIT-licensed):

1. **llama.cpp (`llama-server`)** — recommended for strict OpenAI API/tool-calling
   fidelity. The installer fetches a prebuilt
   `llama-server` binary, downloads a small default GGUF model, and runs it as a
   rootless user systemd service on port `8081`.
2. **Ollama** — recommended for ease of use and quick model pulls. Installed via
   the official script and exposed on port `11434`.
3. **Skip** — provide your own OpenAI-compatible endpoint.

The chosen engine's endpoint is written to `BARE_AI_ENDPOINT` in
`~/.bare-ai/config/agent.env`, which the `bare` launcher sources automatically.

| Engine | Standard bind | Pro bind | Free-tier endpoint |
| --- | --- | --- | --- |
| llama-server | `127.0.0.1:8081` | `0.0.0.0:8081` | `http://127.0.0.1:8081/v1/chat/completions` |
| Ollama | `127.0.0.1:11434` | `0.0.0.0:11434` | `http://127.0.0.1:11434/v1/chat/completions` |


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
- No data leaves the network unless a cloud model is explicitly selected.

See [SECURITY.md](SECURITY.md) for the full security policy.

---

## Dependencies

| Component | Requirement | Notes |
| --- | --- | --- |
| Inference engine | llama.cpp or Ollama | Provisioned by the installer (or bring your own endpoint) |
| Bare-AI-CLI | Node.js, npm | Installed and bundled by the installer |
| OpenBao | Latest | Provisioned by the installer (or reuse an existing server) |
| SearXNG | Docker | Provisioned by the installer (or reuse an existing instance) |
| SSH | OpenSSH client | Required for `bare-enroll` |
| jq | JSON processor | Required for `bare-status` |

---

## Secrets management (OpenBao)

The installer provisions OpenBao and seeds a set of pre-mapped model endpoints
with placeholder API keys. To enable a cloud model, patch the corresponding
secret with a real key:

```bash
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

The inference server must listen on the network so workers can reach it. The Pro
installer binds both engines to `0.0.0.0` automatically; to do it manually:

```bash
export OLLAMA_HOST=0.0.0.0                              # Ollama
llama-server --host 0.0.0.0 --port 8081 -m <model.gguf> # llama.cpp
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

### Standard (single machine)

```bash
curl -fsSL https://bare-ai.me/install.sh | bash
```

### Pro (multi-machine / container isolation)

```bash
curl -fsSL https://bare-ai.pro/install-pro.sh | bash
```

The installer provisions OpenBao, SearXNG, and a sovereign inference engine
(llama.cpp or Ollama — or you can bring your own endpoint), then builds and
wires up the Bare-AI-CLI. See
[Deployment topologies & inference engines](#deployment-topologies--inference-engines)
for details.

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

Session logs are written to `~/bare-necessities-workspace/bare-ai-diary/YYYY-MM-DD.md` with engine tagging.

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
    └── worker/
```

After installation, runtime configuration is created at `~/.bare-ai/`:

```
~/.bare-ai/
├── bin/              # Installed tools (added to PATH)
├── models/           # Default GGUF model weights (llama.cpp)
├── logs/             # JSON telemetry logs
├── config/
│   ├── agent.env     # ENGINE_TYPE, AGENT_ID, BARE_AI_ENDPOINT, BARE_AI_SEARCH_URL
│   └── vault.env     # OpenBao AppRole credentials
└── technical-constitution.md

The agent-authored workspace (scripts, diary) lives in
`~/bare-necessities-workspace/`, separate from the git-tracked repositories.
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
