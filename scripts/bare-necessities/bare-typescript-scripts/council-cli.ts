#!/usr/bin/env node
/**
 * @license
 * Copyright 2026 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

/*
 * council-cli.ts - the Bare-AI Council CLI in TypeScript.
 *
 * A drop-in mirror of bare-ai-council.py: the same flags, the same credential
 * resolution, the same stdout JSON and the same exit codes (0 reached
 * agreement, 1 error or timeout, 2 deliberated but no agreement). Run it with
 * Node 20 or newer, which strips the types on the fly:
 *
 *   node council-cli.ts "task" --models M1 M2 --roles R1 R2 --rounds 2 --json
 *
 * CLONE PAIRING - see ~/.bare-ai/role.md, ROLE-SPECIFIC RULES item 4. Both
 * files below are kept in byte-identical copies, and an edit to one copy is
 * only finished when the same edit is in every copy and md5sum agrees:
 *   council-cli.ts  this fleet copy, and
 *                   bare-ai-agent/scripts/bare-necessities/bare-typescript-scripts/council-cli.ts
 *   council.ts      bare-nino/packages/nino-edge-tools/src/council.ts (canonical),
 *                   this fleet copy, and the same bare-ai-agent folder
 *
 * The Python client stays the default binary (BARE_COUNCIL_BIN) until this one
 * has been proven side by side against it.
 */

import { readFileSync } from 'node:fs';
import { request as httpRequest, type IncomingMessage } from 'node:http';
import { request as httpsRequest } from 'node:https';
import { homedir } from 'node:os';
import {
  DEFAULT_COUNCIL_BASE_URL,
  DEFAULT_COUNCIL_MODELS,
  DEFAULT_COUNCIL_POLL_INTERVAL_MS,
  DEFAULT_COUNCIL_ROLES,
  DEFAULT_COUNCIL_ROUNDS,
  DEFAULT_COUNCIL_TIMEOUT_MS,
  runCouncilJob,
} from './council.ts';

const NL = String.fromCharCode(10);
const BACKSLASH = String.fromCharCode(92);
const VAULT_SECRET_PATH = 'secret/data/bare-ai-council-v1/config';
const VAULT_ENV_KEYS = ['VAULT_ADDR', 'VAULT_ROLE_ID', 'VAULT_SECRET_ID'];
const DEFAULT_TIMEOUT_SECONDS = DEFAULT_COUNCIL_TIMEOUT_MS / 1000;
const DEFAULT_POLL_SECONDS = DEFAULT_COUNCIL_POLL_INTERVAL_MS / 1000;

interface CliOptions {
  task: string;
  models: string[];
  roles: string[];
  rounds: number;
  timeoutSeconds: number;
  pollSeconds: number;
  json: boolean;
  help: boolean;
}

interface StageView {
  stage: string;
  agreed: boolean;
  rounds: number;
  final_output: string;
}

function fail(message: string): never {
  process.stderr.write(message + NL);
  process.exit(1);
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function optionalNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function optionalString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

/**
 * Escape every non-ASCII code unit the way Python's json.dumps does by default
 * (ensure_ascii=True), so the two clients print the same bytes for the same
 * result. Written without a regular expression on purpose: each UTF-16 code
 * unit, including both halves of an astral pair, becomes its own escape, which
 * is exactly what Python emits.
 */
function escapeNonAscii(text: string): string {
  let out = '';
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    if (code < 128) {
      out += text.charAt(index);
      continue;
    }
    out += BACKSLASH + 'u' + code.toString(16).padStart(4, '0');
  }
  return out;
}

function group(int: number): string {
  const text = String(int);
  let out = '';
  for (let index = 0; index < text.length; index += 1) {
    const fromEnd = text.length - index;
    out += text.charAt(index);
    if (fromEnd > 1 && (fromEnd - 1) % 3 === 0) out += ',';
  }
  return out;
}

const USAGE = [
  'usage: council-cli.ts [-h] [--models MODELS [MODELS ...]]',
  '                      [--roles ROLES [ROLES ...]] [--rounds ROUNDS] [--json]',
  '                      [--timeout TIMEOUT] [--poll POLL]',
  '                      task',
  '',
  'Bare-AI Council API client',
  '',
  'positional arguments:',
  '  task                  Task or question for the Council',
  '',
  'options:',
  '  -h, --help            show this help message and exit',
  '  --models MODELS [MODELS ...]',
  '                        Models to use (space-separated)',
  '  --roles ROLES [ROLES ...]',
  '                        Agent roles (space-separated, must match --models count)',
  '  --rounds ROUNDS       Max debate rounds (default: ' + String(DEFAULT_COUNCIL_ROUNDS) + ')',
  '  --json                Emit structured JSON (for proxy/tool_call integration)',
  '  --timeout TIMEOUT     Max wait seconds (default: ' + String(DEFAULT_TIMEOUT_SECONDS) + ')',
  '  --poll POLL           Poll interval seconds (default: ' + String(DEFAULT_POLL_SECONDS) + ')',
].join(NL);

function parseArgs(argv: string[]): { options?: CliOptions; error?: string } {
  const options: CliOptions = {
    task: '',
    models: [...DEFAULT_COUNCIL_MODELS],
    roles: [...DEFAULT_COUNCIL_ROLES],
    rounds: DEFAULT_COUNCIL_ROUNDS,
    timeoutSeconds: DEFAULT_TIMEOUT_SECONDS,
    pollSeconds: DEFAULT_POLL_SECONDS,
    json: false,
    help: false,
  };
  const positional: string[] = [];
  let index = 0;
  while (index < argv.length) {
    const arg = argv[index];
    if (arg === '-h' || arg === '--help') {
      options.help = true;
      index += 1;
      continue;
    }
    if (arg === '--json') {
      options.json = true;
      index += 1;
      continue;
    }
    if (arg === '--models' || arg === '--roles') {
      const values: string[] = [];
      let cursor = index + 1;
      while (cursor < argv.length && !argv[cursor].startsWith('--')) {
        values.push(argv[cursor]);
        cursor += 1;
      }
      if (values.length === 0) return { error: arg + ' needs at least one value' };
      if (arg === '--models') options.models = values;
      else options.roles = values;
      index = cursor;
      continue;
    }
    if (arg === '--rounds' || arg === '--timeout' || arg === '--poll') {
      const raw = argv[index + 1];
      const value = raw === undefined ? Number.NaN : Number(raw);
      if (!Number.isFinite(value) || value < 0) {
        return { error: arg + ' needs a non-negative number' };
      }
      if (arg === '--rounds') options.rounds = Math.trunc(value);
      else if (arg === '--timeout') options.timeoutSeconds = Math.trunc(value);
      else options.pollSeconds = Math.trunc(value);
      index += 2;
      continue;
    }
    if (arg.startsWith('--')) return { error: 'unrecognised option ' + arg };
    positional.push(arg);
    index += 1;
  }
  options.task = positional.join(' ').trim();
  return { options };
}

/** Env first, then ~/.bare-ai/config/vault.env - the Python client's order. */
function loadVaultEnv(): Record<string, string> {
  const found: Record<string, string> = {};
  for (const key of VAULT_ENV_KEYS) {
    const value = process.env[key];
    if (value) found[key] = value;
  }
  let text = '';
  try {
    text = readFileSync(homedir() + '/.bare-ai/config/vault.env', 'utf8');
  } catch {
    return found;
  }
  for (const line of text.split(NL)) {
    const trimmed = line.trim();
    for (const key of VAULT_ENV_KEYS) {
      if (found[key]) continue;
      for (const prefix of ['export ' + key + '=', key + '=']) {
        if (!trimmed.startsWith(prefix)) continue;
        let value = trimmed.slice(prefix.length).trim();
        while (value.length > 0 && (value.startsWith('"') || value.startsWith("'"))) {
          value = value.slice(1);
        }
        while (value.length > 0 && (value.endsWith('"') || value.endsWith("'"))) {
          value = value.slice(0, -1);
        }
        found[key] = value;
      }
    }
  }
  return found;
}

/**
 * One JSON call. The TLS check is disabled for the Vault leg only, mirroring
 * the permissive SSL context in the Python client: the fleet Vault presents a
 * self-signed certificate. The Council API leg goes through the shared client,
 * which uses the runtime fetch with verification on.
 */
function vaultJson(
  url: string,
  init: { method: string; headers?: Record<string, string>; body?: string },
): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const handler = (response: IncomingMessage) => {
      let text = '';
      response.setEncoding('utf8');
      response.on('data', (chunk: string) => {
        text += chunk;
      });
      response.on('end', () => resolve({ status: response.statusCode ?? 0, body: text }));
    };
    const request = url.startsWith('https:')
      ? httpsRequest(
          url,
          { method: init.method, headers: init.headers ?? {}, rejectUnauthorized: false },
          handler,
        )
      : httpRequest(url, { method: init.method, headers: init.headers ?? {} }, handler);
    request.on('error', (error: Error) => reject(error));
    if (init.body !== undefined) request.write(init.body);
    request.end();
  });
}

async function resolveCouncilConfig(): Promise<{ apiKey: string; baseUrl: string }> {
  const envKey = process.env['BARE_COUNCIL_API_KEY'];
  if (envKey) {
    return {
      apiKey: envKey,
      baseUrl: process.env['BARE_COUNCIL_URL'] ?? DEFAULT_COUNCIL_BASE_URL,
    };
  }

  const creds = loadVaultEnv();
  const missing = VAULT_ENV_KEYS.filter((key) => !creds[key]);
  if (missing.length > 0) {
    fail(
      '[council] ERROR: Missing Vault credentials: ' +
        missing.join(', ') +
        NL +
        'Ensure ~/.bare-ai/config/vault.env contains VAULT_ADDR, VAULT_ROLE_ID, and VAULT_SECRET_ID.',
    );
  }
  const addr = creds['VAULT_ADDR'];

  let token = '';
  try {
    const login = await vaultJson(addr + '/v1/auth/approle/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        role_id: creds['VAULT_ROLE_ID'],
        secret_id: creds['VAULT_SECRET_ID'],
      }),
    });
    token = optionalString(asRecord(asRecord(JSON.parse(login.body))['auth'])['client_token']);
  } catch (error: unknown) {
    fail('[council] Vault AppRole login failed: ' + describe(error));
  }
  if (token.length === 0) fail('[council] Vault AppRole login failed: no client token in the reply.');

  let secret: { status: number; body: string };
  try {
    secret = await vaultJson(addr + '/v1/' + VAULT_SECRET_PATH, {
      method: 'GET',
      headers: { 'X-Vault-Token': token },
    });
  } catch (error: unknown) {
    fail('[council] Vault secret fetch failed: ' + describe(error));
  }

  if (secret.status === 404) {
    fail(
      '[council] ERROR: ' +
        VAULT_SECRET_PATH +
        ' not found in Vault.' +
        NL +
        'Purchase a key at https://bare-ai.net then run:' +
        NL +
        "  vault kv put secret/bare-ai-council-v1/config" +
        NL +
        "    api_key='<your-key>'" +
        NL +
        "    base_url='https://api.bare-ai.net/v1/council'",
    );
  }
  if (secret.status < 200 || secret.status >= 300) {
    fail('[council] Vault secret fetch failed: HTTP ' + String(secret.status));
  }

  const data = asRecord(asRecord(asRecord(JSON.parse(secret.body))['data'])['data']);
  const apiKey = optionalString(data['api_key']);
  let baseUrl = optionalString(data['base_url']);
  if (baseUrl.length === 0) baseUrl = DEFAULT_COUNCIL_BASE_URL;
  // Vault may hold https://api.bare-ai.net/v1 without the /council suffix; a
  // broken URL must not reach the API, so fall back to the default.
  if (!baseUrl.endsWith('/v1/council')) baseUrl = DEFAULT_COUNCIL_BASE_URL;

  if (apiKey.length === 0 || apiKey === 'enterYourKey') {
    fail(
      '[council] ERROR: Council API key not configured in Vault.' +
        NL +
        'Purchase a key at https://bare-ai.net then run:' +
        NL +
        "  vault kv put secret/bare-ai-council-v1/config" +
        NL +
        "    api_key='<your-key>'" +
        NL +
        "    base_url='https://api.bare-ai.net/v1/council'",
    );
  }
  return { apiKey, baseUrl };
}

function readStages(payload: Record<string, unknown>): StageView[] {
  const raw = payload['stage_results'];
  if (!Array.isArray(raw)) return [];
  const stages: StageView[] = [];
  for (const item of raw) {
    const stage = asRecord(item);
    stages.push({
      stage: optionalString(stage['stage_name']),
      agreed: stage['agreed'] === true,
      rounds: typeof stage['rounds'] === 'number' ? stage['rounds'] : 0,
      final_output: optionalString(stage['final_output']),
    });
  }
  return stages;
}

function renderJson(
  jobId: string | null,
  payload: Record<string, unknown>,
  stages: StageView[],
  agreed: boolean,
): string {
  const tokenSummary = payload['token_summary'] ?? {};
  const document = {
    job_id: jobId,
    duration_seconds: optionalNumber(payload['duration_seconds']),
    cost_usd: optionalNumber(payload['cost_usd_billed']),
    token_summary: tokenSummary,
    stages: stages.map((stage) => ({
      stage: stage.stage,
      agreed: stage.agreed,
      rounds: stage.rounds,
      final_output: stage.final_output,
    })),
    agreed,
  };
  return escapeNonAscii(JSON.stringify(document, null, 2));
}

function renderText(
  jobId: string | null,
  payload: Record<string, unknown>,
  stages: StageView[],
  agreed: boolean,
): string {
  const rule = '='.repeat(60);
  const duration = optionalNumber(payload['duration_seconds']) ?? 0;
  const cost = optionalNumber(payload['cost_usd_billed']) ?? 0;
  const lines: string[] = [
    NL + rule,
    '  BARE-AI COUNCIL RESULT',
    '  Job: ' + String(jobId),
    '  Duration: ' + duration.toFixed(1) + 's  |  Cost: $' + cost.toFixed(4),
  ];
  const summary = asRecord(payload['token_summary']);
  for (const model of Object.keys(summary)) {
    const entry = asRecord(summary[model]);
    const total = typeof entry['total'] === 'number' ? entry['total'] : 0;
    const prompt = typeof entry['prompt'] === 'number' ? entry['prompt'] : 0;
    const completion = typeof entry['completion'] === 'number' ? entry['completion'] : 0;
    lines.push(
      '  ' +
        model +
        ': ' +
        group(total) +
        ' tokens (' +
        group(prompt) +
        ' prompt + ' +
        group(completion) +
        ' completion)',
    );
  }
  lines.push(rule + NL);
  for (const stage of stages) {
    lines.push(
      (stage.agreed ? 'OK' : 'WARN') +
        ' Stage: ' +
        stage.stage +
        ' | Agreed: ' +
        String(stage.agreed) +
        ' | Rounds: ' +
        String(stage.rounds),
      '',
    );
    lines.push(stage.final_output.length > 0 ? stage.final_output : '(no output)');
    lines.push('');
  }
  if (!agreed) {
    lines.push('Council did NOT reach full agreement - review output carefully.');
  }
  return lines.join(NL);
}

async function main(): Promise<number> {
  const parsed = parseArgs(process.argv.slice(2));
  if (parsed.options === undefined) {
    process.stderr.write('[council] ' + String(parsed.error) + NL + USAGE + NL);
    return 1;
  }
  const options = parsed.options;
  if (options.help) {
    process.stdout.write(USAGE + NL);
    return 0;
  }
  if (options.task.length === 0) {
    process.stderr.write('[council] a task is required' + NL + USAGE + NL);
    return 1;
  }
  if (options.models.length !== options.roles.length) {
    process.stderr.write('[council] --models and --roles must have the same count' + NL);
    return 1;
  }

  const config = await resolveCouncilConfig();

  process.stdout.write(
    '[council] Submitting to Bare-AI Council (' + options.models.join(', ') + ') ...' + NL,
  );

  const result = await runCouncilJob({
    task: options.task,
    apiKey: config.apiKey,
    baseUrl: config.baseUrl,
    models: options.models,
    roles: options.roles,
    rounds: options.rounds,
    pollIntervalMs: options.pollSeconds * 1000,
    timeoutMs: options.timeoutSeconds * 1000,
    log: (message: string) => {
      const text = message.startsWith('council: ') ? message.slice(9) : message;
      process.stdout.write('[council] ' + text + NL);
    },
  });

  const payload = asRecord(result.resultPayload);
  if (!Array.isArray(payload['stage_results'])) {
    // No completed result to mirror: a failed job, a timeout or an unreadable
    // result. The Python client exits 1 on every one of those paths.
    process.stderr.write('[council] ' + result.message + NL);
    return 1;
  }

  const stages = readStages(payload);
  let agreed = true;
  for (const stage of stages) {
    if (!stage.agreed) agreed = false;
  }

  if (options.json) {
    process.stdout.write(renderJson(result.jobId, payload, stages, agreed) + NL);
  } else {
    process.stdout.write(renderText(result.jobId, payload, stages, agreed) + NL);
  }
  return agreed ? 0 : 2;
}

main()
  .then((code) => {
    process.exit(code);
  })
  .catch((error: unknown) => {
    process.stderr.write('[council] ' + describe(error) + NL);
    process.exit(1);
  });
