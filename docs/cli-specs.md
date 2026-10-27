# CLI and Agent Specifications

**Scope:** factual capability reference for `bare-ai-cli` and `bare-ai-agent`.
**Captured:** 2026-10-27 on a bare-ai developer node.
**Version at capture:** `0.1.0-bare-ai`; fleet tag `v2.0.0`
(`bare-ai-cli` at `04030743efe638da95d082685758c43a92b2b980`).

Every flag below was copied from the tool's own `--help` output, not paraphrased. Where something
could not be evidenced it is named in [UNVERIFIED / OPEN](#unverified--open) rather than guessed.

---

## 1. bare-ai-cli

**What it is.** A terminal AI agent CLI: it takes a prompt, an optional model selection, and a set of
tool permissions, and runs either an interactive session or a single non-interactive turn.

### Install / entrypoint

The repository is `/bare-ai-cli` (a Node npm workspace monorepo). The distributable is a single
esbuild bundle at `bundle/bare-ai.js`, built with `npm run build`.

Two facts worth stating plainly, because both surprise people:

- **`bundle/` is gitignored.** The bundle is built per machine. It does not arrive by `git pull`.
- **There is no `bare-ai` executable on `PATH`.** On a bare-ai node the CLI is reached through the
  `bare` shell function that the worker installer writes into `~/.bashrc`, or directly as
  `node bundle/bare-ai.js`. `bare` is a shell function, so it does not exist in a non-interactive
  context (cron, systemd, a dispatch script) — those must invoke the bundle path directly.

```
node bundle/bare-ai.js --help
node bundle/bare-ai.js --version      # prints 0.1.0-bare-ai
```

### Command surface

`--help` reports the program name as `gemini`, which is the upstream lineage it is forked from. The
name in the help text is not the product name.

```
Usage: gemini [options] [command]

Commands:
  gemini [query..]             Launch Bare AI CLI  [default]
  gemini mcp                   Manage MCP servers
  gemini extensions <command>  Manage Bare AI CLI extensions.  [aliases: extension]
  gemini skills <command>      Manage agent skills.  [aliases: skill]
  gemini hooks <command>       Manage Bare AI CLI hooks.  [aliases: hook]

Positionals:
  query  Initial prompt. Runs in interactive mode by default; use -p/--prompt for non-interactive.
```

### Complete flag list

Copied verbatim from `--help`:

| Flag | Behaviour as documented by the tool |
|---|---|
| `-d, --debug` | Run in debug mode (open debug console with F12) `[boolean] [default: false]` |
| `-m, --model` | Model `[string]` |
| `-p, --prompt` | Run in non-interactive (headless) mode with the given prompt. Appended to input on stdin (if any). `[string]` |
| `--prompt-file` | Read the prompt from a file (for large prompts that exceed command-line limits). `[string]` |
| `-i, --prompt-interactive` | Execute the provided prompt and continue in interactive mode `[string]` |
| `--prompt-interactive-file` | Read the interactive initial prompt from a file (for prompts that exceed command-line limits). Equivalent to `--prompt-interactive (-i)` with the file contents. `[string]` |
| `-s, --sandbox` | Run in sandbox? `[boolean]` |
| `-y, --yolo` | Automatically accept all actions (aka YOLO mode) `[boolean] [default: false]` |
| `--approval-mode` | `default` (prompt for approval), `auto_edit` (auto-approve edit tools), `yolo` (auto-approve all tools), `plan` (read-only mode) `[string] [choices: "default", "auto_edit", "yolo", "plan"]` |
| `--disable-tools` | Hard override for this execution: never expose tools to the model, ignoring the catalogue `tool_capability` (thinker/doer) default. Equivalent to `BARE_AI_NO_TOOLS=true`. `[boolean] [default: false]` |
| `--tools` | Hard override for this execution: expose tools to the model even when the catalogue marks it a thinker. Equivalent to `BARE_AI_NO_TOOLS=false`. `[boolean] [default: false]` |
| `--policy` | Additional policy files or directories to load (comma-separated or multiple `--policy`) `[array]` |
| `--admin-policy` | Additional admin policy files or directories to load (comma-separated or multiple `--admin-policy`) `[array]` |
| `--acp` | Starts the agent in ACP mode `[boolean]` |
| `--experimental-acp` | Starts the agent in ACP mode (**deprecated, use `--acp` instead**) `[boolean]` |
| `--allowed-mcp-server-names` | Allowed MCP server names `[array]` |
| `--allowed-tools` | **[DEPRECATED: Use Policy Engine instead]** Tools that are allowed to run without confirmation `[array]` |
| `-e, --extensions` | A list of extensions to use. If not provided, all extensions are used. `[array]` |
| `-l, --list-extensions` | List all available extensions and exit. `[boolean]` |
| `-r, --resume` | Resume a previous session. Use `"latest"` for most recent or index number (e.g. `--resume 5`) `[string]` |
| `--list-sessions` | List available sessions for the current project and exit. `[boolean]` |
| `--delete-session` | Delete a session by index number (use `--list-sessions` to see available sessions). `[string]` |
| `--include-directories` | Additional directories to include in the workspace (comma-separated or multiple `--include-directories`) `[array]` |
| `--screen-reader` | Enable screen reader mode for accessibility. `[boolean]` |
| `-o, --output-format` | The format of the CLI output. `[string] [choices: "text", "json", "stream-json"]` |
| `--raw-output` | Disable sanitization of model output (e.g. allow ANSI escape sequences). WARNING: This can be a security risk if the model output is untrusted. `[boolean]` |
| `--accept-raw-output-risk` | Suppress the security warning when using `--raw-output`. `[boolean]` |
| `-v, --version` | Show version number `[boolean]` |
| `-h, --help` | Show help `[boolean]` |

### Subcommands

`mcp`:
```
gemini mcp add <name> <commandOrUrl> [args...]  Add a server
gemini mcp remove <name>                        Remove a server
gemini mcp list                                 List all configured MCP servers
gemini mcp enable <name>                        Enable an MCP server
gemini mcp disable <name>                       Disable an MCP server
```

`extensions` (alias `extension`):
```
gemini extensions install <source> [--auto-update] [--pre-release]
gemini extensions uninstall [names..]
gemini extensions list
gemini extensions update [<name>] [--all]
gemini extensions disable [--scope] <name>
gemini extensions enable [--scope] <name>
gemini extensions link <path>
gemini extensions new <path> [template]
gemini extensions validate <path>
gemini extensions config [name] [setting]
```

`skills` (alias `skill`):
```
gemini skills list [--all]
gemini skills enable <name>
gemini skills disable <name> [--scope]
gemini skills install <source> [--scope] [--path]
gemini skills link <path>
gemini skills uninstall <name> [--scope]
```

`hooks` (alias `hook`):
```
gemini hooks migrate   Migrate hooks from Claude Code to Bare AI CLI
```

### Models and providers

Model selection is **catalogue-driven**, not hard-coded. The fleet catalogue is a public HTTP
endpoint (`GET /v1/models`) returning rows of the shape:

```json
{"shortcut":"011","model_id":"deepseek-r1:8b","display_name":"deepseek-r1:8b",
 "provider":"Bare-AI","is_cloud":false,"base_url":"<operator-endpoint>",
 "tool_capability":"thinker","model_tier":"Lite","datacenter_region":"Sovereign"}
```

(`base_url` is redacted here deliberately: on a private deployment it is an internal address. The
field name and its role are accurate; the value is deployment-specific.)

Verified at capture: the catalogue is **paginated** and held **70** models
(`count` is the page size, `total` is the catalogue size, `next_page` drives the walk).

Consequences a reader should know:

- A model is addressed by `shortcut` (a numeric code such as `011`) or by `--model <model_id>`.
- `tool_capability` (`thinker` / `doer`) decides whether tools are exposed by default. `--tools` and
  `--disable-tools` override it per execution.
- `is_cloud` distinguishes a locally-served model from a cloud one. A row with `is_cloud: false` and
  a `base_url` is served on the operator's own network and does not leave it.

Provider credentials are held per **provider**, not per model.

### Configuration and credentials

- Credentials are fetched from **OpenBao / Vault at the moment of use** and held in memory. They are
  never inline in a command, a file, or a commit. On a fleet node the AppRole credentials live in
  `~/.bare-ai/config/vault.env` (mode-restricted, never printed).
- A failed secret lookup is a hard stop. There is no fallback to a cached or environment-supplied
  key.
- Runtime behaviour is configured through environment variables, several of which are documented in
  the tool's own help text (`BARE_AI_NO_TOOLS` is the one the flags map onto). `BARE_AI_ENDPOINT`,
  when set, routes the request to the operator's own OpenAI-shaped endpoint instead of the Google
  SDK path.
- Non-secret user settings live in `~/.gemini/settings.json`, with a workspace-level
  `<cwd>/.gemini/settings.json` layered above it. An untrusted workspace's settings are discarded
  wholesale.

### Session, resume, non-interactive behaviour

- Default mode is **interactive**. `-p/--prompt` switches to a single non-interactive turn.
- Sessions persist per project and can be resumed (`-r/--resume`, including `--resume latest`),
  listed (`--list-sessions`), and deleted (`--delete-session <index>`).
- Machine-readable output is available via `-o json` and `-o stream-json`.

**Prompt size.** The CLI does not enforce a hard prompt-length ceiling; the limits that exist are
warning-level token accounting. What is evidenced:

- `--prompt-file` and `--prompt-interactive-file` exist specifically "for large prompts that exceed
  command-line limits" — i.e. the practical ceiling for `-p` is the operating system's per-argument
  limit, not a CLI constant.
- `MAX_FILE_SIZE_MB = 20` applies to the **file-reading tool**, not to prompts.
- `BARE_AI_CONTEXT_WINDOW` (tokens) overrides the context-window figure used by the warning logic.

Any specific "maximum safe prompt length in characters" figure is operator guidance, not a value the
binary enforces. See UNVERIFIED.

### Known limitations and deprecations

- `--allowed-tools` is **deprecated** in favour of the Policy Engine.
- `--experimental-acp` is **deprecated** in favour of `--acp`.
- The help text reports the program name as `gemini`, inherited from the upstream project.
- `bundle/` is not version-controlled, so a "clean" checkout has no runnable CLI until it is built.
- Direct push protection on the source remote can be bypassed by a credential that carries bypass
  permission; a successful push is not evidence that branch protection was absent.

---

## 2. bare-ai-agent

**What it is.** A shell-and-Python repository that installs and maintains a bare-ai worker node: the
launcher, the constitutions, the tool scripts, and the task-dispatch plumbing. It is *not* an AI
engine — it provisions the environment the CLI runs inside.

### Install / entrypoint

There is **no `package.json` and no CLI entrypoint**. It is a collection of executable shell scripts:

```
./install.sh                     # bootstrap: clone/update the repo, then run the worker installer
./install-pro.sh                 # same, pro tier
./scripts/worker/setup_bare-ai-worker.sh [--fast] [--pro] [--tier <tier>]
./scripts/worker/uninstall_bare-ai.sh
./scripts/worker/verify_launcher.sh
./scripts/lib/dispatch-job.sh
```

`install.sh` clones the repository to `$HOME/bare-ai-agent` (using `$SUDO_USER`'s home when run
under `sudo`) and hands off to `setup_bare-ai-worker.sh --tier free`.

### What the installer produces

- `~/.bare-ai/technical-constitution.md` — the technical constitution (read-only to the agent).
- `~/.bare-ai/role.md` — the functional constitution (the agent's role). **Optional**: if
  `bare-environment.md` is present, the installer deliberately does not create it.
- `~/.bare-ai/bare-environment.md` — node-specific private environment.
- `~/.bare-ai/lib/` — catalog helper (`catalog.sh`) and other runtime helpers.
- A delimited block in `~/.bashrc` defining the `bare()` launcher and the `bare-update`,
  `bare-update-pro`, `bare-uninstall`, `bare-constitution` and `bare-role` commands.

### How the launcher composes a prompt

`bare()` reads the technical constitution path into `BARE_AI_CONSTITUTION` (which the CLI uses as the
system instruction), and writes the role text plus a shield banner into a private per-user file that
is passed to the CLI as the **first user message**. The role text is read from `role.md` only; the
launcher has no `bare-environment.md` fallback, and warns "running with technical only" when
`role.md` is absent.

The launcher also resolves the numeric menu shortcut through the catalogue, sets
`BARE_AI_NO_TOOLS` from the model's `tool_capability`, and exports the Vault secret path for the
selected model.

### Models and providers

Identical mechanism to the CLI: the launcher renders a menu from `~/.bare-ai/lib/catalog.sh` and
resolves the chosen shortcut to `model_id | provider | tool_capability | is_cloud`. It reaches the
same catalogue described in section 1.

### Configuration and credentials

- AppRole credentials in `~/.bare-ai/config/vault.env`; secrets fetched at point of use.
- Node identity and topology live in `~/.bare-ai/bare-environment.md`, which is explicitly private
  and must never be committed, pasted into a ticket, or logged.
- The technical constitution governs tool safety and wins over any instruction in the role or a
  prompt.

### Known limitations

- No versioned release artefact: it is consumed as a git checkout plus a shell installer.
- Shell-alias-based commands exist only in interactive shells. Anything run by cron, systemd or a
  dispatcher must call the underlying script path.
- The installer manages a delimited region of `~/.bashrc`; re-running it is the supported way to
  refresh that region, and hand-editing it drifts from the regenerated version.
- `install.sh` requires a TTY (`< /dev/tty`), so it cannot be driven fully non-interactively as
  shipped.

---

## UNVERIFIED / OPEN

Listed honestly rather than filled in:

1. **A numeric maximum prompt length.** No constant enforces one; the only evidenced ceiling is the
   OS argument limit, and the `*-file` flags exist to sidestep it. Any figure quoted elsewhere is
   operator guidance, not a binary limit.
2. **The complete provider list.** Catalogue rows carry a `provider` field, but the full set of
   provider adapters compiled into the build, and each one's endpoint shape, was not enumerated.
3. **Per-provider credential paths.** The pattern is per-provider rather than per-model, but the
   complete secret-path map was not read end to end.
4. **`--sandbox` behaviour.** The flag exists and is documented; what it isolates in this build was
   not tested.
5. **`--acp` protocol details.** Flag and deprecation are documented; the protocol itself was not
   exercised.
6. **Benchmarks of any kind.** None were run, so none are reported.
7. **`bare-ai-agent` as a "CLI".** It has no command surface of its own; the subcommand list above
   belongs to `bare-ai-cli`. Callers looking for a `bare-ai-agent --help` will not find one.
8. **Windows/macOS support.** Untested; everything here was verified on Linux only.
