#!/usr/bin/env python3
"""taskbus_supervisor.py - Stage 0 primitives for the supervised headless runner.

Why this module exists
----------------------
The runner used to make one CLI call per pass and treated a non-zero exit as the
only failure signal. A supervised run needs four things it did not have:

  1. Continuity. bare-ai-cli --resume does NOT restore conversation history on
     this build (session-*.jsonl are 228-byte metadata stubs with no messages),
     so the loop cannot lean on session memory. State is carried in an atomic
     per-row state file and re-briefed into every turn instead.
  2. Evidence over assertion. The model's own VERIFIED_COMPLETE is a claim, not
     proof. Evidence is recorded per acceptance criterion and the loop only
     finishes when every criterion carries evidence, or a budget is exhausted,
     or the agent asks for a human.
  3. Liveness. A run longer than the 900s gateway lease loses its claim. A
     background heartbeat extends it, and the child process group is killed the
     moment ownership is lost so a revoked task cannot keep mutating disk.
  4. Containment. The child writes as the node user. allowed_paths is checked
     after the fact and protected credential paths are refused outright.

Everything here is dependency-free and side-effect-light so it can be unit
tested without a gateway, a database, or a model.
"""
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone

DEFAULT_MAX_PASSES = 5
DEFAULT_MAX_TOOL_ITERATIONS = 32
DEFAULT_MAX_EXECUTION_SECONDS = 7200      # must stay <= the gateway horizon
DEFAULT_LEASE_SECONDS = 900
MAX_SPAWN_DEPTH = 3
PARENT_CHAIN_HARD_CAP = 32

PROTECTED_PATH_MARKERS = (
    "/.bare-ai/config/vault.env",
    "/.bare-ai/config/deploy-credentials",
    "/.bare-ai/model-credentials.env",
)

BLOCKED_PREFIX = "BLOCKED_NEEDS_HUMAN:"


class SupervisorError(Exception):
    """Base error for the supervisor."""


class CliOutputError(SupervisorError):
    """The CLI stdout carried no parseable JSON object."""


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_cli_json(stdout):
    """Extract the CLI's JSON envelope from stdout.

    Measured on CLI d0e4529f0: with -o json the object is preceded by 2-10
    preamble lines (tool-policy notice, YOLO banner, telemetry). A parser that
    assumes line 1 is JSON fails on every run, so scan for the first line that
    is exactly an opening brace and parse from there.

    Raises CliOutputError when no object is present, carrying a short stdout
    tail so the failure is diagnosable from the ticket.
    """
    if not isinstance(stdout, str):
        raise CliOutputError("stdout was %s, not str" % type(stdout).__name__)
    lines = stdout.splitlines()
    first_error = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        # Try every candidate start. A preamble line may legitimately contain a
        # brace without beginning one, and the CLI may emit either pretty-printed
        # or compact single-line JSON.
        try:
            return json.loads("\n".join(lines[index:]))
        except ValueError as exc:
            if first_error is None:
                first_error = (index + 1, exc)
            continue
    if first_error is not None:
        raise CliOutputError("JSON at stdout line %d is malformed: %s" % first_error)
    tail = " | ".join(lines[-4:])[:300]
    raise CliOutputError("no JSON object in stdout; tail: %s" % (tail or "<empty>"))


def spawn_depth_guard(depth, max_depth=MAX_SPAWN_DEPTH):
    """Return (ok, reason). A ticket spawned at or beyond max_depth is refused."""
    try:
        value = int(depth)
    except (TypeError, ValueError):
        return False, "spawn_depth %r is not an integer" % (depth,)
    if value < 0:
        return False, "spawn_depth %d is negative" % value
    if value >= max_depth:
        return False, "spawn_depth %d has reached the limit of %d" % (value, max_depth)
    return True, ""


def _link_ids(value):
    """Normalise a link_row field to a list of integer row ids."""
    out = []
    if not value:
        return out
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        value = [value]
    for item in value:
        if isinstance(item, dict):
            item = item.get("id")
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def depth_from_parent_chain(row_id, fetch_row, max_depth=MAX_SPAWN_DEPTH,
                            hard_cap=PARENT_CHAIN_HARD_CAP):
    """Walk parent_task links upward and return (depth, problem).

    fetch_row(row_id) returns a dict for the row, or None. The chain is the
    authority, not a stored counter, so a worker cannot launder its depth by
    omitting a field. Visited ids are tracked so a cycle terminates instead of
    hanging the poller, and the walk is capped independently of max_depth.
    """
    depth = 0
    seen = set()
    current = int(row_id)
    while True:
        if current in seen:
            return depth, "parent_task chain is cyclic at row %d" % current
        seen.add(current)
        if len(seen) > hard_cap:
            return depth, "parent_task chain exceeded %d links" % hard_cap
        try:
            row = fetch_row(current)
        except Exception as exc:                     # noqa: BLE001 - caller fetch may fail
            return depth, "could not read row %d: %s" % (current, exc)
        if not row:
            return depth, ""
        parents = _link_ids(row.get("parent_task"))
        if not parents:
            return depth, ""
        if len(parents) > 1:
            return depth, "row %d has %d parents; a chain must be linear" % (current, len(parents))
        current = parents[0]
        depth += 1
        if depth > hard_cap:
            return depth, "parent_task chain exceeded %d links" % hard_cap


def _skeleton(row_id, spawn_depth, parent_row_id=None):
    return {
        "row_id": row_id,
        "spawn_depth": spawn_depth,
        "parent_row_id": parent_row_id,
        "created_at": _now(),
        "updated_at": _now(),
        "passes": [],
        "criteria": [],
        "blocked_reason": None,
        "outcome": None,
    }


class StateFile:
    """Atomic per-row state, re-briefed into every turn.

    Writes go to a temporary file in the same directory and are moved into place
    with os.replace, an atomic rename on the same filesystem, so a reader never
    observes a half-written state file.
    """

    def __init__(self, path, row_id=None, spawn_depth=0, parent_row_id=None):
        self.path = path
        self.row_id = row_id
        self.spawn_depth = spawn_depth
        self.parent_row_id = parent_row_id

    def exists(self):
        return os.path.exists(self.path)

    def load(self):
        if not self.exists():
            return _skeleton(self.row_id, self.spawn_depth, self.parent_row_id)
        with open(self.path, encoding="utf-8") as fh:
            try:
                state = json.load(fh)
            except ValueError as exc:
                raise SupervisorError("state file %s is corrupt: %s" % (self.path, exc))
        if not isinstance(state, dict):
            raise SupervisorError("state file %s is not an object" % self.path)
        state.setdefault("passes", [])
        state.setdefault("criteria", [])
        state.setdefault("blocked_reason", None)
        state.setdefault("outcome", None)
        return state

    def save(self, state):
        state["updated_at"] = _now()
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".taskbus-state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return state

    @staticmethod
    def record_pass(state, pass_no, exit_code, session_id=None, response_excerpt="", tools=None):
        state["passes"].append({
            "pass": pass_no,
            "at": _now(),
            "exit_code": exit_code,
            "session_id": session_id,
            "tools": tools or {},
            "response_excerpt": (response_excerpt or "")[:1200],
        })
        return state

    @staticmethod
    def register_criteria(state, criteria):
        known = {c.get("criterion") for c in state["criteria"]}
        for criterion in criteria or []:
            criterion = (criterion or "").strip()
            if criterion and criterion not in known:
                state["criteria"].append({"criterion": criterion, "status": "pending", "evidence": []})
                known.add(criterion)
        return state

    @staticmethod
    def add_evidence(state, criterion, evidence):
        """Attach evidence to a criterion, creating it when unseen."""
        criterion = (criterion or "").strip()
        evidence = (evidence or "").strip()
        if not criterion:
            raise SupervisorError("evidence needs a criterion to attach to")
        if not evidence:
            raise SupervisorError("evidence text is empty")
        for item in state["criteria"]:
            if item.get("criterion") == criterion:
                item.setdefault("evidence", []).append(evidence)
                item["status"] = "evidenced"
                return state
        state["criteria"].append({
            "criterion": criterion,
            "status": "evidenced",
            "evidence": [evidence],
        })
        return state

    @staticmethod
    def all_criteria_evidenced(state):
        criteria = state.get("criteria") or []
        if not criteria:
            return False
        return all(c.get("status") == "evidenced" and c.get("evidence") for c in criteria)

    @staticmethod
    def render_brief(state):
        """The re-brief block prepended to the task prompt on every turn."""
        lines = [
            "## SUPERVISOR STATE (authoritative - re-read every turn)",
            "There is no session memory between turns, so this block is the only continuity.",
            "",
            "Row: %s   pass: %d   spawn_depth: %s" % (
                state.get("row_id"), len(state.get("passes") or []), state.get("spawn_depth")),
            "",
            "ACCEPTANCE CRITERIA - each must end up evidenced or the run goes to a human:",
        ]
        for item in state.get("criteria") or []:
            mark = "EVIDENCED" if item.get("status") == "evidenced" else "PENDING"
            lines.append("  [%s] %s" % (mark, item.get("criterion")))
            for evidence in item.get("evidence") or []:
                lines.append("      evidence: %s" % evidence)
        if state.get("blocked_reason"):
            lines.append("")
            lines.append("BLOCKED: %s" % state["blocked_reason"])
        lines.append("")
        lines.append("Reply with a JSON block giving, per criterion, the concrete evidence you produced.")
        lines.append("If you cannot proceed, reply starting with: " + BLOCKED_PREFIX + " <reason>")
        return "\n".join(lines)


def drain_streams(proc, chunk=65536, join_timeout=30.0):
    """Read proc.stdout and proc.stderr concurrently and return (out, err) bytes.

    PIPE on both streams deadlocks once a child fills the unread buffer, so each
    stream gets its own thread. Missing streams are tolerated.
    """
    out = bytearray()
    err = bytearray()

    def pump(stream, sink):
        if stream is None:
            return
        try:
            while True:
                data = stream.read(chunk)
                if not data:
                    break
                sink.extend(data)
        except (ValueError, OSError):
            return

    threads = [
        threading.Thread(target=pump, args=(getattr(proc, "stdout", None), out), daemon=True),
        threading.Thread(target=pump, args=(getattr(proc, "stderr", None), err), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(join_timeout)
    return bytes(out), bytes(err)


class Heartbeat:
    """Background lease extension with bounded failure handling.

    interval should be lease/3. call() performs one heartbeat and must raise on
    any refusal (403/409/410, ownership lost). After max_failures consecutive
    failures the run is considered lost: lost_reason is set, the thread stops,
    and the caller is expected to kill the child process group immediately.
    """

    def __init__(self, interval, call, max_failures=3, name="taskbus-heartbeat"):
        self.interval = float(interval)
        self.call = call
        self.max_failures = int(max_failures)
        self.name = name
        self.lost_reason = None
        self.beats = 0
        self.failures = 0
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while True:
            if self._stop.wait(self.interval):
                return
            if self._stop.is_set():
                return
            try:
                self.call()
                self.beats += 1
                self.failures = 0
            except Exception as exc:                 # noqa: BLE001 - any refusal means trouble
                self.failures += 1
                if self.failures >= self.max_failures:
                    self.lost_reason = str(exc) or exc.__class__.__name__
                    self._stop.set()
                    return

    @property
    def alive(self):
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self.interval + 1.0)
        return self


def terminate_process_group(proc, grace=15.0, kill_wait=5.0):
    """SIGTERM the whole child process group, then SIGKILL if it lingers.

    The child is spawned with start_new_session=True so its pid is a process
    group leader; killing the group takes any node grandchildren with it, which
    a bare proc.terminate() would leave running.
    """
    if proc is None:
        return "no-process"
    if proc.poll() is not None:
        return "already-exited"
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        try:
            proc.terminate()
        except OSError:
            return "gone"
        return "terminate-fallback"
    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        return "gone"
    try:
        proc.wait(timeout=grace)
        return "sigterm"
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        return "gone"
    try:
        proc.wait(timeout=kill_wait)
    except subprocess.TimeoutExpired:
        return "unkillable"
    return "sigkill"


def enforce_allowed_paths(changed_paths, allowed_paths, protected_markers=PROTECTED_PATH_MARKERS):
    """Return a list of violations for paths a child must not have touched.

    Protected credential paths are refused whatever allowed_paths says: the
    prompt is not a security boundary, so the check has to be outside it.
    """
    violations = []
    resolved_allowed = [os.path.realpath(p) for p in (allowed_paths or []) if p]
    for raw in changed_paths or []:
        if not raw:
            continue
        path = os.path.realpath(str(raw))
        guarded = next((m for m in protected_markers if path.endswith(m) or m in path), None)
        if guarded:
            violations.append({"path": path, "reason": "protected credential path (%s)" % guarded})
            continue
        if not resolved_allowed:
            continue
        if not any(path == base or path.startswith(base + os.sep) for base in resolved_allowed):
            violations.append({"path": path, "reason": "outside allowed_paths"})
    return violations


def reflect_to_role_diary(role_path, diary_path, findings, max_chars=4000,
                          heading="Learned Constraints"):
    """Append a bounded, verified-findings block to the local role and diary.

    Reflection must never invalidate already-verified task output, so every
    failure path returns False instead of raising. The write is atomic so a
    concurrent reader cannot see a half-written role file.
    """
    text = (findings or "").strip()
    if not text:
        return False
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n[truncated at %d chars]" % max_chars
    stamp = _now()
    block = "\n## %s (appended %s by supervised runner)\n\n%s\n" % (heading, stamp, text)
    wrote = False
    for target in (role_path, diary_path):
        if not target:
            continue
        try:
            existing = ""
            if os.path.exists(target):
                with open(target, encoding="utf-8") as fh:
                    existing = fh.read()
            directory = os.path.dirname(os.path.abspath(target)) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".reflect.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(existing + block)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
            wrote = True
        except OSError:
            continue
    return wrote


def run_supervised_loop(row_id, task_text, state_path, run_cli, heartbeat_call=None,
                        allowed_paths=None, criteria=None, spawn_depth=0,
                        lease_seconds=DEFAULT_LEASE_SECONDS, heartbeat_interval=None,
                        max_passes=DEFAULT_MAX_PASSES,
                        max_tool_iterations=DEFAULT_MAX_TOOL_ITERATIONS,
                        max_execution_seconds=DEFAULT_MAX_EXECUTION_SECONDS,
                        clock=time.monotonic, reflect=None, log=None):
    """Drive a supervised multi-pass run. Returns a result dict.

    All side effects are injected so the loop itself is testable:
      run_cli(prompt_text, pass_no) -> dict with exit_code, stdout, stderr,
          session_id, and optionally evidence [{criterion, evidence}] and blocked
      heartbeat_call() -> performs one heartbeat, raising on refusal
      reflect(findings_text) -> bounded reflection, return value ignored

    Outcomes: verified | blocked | budget_exhausted | lost | depth_refused | error.
    Nothing here ever decides Completed - the caller reports HumanReviewNeeded.
    """
    def emit(message):
        if log:
            log(message)

    ok, reason = spawn_depth_guard(spawn_depth)
    if not ok:
        return {"outcome": "depth_refused", "reason": reason, "passes": 0, "state": None}

    if max_execution_seconds > DEFAULT_MAX_EXECUTION_SECONDS:
        max_execution_seconds = DEFAULT_MAX_EXECUTION_SECONDS

    store = StateFile(state_path, row_id=row_id, spawn_depth=spawn_depth)
    state = store.load()
    StateFile.register_criteria(state, criteria or [])
    store.save(state)

    heartbeat = None
    if heartbeat_call is not None:
        interval = heartbeat_interval if heartbeat_interval else max(1.0, lease_seconds / 3.0)
        heartbeat = Heartbeat(interval, heartbeat_call).start()
        emit("supervisor: heartbeat every %.1fs" % interval)
    else:
        emit("supervisor: no heartbeat supplied - long runs may lose the lease")

    started = clock()
    outcome = "budget_exhausted"
    outcome_reason = ""
    passes_done = 0

    try:
        for pass_no in range(1, int(max_passes) + 1):
            if heartbeat is not None and heartbeat.lost_reason:
                outcome = "lost"
                outcome_reason = "heartbeat lost: " + heartbeat.lost_reason
                break
            elapsed = clock() - started
            if elapsed >= max_execution_seconds:
                outcome = "budget_exhausted"
                outcome_reason = "max_execution_seconds %d reached after %.0fs" % (
                    max_execution_seconds, elapsed)
                break

            prompt = task_text + "\n\n" + StateFile.render_brief(state)
            try:
                result = run_cli(prompt, pass_no) or {}
            except Exception as exc:                    # noqa: BLE001 - a crash is a failed pass
                result = {"exit_code": -1, "stdout": "", "stderr": str(exc)}

            passes_done = pass_no
            exit_code = int(result.get("exit_code", -1))
            stdout = result.get("stdout") or ""
            response = ""
            session_id = result.get("session_id")
            tools = result.get("tools") or {}
            if not session_id:
                try:
                    envelope = parse_cli_json(stdout)
                    response = envelope.get("response") or ""
                    session_id = envelope.get("session_id")
                    tools = (envelope.get("stats") or {}).get("tools") or tools
                except CliOutputError as exc:
                    emit("supervisor: pass %d produced no parseable JSON: %s" % (pass_no, exc))

            StateFile.record_pass(state, pass_no, exit_code, session_id, response, tools)
            for item in result.get("evidence") or []:
                try:
                    StateFile.add_evidence(state, item.get("criterion"), item.get("evidence"))
                except SupervisorError as exc:
                    emit("supervisor: rejected evidence on pass %d: %s" % (pass_no, exc))
            if result.get("blocked"):
                state["blocked_reason"] = str(result["blocked"])
            elif BLOCKED_PREFIX in response:
                state["blocked_reason"] = response.split(BLOCKED_PREFIX, 1)[1].strip()[:500]
            store.save(state)

            violations = enforce_allowed_paths(result.get("changed_paths") or [], allowed_paths)
            if violations:
                state["blocked_reason"] = "path violation: " + "; ".join(
                    v["path"] + " (" + v["reason"] + ")" for v in violations[:3])
                store.save(state)
                outcome = "blocked"
                outcome_reason = state["blocked_reason"]
                break

            if state.get("blocked_reason"):
                outcome = "blocked"
                outcome_reason = state["blocked_reason"]
                break

            if StateFile.all_criteria_evidenced(state):
                outcome = "verified"
                outcome_reason = "all criteria evidenced"
                break

            emit("supervisor: pass %d incomplete - criteria still pending" % pass_no)
        else:
            outcome = "budget_exhausted"
            outcome_reason = "max_passes %d exhausted" % int(max_passes)
    finally:
        if heartbeat is not None:
            heartbeat.stop()

    if outcome == "verified" and reflect is not None:
        try:
            reflect(_reflection_text(state))
        except Exception as exc:                        # noqa: BLE001 - never invalidate the run
            emit("supervisor: reflection failed (ignored): %s" % exc)

    state["outcome"] = outcome
    try:
        store.save(state)
    except SupervisorError as exc:
        emit("supervisor: could not persist final state: %s" % exc)
    return {"outcome": outcome, "reason": outcome_reason,
            "passes": passes_done, "state": state, "state_path": state_path}


def _reflection_text(state):
    """Only verified, evidenced lines are worth persisting."""
    lines = ["Outcome: %s (row %s, %d pass(es))" % (
        state.get("outcome"), state.get("row_id"), len(state.get("passes") or []))]
    for item in state.get("criteria") or []:
        if item.get("status") == "evidenced":
            lines.append("- %s: %s" % (item.get("criterion"), "; ".join(item.get("evidence") or [])))
    if state.get("blocked_reason"):
        lines.append("- blocked: %s" % state["blocked_reason"])
    return "\n".join(lines)
