# 🏛️ PROJECT BARE: Architectural Handover & Protocol
**Target Audience:** Autonomous Infrastructure Engineers & AI Systems Architects
**Repository:** [github.com/Cian-CloudIntCorp/bare-ai-agent](https://github.com/Cian-CloudIntCorp/bare-ai-agent)
**Version:** v5.5.1-Enterprise (Sovereign Switchboard Edition)

## 1. The Architectural Paradigm
Project Bare is a Level 4 Autonomous, Sovereign Overlay Network. It represents a strict decoupling of **Intelligence** (The Agents) from **Governance** (The Brain).

The architecture strictly separates the Control Plane from the Data/Execution Plane across two distinct codebases:

| Plane | Component | Location | Responsibility |
| :--- | :--- | :--- | :--- |
| **Control** | Sovereign Brain | `bare-ai-premium` (Golang Repo) | Agentless deterministic auditing, global fleet governance, and hardware safety reflexes. |
| **Data/Execution** | Bare-AI Agent | `bare-ai-agent` (This Repo) | Local Level 4 autonomous execution, self-healing data pipelines, and hybrid LLM routing. |

---

## 2. Component Topography

### 🧠 The Sovereign Brain (External Control Plane)
*Note: The Brain has graduated to its own standalone enterprise repository.*
The Brain is a compiled Golang orchestrator running as a daemon on a secure control node (e.g., `bare-dc`). 
* **Agentless Telemetry:** It does not rely on installed agent software. It uses a universal SSH probe to read native Linux kernel sensors (`hwmon`, `rocm-smi`, `nvidia-smi`).
* **Deterministic Execution:** It audits the entire data center in < 500ms and executes immediate, hardcoded reflexes (e.g., `docker stop`) if thermal or storage boundaries are breached.

### 👷 The Bare-AI Worker (The Execution Plane)
This repository contains the Worker Agent framework. It is an intelligent, self-aware CLI toolset injected directly into the Linux `$PATH`.
* **Level 4 Autonomy:** Workers run in `YOLA` (You Only Live Autonomously) mode, bypassing human confirmation prompts for trusted internal tasks.
* **Dual-Engine:** Workers can utilize either the `Bare-AI-CLI` (Sovereign local models) or the `Gemini-CLI` (Cloud models) based on dynamic Vault routing.

### 🧰 The Bare-Necessities Toolkit
A suite of highly optimized bash/python scripts (`cpu-temp`, `pve-check`, `code-map`) deployed globally to `/usr/local/bin/`. These provide the local LLM with immediate context regarding its host environment without wasting tokens generating complex native shell commands.

---

## 3. The Sovereign Switchboard (Vault Integration)
In v5.5.0+, the architecture utilizes HashiCorp Vault not just for secrets, but as a **Dynamic Model Router**. 

When a user or cron job invokes an agent (e.g., `bare 011`), the system:
1. Translates the 3-digit menu code to a model slug (e.g., `deepseek-r1:8b`).
2. Authenticates with Vault via local AppRole credentials (`~/.bare-ai/config/vault.env`).
3. Fetches the exact Inference Server IP and API Key required to run that specific model.
4. Executes the payload and saves the AST/Memory to the local diary.

This allows 1:1 mapping between models and infrastructure, ensuring isolation and modular scaling of GPU inference engines.

---

## 4. The Dual-Constitution Framework
To ensure safe autonomous execution, Agents operate under two distinct constitutional layers concatenated at runtime:

1. **The Technical Constitution (`technical-constitution.md`)**
   * **Owner:** Repository / Operations Team.
   * **Purpose:** Defines hard boundaries. (e.g., "Never modify `/etc/shadow`, use the bare-necessities toolkit for system checks"). 
   * **State:** Read-Only.

2. **The Functional Constitution (`role.md`)**
   * **Owner:** The Node Administrator / End User.
   * **Purpose:** Defines the persona and mission. (e.g., "You are a Python data engineer responsible for cleaning pipeline logs").
   * **State:** Read-Write.

---

## 5. Security Posture & The "Air-Gap" Illusion
The Sovereign architecture utilizes a **Tailscale/Headscale Mesh Overlay**.
* **Transport:** All fleet communication (Brain -> Worker, Worker -> Vault, Worker -> Ollama) occurs via IP spaces (e.g., `100.64.x.x`).
* **Encryption:** By defaulting to this mesh, we achieve automated, encapsulated WireGuard encryption across the entire Data Plane, rendering local application-layer SSL termination largely cosmetic.

## 6. End-State Goal
When this repository is deployed to a fresh Linux node, that node ceases to be a static VM. It becomes a context-aware, self-documenting "Doer" capable of repairing its own software stacks while reporting to the Sovereign Brain.