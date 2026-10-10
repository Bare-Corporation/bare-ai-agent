/**
 * @license
 * Copyright 2026 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * council.ts - the Bare-AI Council as a plain client.
 *
 * Universal on purpose. It needs two primitives from its host - a fetch and a
 * timer - and imports nothing, so the React Native app and a Node script share
 * one implementation of this contract. Both are declared structurally below
 * rather than named as globals, because this package compiles without the DOM
 * library: that is what keeps it portable, and it is also why a test can hand
 * over a plain object. The credential and the base URL arrive as parameters: the
 * caller decides where a key comes from, and this file never reads the
 * environment, the filesystem or a keystore.
 *
 * The wire contract is the one the Python client in bare-ai-cli already speaks,
 * so the two can run side by side while the unified feature proves itself:
 *
 *   POST {base}/run            { yaml_config, task } -> { job_id, pipeline_name }
 *   GET  {base}/status/{id}                          -> { status: ... }
 *   GET  {base}/result/{id}                          -> { stage_results: [...] }
 *
 * Every failure comes back as a value, never as a throw. This runs inside a tool
 * call, and the model needs a sentence it can act on, not a stack trace.
 */

export const DEFAULT_COUNCIL_BASE_URL = 'https://api.bare-ai.net/v1/council';

export const DEFAULT_COUNCIL_MODELS: readonly string[] = [
  'claude-sonnet-4-6',
  'deepseek-v4-pro',
];

export const DEFAULT_COUNCIL_ROLES: readonly string[] = [
  'Senior Engineer',
  'Critical Reviewer',
];

export const DEFAULT_COUNCIL_ROUNDS = 2;

/** Ten seconds between polls, and ten minutes of patience: the Python defaults. */
export const DEFAULT_COUNCIL_POLL_INTERVAL_MS = 10_000;
export const DEFAULT_COUNCIL_TIMEOUT_MS = 600_000;

/** Cloudflare answers 403/1010 to a request without this header. */
export const COUNCIL_USER_AGENT = 'bare-ai-council-agent/1.0';

const MAX_ERROR_BODY = 200;

export interface CouncilRunConfig {
  task: string;
  apiKey: string;
  baseUrl?: string;
  models?: readonly string[];
  roles?: readonly string[];
  rounds?: number;
  pollIntervalMs?: number;
  timeoutMs?: number;
  /** Injectable: the device and the CLI both use their runtime's fetch. */
  fetchImpl?: FetchLike;
  /** Injectable so a test does not wait ten seconds per poll. */
  sleep?: (ms: number) => Promise<void>;
  log?: (message: string) => void;
}

export interface CouncilStageSummary {
  stage: string;
  agreed: boolean;
  rounds: number;
  finalOutput: string;
}

export interface CouncilRunResult {
  ok: boolean;
  jobId: string | null;
  /** The Council's answer, when there is one. */
  output: string | null;
  stages: CouncilStageSummary[];
  /** True only when every stage reported agreement. */
  agreed: boolean;
  /** How many status requests were made, for diagnostics. */
  polls: number;
  elapsedMs: number;
  /** The final output, or why there is none. Always safe to show a user. */
  message: string;
  /**
   * The raw /result payload, for a consumer that mirrors the API's own JSON - the
   * Council CLI prints duration_seconds, cost_usd_billed and token_summary straight
   * from it, so they do not have to be modelled here. Untrusted input: narrow it.
   */
  resultPayload?: unknown;
}

const NL = String.fromCharCode(10);

/** The part of a Response this client reads. */
export interface FetchResponseLike {
  ok: boolean;
  status: number;
  text(): Promise<string>;
}

/** The part of fetch this client calls. A host can satisfy it with anything. */
export type FetchLike = (
  url: string,
  init: { method: string; headers: Record<string, string>; body?: string },
) => Promise<FetchResponseLike>;

interface HostPrimitives {
  fetchImpl: FetchLike;
  sleep: (ms: number) => Promise<void>;
}

/**
 * The two things this client needs from its host, resolved once per call. A host
 * that has neither, and injected nothing, gets a sentence rather than a throw.
 */
function hostPrimitives(config: CouncilRunConfig): HostPrimitives | null {
  const globals = globalThis as unknown as { fetch?: unknown; setTimeout?: unknown };
  const fetchImpl =
    config.fetchImpl ??
    (typeof globals.fetch === 'function' ? (globals.fetch as FetchLike) : null);
  const timer = globals.setTimeout;
  const sleep =
    config.sleep ??
    (typeof timer === 'function'
      ? (ms: number): Promise<void> =>
          new Promise((resolve) => {
            (timer as (handler: () => void, ms: number) => unknown)(resolve, ms);
          })
      : null);
  if (fetchImpl === null || sleep === null) return null;
  return { fetchImpl, sleep };
}

/**
 * The pipeline description the endpoint expects. Kept line by line rather than
 * assembled from a template so the shape stays inspectable, and deliberately the
 * same shape the Python client sends: the server parses it.
 */
export function buildCouncilYaml(
  models: readonly string[],
  roles: readonly string[],
  rounds: number,
): string {
  const lines: string[] = [
    'pipeline_name: cli-council',
    'vault_enabled: true',
    'max_debate_rounds: ' + String(rounds),
    'stages:',
    '  - name: analysis',
    '    max_debate_rounds: ' + String(rounds),
    '    team:',
  ];
  models.forEach((model, index) => {
    const role = roles[index] ?? 'Reviewer';
    const signOff =
      index === 0
        ? 'End with [APPROVED] only when fully confident.'
        : 'End with [APPROVED] only when you genuinely agree.';
    lines.push('      - id: agent' + String(index + 1));
    lines.push('        role: ' + role);
    lines.push('        model: ' + model);
    lines.push('        rank: ' + String(index + 1));
    lines.push('        system_prompt: "You are a ' + role + '. ' + signOff + '"');
    lines.push('        temperature: 0.3');
    lines.push('        max_tokens: 2000');
  });
  return lines.join(NL);
}

function oneLine(value: unknown): string {
  return String(value ?? '')
    .split(NL)
    .join(' ')
    .split(String.fromCharCode(13))
    .join(' ')
    .trim();
}

function shortBody(value: unknown): string {
  const text = oneLine(value);
  return text.length > MAX_ERROR_BODY ? text.slice(0, MAX_ERROR_BODY) + ' …' : text;
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value === 'object' && value !== null) return value as Record<string, unknown>;
  return {};
}

function jobIdFrom(payload: unknown): string | null {
  const record = asRecord(payload);
  for (const key of ['job_id', 'jobId', 'id']) {
    const value = record[key];
    if (typeof value === 'string' && value.length > 0) return value;
  }
  return null;
}

function statusFrom(payload: unknown): string {
  const record = asRecord(payload);
  const value = record.status ?? record.state;
  return typeof value === 'string' ? value.toLowerCase() : '';
}

function isComplete(state: string): boolean {
  return state === 'complete' || state === 'completed';
}

function isFailed(state: string): boolean {
  return state === 'failed' || state === 'error' || state === 'failed_permanently';
}

function readStages(payload: unknown): CouncilStageSummary[] {
  const stages = asRecord(payload).stage_results;
  if (!Array.isArray(stages)) return [];
  return stages.map((entry) => {
    const stage = asRecord(entry);
    return {
      stage: oneLine(stage.stage_name) || 'analysis',
      agreed: stage.agreed === true,
      rounds: typeof stage.rounds === 'number' ? stage.rounds : 0,
      finalOutput: typeof stage.final_output === 'string' ? stage.final_output : '',
    };
  });
}

function lastOutput(stages: readonly CouncilStageSummary[]): string | null {
  for (let index = stages.length - 1; index >= 0; index -= 1) {
    const output = stages[index].finalOutput.trim();
    if (output.length > 0) return output;
  }
  return null;
}

interface CallOutcome {
  ok: boolean;
  status: number;
  payload: unknown;
  /** Transport-level failure, as opposed to an HTTP error status. */
  transportError: string | null;
  body: string;
}

/**
 * One request. A 4xx or 5xx comes back as a value rather than a throw, because
 * the caller has to decide whether it is fatal (submitting) or transient (a 524
 * in the middle of a long poll).
 */
async function call(
  url: string,
  apiKey: string,
  fetchImpl: FetchLike,
  body: Record<string, unknown> | null,
): Promise<CallOutcome> {
  const headers: Record<string, string> = {
    'X-API-Key': apiKey,
    'User-Agent': COUNCIL_USER_AGENT,
    'Content-Type': 'application/json',
  };
  try {
    const response = await fetchImpl(url, {
      method: body === null ? 'GET' : 'POST',
      headers,
      ...(body === null ? {} : { body: JSON.stringify(body) }),
    });
    const text = await response.text();
    let payload: unknown = null;
    try {
      payload = text.length === 0 ? null : JSON.parse(text);
    } catch {
      payload = null;
    }
    return {
      ok: response.ok,
      status: response.status,
      payload,
      transportError: null,
      body: text,
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      payload: null,
      transportError: describe(error),
      body: '',
    };
  }
}

function failure(
  message: string,
  extra: Partial<CouncilRunResult> = {},
): CouncilRunResult {
  return {
    ok: false,
    jobId: null,
    output: null,
    stages: [],
    agreed: false,
    polls: 0,
    elapsedMs: 0,
    message,
    ...extra,
  };
}

/**
 * Submit the task to the Council, wait for it, and bring back the final output.
 *
 * The loop is the same one the Python client runs: poll, treat a completed state
 * as the end, and treat a Cloudflare timeout (524) or a dropped connection as
 * something to retry rather than something to report - a single lost request
 * must not kill a job that is still running on the far side.
 */
export async function runCouncilJob(config: CouncilRunConfig): Promise<CouncilRunResult> {
  const startedAt = Date.now();
  const log = config.log ?? (() => undefined);
  const baseUrl = (config.baseUrl ?? DEFAULT_COUNCIL_BASE_URL).replace(/\/+$/, '');
  const models = config.models ?? DEFAULT_COUNCIL_MODELS;
  const roles = config.roles ?? DEFAULT_COUNCIL_ROLES;
  const rounds = config.rounds ?? DEFAULT_COUNCIL_ROUNDS;
  const pollIntervalMs = config.pollIntervalMs ?? DEFAULT_COUNCIL_POLL_INTERVAL_MS;
  const timeoutMs = config.timeoutMs ?? DEFAULT_COUNCIL_TIMEOUT_MS;

  if (config.apiKey.trim().length === 0) {
    return failure('Council escalation is not configured: no API key was supplied.');
  }
  if (config.task.trim().length === 0) {
    return failure('Council escalation needs a task to debate.');
  }
  if (models.length !== roles.length) {
    return failure(
      'Council escalation needs one role per model: ' +
        String(models.length) +
        ' models, ' +
        String(roles.length) +
        ' roles.',
    );
  }

  const host = hostPrimitives(config);
  if (host === null) {
    return failure(
      'Council escalation is not available in this runtime: no fetch or timer was provided.',
    );
  }
  const fetchImpl = host.fetchImpl;

  // Submit.
  log('council: submitting ' + String(models.length) + ' agents to ' + baseUrl);
  const submitted = await call(baseUrl + '/run', config.apiKey, fetchImpl, {
    yaml_config: buildCouncilYaml(models, roles, rounds),
    task: config.task,
  });
  if (submitted.transportError !== null) {
    return failure(
      'Council escalation failed: could not reach ' + baseUrl + ' (' + submitted.transportError + ').',
      { elapsedMs: Date.now() - startedAt },
    );
  }
  if (!submitted.ok) {
    const hint =
      submitted.status === 403 || submitted.status === 1010
        ? ' The Council endpoint rejects requests without the bare-ai-council-agent user agent, which this client sends; check that the key is valid for this base URL.'
        : '';
    return failure(
      'Council escalation failed: HTTP ' +
        String(submitted.status) +
        ' from ' +
        baseUrl +
        '/run: ' +
        shortBody(submitted.body) +
        '.' +
        hint,
      { elapsedMs: Date.now() - startedAt },
    );
  }

  const jobId = jobIdFrom(submitted.payload);
  if (jobId === null) {
    return failure(
      'Council escalation failed: the Council accepted the task but returned no job id.',
      { elapsedMs: Date.now() - startedAt },
    );
  }
  log('council: job ' + jobId + ' accepted');

  // Poll.
  let polls = 0;
  let lastState = '';
  for (;;) {
    const elapsed = Date.now() - startedAt;
    if (elapsed >= timeoutMs) {
      return failure(
        'Council escalation timed out after ' +
          String(Math.round(timeoutMs / 1000)) +
          's waiting for job ' +
          jobId +
          (lastState.length === 0 ? '.' : ' (last status: ' + lastState + ').'),
        { jobId, polls, elapsedMs: elapsed },
      );
    }
    await host.sleep(pollIntervalMs);
    polls += 1;
    const status = await call(baseUrl + '/status/' + jobId, config.apiKey, fetchImpl, null);
    if (status.transportError !== null || status.status === 524) {
      // Transient: keep waiting rather than reporting a job that is probably
      // still running as broken.
      log('council: job ' + jobId + ' status unavailable, retrying');
      continue;
    }
    if (!status.ok) {
      return failure(
        'Council escalation failed: HTTP ' +
          String(status.status) +
          ' from ' +
          baseUrl +
          '/status/' +
          jobId +
          ': ' +
          shortBody(status.body) +
          '.',
        { jobId, polls, elapsedMs: Date.now() - startedAt },
      );
    }
    lastState = statusFrom(status.payload);
    if (isComplete(lastState)) break;
    if (isFailed(lastState)) {
      return failure(
        'Council failed to reach agreement: the job reported ' +
          lastState +
          ': ' +
          shortBody(JSON.stringify(status.payload)) +
          '.',
        { jobId, polls, elapsedMs: Date.now() - startedAt },
      );
    }
  }

  // Collect.
  const result = await call(baseUrl + '/result/' + jobId, config.apiKey, fetchImpl, null);
  if (result.transportError !== null || !result.ok) {
    return failure(
      'Council escalation failed: the job finished but its result could not be read (' +
        (result.transportError ?? 'HTTP ' + String(result.status)) +
        ').',
      { jobId, polls, elapsedMs: Date.now() - startedAt },
    );
  }
  const stages = readStages(result.payload);
  const output = lastOutput(stages);
  const agreed = stages.length > 0 && stages.every((stage) => stage.agreed);
  const elapsedMs = Date.now() - startedAt;
  if (output === null) {
    return failure(
      'Council failed to reach agreement: the job completed without a final output.',
      { jobId, stages, agreed, polls, elapsedMs },
    );
  }
  return {
    ok: true,
    jobId,
    output,
    stages,
    agreed,
    polls,
    elapsedMs,
    message: output,
    resultPayload: result.payload,
  };
}
