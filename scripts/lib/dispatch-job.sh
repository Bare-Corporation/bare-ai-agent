#!/usr/bin/env bash
# dispatch-job.sh — target-side async job runner (M2M Phase 1)
# Ticket: td-44722b916d76 (Implement M2M Async Job Dispatcher Phase 1)
# Liege directive 2026-10-24: record the row's success_criterion in the job state.
# Passed by the poller as an environment variable; empty means "not supplied", which
# keeps this backwards compatible with any caller that does not set it.
CRIT="${SUCCESS_CRITERION:-}"

#
# Deployed to: /home/bare-ai/.bare-ai/lib/dispatch-job.sh on target nodes.
# Invoked by the dispatcher (detached, survives SSH disconnect):
#   ssh <node> 'setsid nohup bash /home/bare-ai/.bare-ai/lib/dispatch-job.sh <uuid> <cmd...> >/dev/null 2>&1 &'
#
# Behavior (per blueprint):
#   1. Accepts UUID + command (a headless CLI invocation or arbitrary shell command).
#   2. Writes ~/.bare-ai/jobs/<uuid>.json state (tmp write then atomic mv).
#   3. Runs command with stdout/stderr -> ~/.bare-ai/jobs/<uuid>.log (secret-scrubbed).
#   4. On exit updates status to done|failed with exit_code and ended_at.
#
# Secret hygiene (blueprint section 4):
#   - Log stream scrubbed via sed BEFORE writing to disk. VALUES are redacted,
#     not just credential names (see the 2026-09-13 rewrite note below).
#   - JSON state never contains secrets; set +x enforced; -x traces suppressed.
#   - On completion log is truncated to last 2000 lines and flagged secret_scrubbed: true.

set -u

UUID="${1:-}"
if [ -z "${UUID}" ]; then
  echo "usage: dispatch-job.sh <uuid> <cmd...>" >&2
  exit 2
fi
shift

JOB_DIR="${HOME}/.bare-ai/jobs"
LOG_PATH="${JOB_DIR}/${UUID}.log"
STATE_PATH="${JOB_DIR}/${UUID}.json"

# Secret scrub regex (POSIX ERE for sed -E). Applied to the log stream.
# Ordered scrub expressions, applied with GNU sed -E. No single quotes appear
# inside any expression, so shell quoting stays simple.
#   1. sk- provider keys: WORD BOUNDARY (\b) plus >=8 chars. The boundary stops "sk-"
#      matching inside "Task-bus" (which previously produced "Ta[REDACTED]").
#   2. Bearer <token>            -> Bearer [REDACTED]
#   3. Authorization: <rest of line> -> Authorization: [REDACTED].
#      Consumes the WHOLE tail, because schemes like "Basic <blob>" have two
#      tokens and a one-token pattern left the credential behind.
#   4. NAME=value / NAME: value  -> keep the name, REDACT THE VALUE.
#      The name is matched as [A-Za-z0-9_]* + fragment so compound names such
#      as BARE_AI_API_KEY match; a leading \b would NOT (underscore is a word
#      char), which is how an API key previously slipped through.
#      This is the important fix: the old single regex replaced only the matched
#      text, which was the credential NAME, so VALUES were written to disk in
#      clear text ("PASSWORD=hunter2" became "[REDACTED]=hunter2"). A probe had
#      FOUR real secret values survive while the job still recorded
#      secret_scrubbed: true - the flag was lying.
SCRUB_SED=(
  -e 's/\bsk-[A-Za-z0-9_-]{8,}/[REDACTED]/g'
  -e 's/(Bearer[[:space:]]+)[^[:space:]]+/\1[REDACTED]/gi'
  -e 's/(Authorization[[:space:]]*:[[:space:]]*).*/\1[REDACTED]/gi'
  -e 's/([A-Za-z0-9_]*(API[_-]?KEY|ACCESS[_-]?KEY|SECRET[_-]?KEY|CLIENT[_-]?SECRET|TOKEN|SECRET|PASSWORD|PASSWD))([[:space:]]*[=:][[:space:]]*)[^[:space:]]+/\1\3[REDACTED]/gi'
)

mkdir -p "${JOB_DIR}"
: > "${LOG_PATH}"

# Resolve node identity (tailscale IPv4 preferred, else first host IP).
NODE="$( { tailscale ip -4 2>/dev/null || true; } | head -1 )"
if [ -z "${NODE}" ]; then
  NODE="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi
[ -n "${NODE}" ] || NODE="unknown"

STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PID="$$"

# --- Initial state (atomic: tmp + mv) ---
STATE_TMP="${STATE_PATH}.tmp.${PID}"
cat > "${STATE_TMP}" <<EOF
{
  "uuid": "${UUID}",
  "node": "${NODE}",
  "status": "running",
  "pid": ${PID},
  "started_at": "${STARTED_AT}",
  "ended_at": null,
  "exit_code": null,
  "log_path": "${LOG_PATH}",
  "success_criterion": null,
  "secret_scrubbed": false
}
EOF
mv -f "${STATE_TMP}" "${STATE_PATH}"
if [ -n "${CRIT}" ]; then
  python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));d["success_criterion"]=sys.argv[2];open(sys.argv[1],"w").write(json.dumps(d,indent=2,sort_keys=True))' "${STATE_PATH}" "${CRIT}" || true
fi

# --- Run the command, scrubbing the log stream, preserving the command's exit code ---
set +x
if [ "$#" -eq 0 ]; then
  echo "dispatch-job: no command supplied" >> "${LOG_PATH}"
  EXIT_CODE=2
else
  # fd 0 pinned to /dev/null: a detached job must not depend on the fd table it
  # inherited from ssh/setsid, and a CLI probing stdin must see EOF, not EBADF.
  "$@" < /dev/null 2>&1 | sed -E "${SCRUB_SED[@]}" >> "${LOG_PATH}"
  EXIT_CODE=${PIPESTATUS[0]}
fi

# --- Final state ---
ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ "${EXIT_CODE}" -eq 0 ]; then
  STATUS="done"
else
  STATUS="failed"
fi

# Truncate log to last 2000 lines (log hygiene), keep tail.
if [ -s "${LOG_PATH}" ]; then
  tail -n 2000 "${LOG_PATH}" > "${LOG_PATH}.tail.${PID}"
  mv -f "${LOG_PATH}.tail.${PID}" "${LOG_PATH}"
fi

STATE_TMP="${STATE_PATH}.tmp.${PID}"
cat > "${STATE_TMP}" <<EOF
{
  "uuid": "${UUID}",
  "node": "${NODE}",
  "status": "${STATUS}",
  "pid": ${PID},
  "started_at": "${STARTED_AT}",
  "ended_at": "${ENDED_AT}",
  "exit_code": ${EXIT_CODE},
  "log_path": "${LOG_PATH}",
  "success_criterion": null,
  "secret_scrubbed": true
}
EOF
mv -f "${STATE_TMP}" "${STATE_PATH}"
if [ -n "${CRIT}" ]; then
  python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));d["success_criterion"]=sys.argv[2];open(sys.argv[1],"w").write(json.dumps(d,indent=2,sort_keys=True))' "${STATE_PATH}" "${CRIT}" || true
fi

exit ${EXIT_CODE}
