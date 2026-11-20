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


class HeartbeatLost(SupervisorError):
    """The claim is no longer ours. Acting on this is not optional.

    status is the HTTP status the gateway answered: 403/409/410 mean the nonce or
    the ownership is gone, which is FATAL - the child must be killed at once and
    must not be allowed to write back. Anything else (a transport error, a 5xx, a
    401 from a bad credential) is transient and is retried up to max_failures.
    """

    FATAL_STATUSES = (403, 409, 410)

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

    @property
    def fatal(self):
        return self.status in self.FATAL_STATUSES


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_cli_json(text, stream="stdout"):
    """Extract the CLI's JSON envelope from one captured stream.

    Measured on CLI d0e4529f0: with -o json the object is preceded by 2-10
    preamble lines (tool-policy notice, YOLO banner, telemetry). A parser that
    assumes line 1 is JSON fails on every run, so scan for the first line that
    is exactly an opening brace and parse from there.

    Measured again 2026-11-19 on the installed bundle: the envelope arrives on
    STDERR and stdout carries only the tool-policy line, so callers should use
    parse_cli_envelope() rather than assume a stream. stream only names the
    source in the error text.

    Raises CliOutputError when no object is present, carrying a short tail so
    the failure is diagnosable from the ticket.
    """
    if not isinstance(text, str):
        raise CliOutputError("%s was %s, not str" % (stream, type(text).__name__))
    lines = text.splitlines()
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
        raise CliOutputError("JSON at %s line %d is malformed: %s"
                             % (stream, first_error[0], first_error[1]))
    tail = " | ".join(lines[-4:])[:300]
    raise CliOutputError("no JSON object in %s; tail: %s" % (stream, tail or "<empty>"))


def parse_cli_envelope(result):
    """Return (envelope, stream_name) for a run_cli result dict.

    stdout is tried first because a CLI that writes the envelope there is
    equally valid; stderr is the stream today's CLI actually uses. Both are
    preserved in the raised error, so a ticket shows what each carried.
    """
    errors = []
    for stream in ("stdout", "stderr"):
        try:
            return parse_cli_json(result.get(stream) or "", stream), stream
        except CliOutputError as exc:
            errors.append(str(exc))
    raise CliOutputError("no JSON envelope in either stream: " + " | ".join(errors))


def format_cli_error(error):
    """One readable line for a CLI envelope's error object, or "".

    Measured shape on a failed turn (installed bundle, 2026-11-19):
        {"session_id": "...", "error": {"type": "TypeError",
                                        "message": "fetch failed", "code": 1}}
    There is no "response" in that envelope, so a response-only excerpt records
    nothing and the failure reason never reaches the state file or the ticket.
    Deliberately total: any shape produces a line rather than raising, because
    this runs on the failure path.
    """
    if not error:
        return ""
    if isinstance(error, str):
        text = error.strip()
        return ("CLI error: " + text[:400]) if text else ""
    if isinstance(error, dict):
        kind = str(error.get("type") or "").strip()
        message = str(error.get("message") or "").strip()
        code = error.get("code")
        head = ": ".join(part for part in (kind, message) if part)
        if code not in (None, ""):
            head = (head + " ") if head else ""
            head = head + "(code %s)" % code
        if not head:
            head = json.dumps(error, sort_keys=True)
        return "CLI error: " + head[:400]
    try:
        return "CLI error: " + json.dumps(error, sort_keys=True)[:400]
    except (TypeError, ValueError):
        return "CLI error: " + str(error)[:400]


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


def drain_streams(proc, chunk=65536, join_timeout=30.0, watch=None,
                  poll_interval=0.5):
    """Read proc.stdout and proc.stderr concurrently and return (out, err) bytes.

    PIPE on both streams deadlocks once a child fills the unread buffer, so each
    stream gets its own thread. Missing streams are tolerated.

    watch, when given, is polled every poll_interval while the pumps run and this
    returns as soon as it says True - the caller is then expected to kill the
    child. Without it a lost claim stays invisible until the child exits, which is
    how attempt 1 kept running beside a re-claim on row 702.
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
    deadline = time.monotonic() + float(join_timeout)
    while True:
        if all(not thread.is_alive() for thread in threads):
            break
        if watch is not None:
            try:
                if watch():
                    break
            except Exception:                        # noqa: BLE001 - a watcher must not abort the run
                pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, remaining))
    for thread in threads:
        thread.join(timeout=0.2)
    return bytes(out), bytes(err)


class Heartbeat:
    """Background lease extension with bounded failure handling.

    interval should be lease/3. call() performs one heartbeat and must raise on
    any refusal (403/409/410, ownership lost). After max_failures consecutive
    failures the run is considered lost: lost_reason is set, the thread stops,
    and the caller is expected to kill the child process group immediately.
    """

    def __init__(self, interval, call, max_failures=3, name="taskbus-heartbeat",
                 on_lost=None, log=None):
        self.interval = float(interval)
        self.call = call
        self.max_failures = int(max_failures)
        self.name = name
        self.lost_reason = None
        self.beats = 0
        self.failures = 0
        self.on_lost = on_lost
        self.log = log
        self._stop = threading.Event()
        self._thread = None

    def _note_failure(self, exc):
        if self.log is None:
            return
        try:
            self.log("heartbeat failure %d/%d (will retry): %s"
                     % (self.failures, self.max_failures, exc))
        except Exception:                        # noqa: BLE001
            pass

    def _declare_lost(self, reason):
        """Set lost_reason and tell the caller AT ONCE, not at the next pass."""
        if self.lost_reason is not None:
            return
        self.lost_reason = reason or "heartbeat lost"
        self._stop.set()
        if self.log is not None:
            try:
                self.log("heartbeat LOST: %s" % self.lost_reason)
            except Exception:                        # noqa: BLE001 - logging must not kill the run
                pass
        if self.on_lost is not None:
            try:
                self.on_lost(self.lost_reason)
            except Exception:                        # noqa: BLE001 - same
                pass

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
            except HeartbeatLost as exc:
                # Ownership or authorisation is GONE: retrying cannot help, and the
                # child must not keep running beside a re-claim.
                if exc.fatal:
                    self._declare_lost(str(exc) or "claim lost (HTTP %s)" % exc.status)
                    return
                self.failures += 1
                self._note_failure(exc)
                if self.failures >= self.max_failures:
                    self._declare_lost(str(exc) or exc.__class__.__name__)
                    return
            except Exception as exc:                 # noqa: BLE001 - transport/unknown
                self.failures += 1
                self._note_failure(exc)
                if self.failures >= self.max_failures:
                    self._declare_lost(str(exc) or exc.__class__.__name__)
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


def _own_pgid():
    """This process's own group id, or None when it cannot be read."""
    try:
        return os.getpgid(0)
    except OSError:
        return None


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _wait_group_gone(proc, pgid, timeout, interval=0.05):
    """True once the group is empty, False if it outlives the budget.

    proc.poll() runs on every pass on purpose: it is a non-blocking waitpid, and
    an UNREAPED ZOMBIE still occupies its process group. Polling killpg alone
    would keep seeing the dead child and report a successful kill as unkillable.
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        try:
            proc.poll()
        except (OSError, ValueError):
            pass
        if not _group_alive(pgid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def _reap(proc):
    """Wait on the leader so no zombie is left behind. Never raises."""
    try:
        proc.wait(timeout=1.0)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass


def _target_pgid(proc):
    """The pgid to signal for proc, or None when there is nothing to signal.

    ROW 711: while the leader exists, os.getpgid gives the real group. Once it has
    been reaped that call raises ProcessLookupError - but the GROUP can still have
    members, and because the child ran in its own session its pgid is still its
    old pid. Falling back to proc.pid is therefore correct, not a guess.
    """
    try:
        return os.getpgid(proc.pid)
    except ProcessLookupError:
        return proc.pid
    except OSError:
        return None


def _terminate_single(proc, grace, kill_wait):
    """Fallback for a child that shares OUR group: pid only, never killpg."""
    try:
        proc.terminate()
    except OSError:
        return "gone"
    try:
        proc.wait(timeout=grace)
        return "terminate"
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except OSError:
        return "gone"
    try:
        proc.wait(timeout=kill_wait)
    except subprocess.TimeoutExpired:
        return "unkillable"
    return "kill"


def terminate_process_group(proc, grace=2.0, kill_wait=5.0):
    """SIGTERM the child's whole process group, then SIGKILL if it lingers.

    The child is spawned with start_new_session=True so its pid is a process
    group leader and the group kill takes its own children with it.

    ROW 711 - A GROUP CAN OUTLIVE ITS LEADER. The CLI spawns the work it waits on
    and can exit (or be reaped during the drain) while grandchildren still live in
    its group. Treating "the leader has exited" as "nothing left to kill" is
    exactly how a sleep 600 survived a claim-loss kill, so the group is resolved
    and signalled even when proc.poll() is not None.

    The target is never OUR OWN process group: killpg on that would take the
    runner and every sibling job with it, so a child that shares our group is
    signalled by pid only.
    """
    if proc is None:
        return "no-process"

    pgid = _target_pgid(proc)
    if pgid is None:
        return "gone"

    own = _own_pgid()
    if own is not None and pgid == own:
        return _terminate_single(proc, grace, kill_wait)

    if not _group_alive(pgid):
        _reap(proc)
        return "already-exited"

    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        _reap(proc)
        return "gone"

    if _wait_group_gone(proc, pgid, grace):
        _reap(proc)
        return "sigterm"

    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        _reap(proc)
        return "gone"

    if _wait_group_gone(proc, pgid, kill_wait):
        _reap(proc)
        return "sigkill"

    _reap(proc)
    return "unkillable"


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
                        on_lost=None,
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
        # on_lost fires the moment ownership is lost, which is what lets the
        # caller kill the child mid-pass instead of waiting for it to exit.
        heartbeat = Heartbeat(interval, heartbeat_call, on_lost=on_lost,
                              log=emit).start()
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
            # ROW 711: the claim died WHILE this pass was running, so the child was
            # killed mid-stream by the drain watch. Stop here: recording the pass
            # and continuing could still reach outcome "verified" - a completion
            # outcome for a run whose ownership is gone.
            if heartbeat is not None and heartbeat.lost_reason:
                outcome = "lost"
                outcome_reason = "heartbeat lost mid-pass: " + heartbeat.lost_reason
                break
            exit_code = int(result.get("exit_code", -1))
            stdout = result.get("stdout") or ""
            response = ""
            session_id = result.get("session_id")
            tools = result.get("tools") or {}
            if not session_id:
                try:
                    envelope, json_stream = parse_cli_envelope(result)
                    response = envelope.get("response") or ""
                    if not response:
                        # A failed turn carries error, not response. Without this the
                        # pass records an empty excerpt and reads as a silent no-op.
                        error_line = format_cli_error(envelope.get("error"))
                        if error_line:
                            response = error_line
                            emit("supervisor: pass %d %s (cli exit %s)"
                                 % (pass_no, error_line, exit_code))
                    session_id = envelope.get("session_id")
                    tools = (envelope.get("stats") or {}).get("tools") or tools
                    if json_stream != "stdout":
                        emit("supervisor: pass %d envelope read from %s "
                             "(stdout carried no JSON)" % (pass_no, json_stream))
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

            claim_still_ours = not (heartbeat is not None and heartbeat.lost_reason)
            if StateFile.all_criteria_evidenced(state) and claim_still_ours:
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
