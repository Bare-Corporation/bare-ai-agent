#!/usr/bin/env python3
"""test_runner_profile_gate.py - behavioural tests for the T4 runner gate.

The runner file has dashes in its name, so it cannot be imported normally; it is
loaded by path. Every test clears the env vars the gate reads, so the suite is
hermetic and does not depend on the ambient shell.

The important cases are the NEGATIVE ones: a corrupted snapshot must be refused,
and a profile-bound row with no snapshot must be refused rather than silently
falling back to the node's local role.
"""
import contextlib
import importlib.util
import io
import shutil
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RUNNER = os.path.join(HERE, "taskbus-agent-job.py")
FIXTURE = os.path.join(HERE, "tests", "fixtures", "t4-claim-payload.json")

ENV_KEYS = ("TASKBUS_CLAIM_PAYLOAD", "TASKBUS_CLAIM_JSON",
            "TASKBUS_PROFILE_REQUIRED")


def load_runner():
    spec = importlib.util.spec_from_file_location("taskbus_agent_job", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_fixture():
    with io.open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


class GateTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        self._tmp = []

    def tearDown(self):
        for k in ENV_KEYS:
            os.environ.pop(k, None)
            if self._saved.get(k) is not None:
                os.environ[k] = self._saved[k]
        for p in self._tmp:
            try:
                os.unlink(p)
            except OSError:
                pass

    def write_payload(self, payload):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        self._tmp.append(path)
        return path


class TestPositive(GateTestBase):
    def test_valid_claim_payload_verifies(self):
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = FIXTURE
        ctx = self.runner.profile_gate()
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["slug"], "example-profile")
        self.assertTrue(all(c["ok"] for c in ctx["checks"]))
        self.assertGreater(ctx["markdown_bytes"], 1000)

    def test_inline_json_is_read(self):
        os.environ["TASKBUS_CLAIM_JSON"] = json.dumps(load_fixture())
        ctx = self.runner.profile_gate()
        self.assertIsNotNone(ctx)
        self.assertTrue(ctx["verified"])

    def test_audit_stamp_is_produced(self):
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = FIXTURE
        ctx = self.runner.profile_gate()
        stamp = self.runner.profile_gate_stamp(ctx)
        self.assertIn("execution_profile verified", stamp)
        self.assertIn(ctx["profile_hash"][:16], stamp)

    def test_gate_json_reports_applied(self):
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = FIXTURE
        ctx = self.runner.profile_gate()
        line = self.runner.profile_gate_json(ctx=ctx)
        self.assertTrue(line.startswith("PROFILE_GATE_JSON: "))
        body = json.loads(line.split(":", 1)[1].strip())
        self.assertTrue(body["ok"])
        self.assertTrue(body["applied"])
        self.assertEqual(body["slug"], "example-profile")


class TestFailClosed(GateTestBase):
    def test_corrupted_markdown_is_refused(self):
        payload = load_fixture()
        block = payload["execution_profile"]
        block["snapshot"]["role"]["content_markdown"] += " INJECTED"
        # recompute profile_hash so ONLY the role hash can catch it
        pc = importlib.util.spec_from_file_location(
            "pc_for_test", os.path.join(HERE, "profile_context.py"))
        mod = importlib.util.module_from_spec(pc)
        pc.loader.exec_module(mod)
        block["profile_hash"] = mod.sha256_hex_json(block["snapshot"])
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = self.write_payload(payload)
        with self.assertRaises(self.runner.ProfileGateFailure) as cm:
            self.runner.profile_gate()
        self.assertEqual(cm.exception.reason, "profile_hash_mismatch")
        self.assertEqual(cm.exception.which, "role_content_sha256")

    def test_unsupported_snapshot_version_is_unusable(self):
        payload = load_fixture()
        payload["execution_profile"]["snapshot"]["snapshot_version"] = 2
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = self.write_payload(payload)
        with self.assertRaises(self.runner.ProfileGateUnusable):
            self.runner.profile_gate()

    def test_missing_block_is_unusable(self):
        payload = load_fixture()
        payload.pop("execution_profile", None)
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = self.write_payload(payload)
        with self.assertRaises(self.runner.ProfileGateUnusable):
            self.runner.profile_gate()

    def test_unreadable_payload_is_unusable(self):
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = "/nonexistent/claim.json"
        with self.assertRaises(self.runner.ProfileGateUnusable):
            self.runner.profile_gate()

    def test_absent_payload_when_required_is_refused(self):
        """A profile-bound row must NOT silently fall back to the local role."""
        with self.assertRaises(self.runner.ProfileGateUnusable) as cm:
            self.runner.profile_gate(required=True)
        self.assertEqual(cm.exception.reason, "profile_snapshot_unusable")
        self.assertEqual(cm.exception.which, "snapshot_absent")

    def test_failure_json_is_not_ok(self):
        try:
            self.runner.profile_gate(required=True)
        except self.runner.ProfileGateFailure as exc:
            line = self.runner.profile_gate_json(failure=exc)
            body = json.loads(line.split(":", 1)[1].strip())
            self.assertFalse(body["ok"])
            self.assertEqual(body["reason"], "profile_snapshot_unusable")
        else:
            self.fail("gate did not refuse an absent required payload")


class TestInertWhenUnbound(GateTestBase):
    def test_absent_payload_not_required_is_inert(self):
        self.assertIsNone(self.runner.profile_gate())

    def test_absent_payload_report_is_not_applied(self):
        line = self.runner.profile_gate_json(ctx=None)
        body = json.loads(line.split(":", 1)[1].strip())
        self.assertTrue(body["ok"])
        self.assertFalse(body["applied"])


class ProfileRoutePrecedenceTests(unittest.TestCase):
    """Row 697: a VERIFIED snapshot's route outranks the node's local profile.env.

    .16's snapshot routed deepseek-v4-flash|deepseek-cn while profile.env said
    claude-sonnet-4-6 at api.anthropic.com, and the profile won - so a DeepSeek key
    was posted to Anthropic. These tests assert on the child env dict itself, plus
    the log line an operator reads on the ticket.
    """

    # The pair that produced the 401, verbatim.
    PROFILE_ENV = {"BARE_AI_ENDPOINT": "https://api.anthropic.com/v1/messages",
                   "BARE_AI_MODEL": "claude-sonnet-4-6"}
    DEEPSEEK_ROUTE = {"model_id": "deepseek-v4-flash|deepseek-cn", "base_url": "",
                      "source": "execution_config.routed_model"}

    @classmethod
    def setUpClass(cls):
        cls.job = load_runner()

    def setUp(self):
        self._real_central = self.job._central_models
        self.out = ""

    def tearDown(self):
        self.job._central_models = self._real_central

    def _catalogue(self, models):
        self.job._central_models = lambda: ({"models": models}, "")

    def _run(self, ctx, env):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = self.job.apply_profile_route(ctx, env)
        self.out = buf.getvalue()
        return result

    def test_verified_profile_route_beats_the_node_profile(self):
        self._catalogue([{"shortcut": "12",
                          "model_id": "deepseek-v4-flash|deepseek-cn",
                          "base_url": "https://api.deepseek.com"}])
        env = dict(self.PROFILE_ENV)
        applied = self._run({"route": dict(self.DEEPSEEK_ROUTE)}, env)
        self.assertEqual(env["BARE_AI_MODEL"], "deepseek-v4-flash")
        self.assertEqual(env["BARE_AI_ENDPOINT"],
                         "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(applied["endpoint"], env["BARE_AI_ENDPOINT"])
        self.assertIn("route source = execution_profile", self.out)
        self.assertIn("overrode the node profile", self.out)

    def test_without_a_snapshot_route_the_profile_stands(self):
        # The fallback half of the requirement: profile.env is a fallback, and
        # stays one when the snapshot names no route.
        env = dict(self.PROFILE_ENV)
        self.assertIsNone(self._run({"route": None}, env))
        self.assertEqual(env["BARE_AI_MODEL"], "claude-sonnet-4-6")
        self.assertEqual(env["BARE_AI_ENDPOINT"],
                         "https://api.anthropic.com/v1/messages")
        self.assertEqual(self.out, "")

    def test_snapshot_base_url_needs_no_catalogue(self):
        def boom():
            raise AssertionError("the catalogue must not be consulted")
        self.job._central_models = boom
        env = {"BARE_AI_ENDPOINT": "https://api.anthropic.com/v1/messages",
               "BARE_AI_MODEL": "claude-sonnet-4-6"}
        self._run({"route": {"model_id": "some-model|cn",
                             "base_url": "https://api.example.com/v1"}}, env)
        self.assertEqual(env["BARE_AI_MODEL"], "some-model")
        self.assertEqual(env["BARE_AI_ENDPOINT"],
                         "https://api.example.com/v1/chat/completions")

    def test_catalogue_match_ignores_the_provider_suffix(self):
        # The list may carry the bare id while the snapshot carries the piped one.
        self._catalogue([{"shortcut": "12", "model_id": "deepseek-v4-flash",
                          "base_url": "https://api.deepseek.com"}])
        env = dict(self.PROFILE_ENV)
        self._run({"route": dict(self.DEEPSEEK_ROUTE)}, env)
        self.assertEqual(env["BARE_AI_MODEL"], "deepseek-v4-flash")

    def test_an_unresolvable_route_keeps_the_profile_and_warns(self):
        self._catalogue([])                       # the routed model is absent
        env = dict(self.PROFILE_ENV)
        self.assertIsNone(self._run({"route": {"model_id": "not-in-the-list"}}, env))
        self.assertEqual(env["BARE_AI_MODEL"], "claude-sonnet-4-6")
        self.assertEqual(env["BARE_AI_ENDPOINT"],
                         "https://api.anthropic.com/v1/messages")
        self.assertIn("ROUTER WARNING", self.out)


class RowStampedModelRouteTests(unittest.TestCase):
    """Row 698: the model is stamped on the ROW, not inside the snapshot.

    .16's verified Execution Profile carried only policy knobs in execution_config
    (routing_strategy, exclude_providers, ceiling, max_attempts,
    require_human_review, role_alias, role hash), so profile_route() found nothing
    and ~/.bare-ai/profile.env won again - a DeepSeek key posted to Anthropic. The
    poller now exports the row's model_catalog as TASKBUS_ROW_MODEL and the runner
    resolves the catalogue route from it.
    """

    PROFILE_ENV = {"BARE_AI_ENDPOINT": "https://api.anthropic.com/v1/messages",
                   "BARE_AI_MODEL": "claude-sonnet-4-6"}
    # The shape .4 measured on the live row 698 claim payload.
    VERIFIED_CTX_NO_ROUTE = {
        "verified": True,
        "slug": "canary-profile",
        "version": 2,
        "route": None,
        "router": {"routing_strategy": "cheapest_flash", "exclude_providers": []},
    }
    CATALOGUE = [{"shortcut": "38", "model_id": "deepseek-v4-flash|deepseek-cn",
                  "base_url": "https://api.deepseek.com"}]

    @classmethod
    def setUpClass(cls):
        cls.job = load_runner()

    def setUp(self):
        self._real_central = self.job._central_models
        self.out = ""

    def tearDown(self):
        self.job._central_models = self._real_central

    def _catalogue(self, models):
        self.job._central_models = lambda: ({"models": models}, "")

    def _run(self, ctx, env, row_model=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = self.job.apply_profile_route(ctx, env, row_model=row_model)
        self.out = buf.getvalue()
        return result

    def test_row_model_resolves_when_the_snapshot_has_no_route(self):
        self._catalogue(self.CATALOGUE)
        env = dict(self.PROFILE_ENV)
        applied = self._run(self.VERIFIED_CTX_NO_ROUTE, env, row_model="deepseek-v4-flash")
        self.assertEqual(env["BARE_AI_MODEL"], "deepseek-v4-flash")
        self.assertEqual(env["BARE_AI_ENDPOINT"],
                         "https://api.deepseek.com/v1/chat/completions")
        self.assertIn("route source = execution_profile", self.out)
        self.assertIn("row.model_catalog", self.out)
        self.assertIn("overrode the node profile", self.out)
        self.assertEqual(applied["model"], "deepseek-v4-flash")

    def test_row_model_accepts_the_piped_catalogue_form(self):
        # The catalogue lists the pipe form; the row may carry either.
        self._catalogue(self.CATALOGUE)
        env = dict(self.PROFILE_ENV)
        self._run(self.VERIFIED_CTX_NO_ROUTE, env, row_model="deepseek-v4-flash|deepseek-cn")
        self.assertEqual(env["BARE_AI_MODEL"], "deepseek-v4-flash")
        self.assertEqual(env["BARE_AI_ENDPOINT"],
                         "https://api.deepseek.com/v1/chat/completions")

    def test_snapshot_route_still_outranks_the_row_model(self):
        self._catalogue(self.CATALOGUE)
        ctx = dict(self.VERIFIED_CTX_NO_ROUTE)
        ctx["route"] = {"model_id": "qwen2.5-coder",
                        "base_url": "https://api.example.com/v1",
                        "source": "execution_config.routed_model"}
        env = dict(self.PROFILE_ENV)
        self._run(ctx, env, row_model="deepseek-v4-flash")
        self.assertEqual(env["BARE_AI_MODEL"], "qwen2.5-coder")
        self.assertEqual(env["BARE_AI_ENDPOINT"],
                         "https://api.example.com/v1/chat/completions")

    def test_unverified_context_does_not_use_the_row_model(self):
        # Control: an unverified row value must not overrule local configuration.
        self._catalogue(self.CATALOGUE)
        env = dict(self.PROFILE_ENV)
        ctx = dict(self.VERIFIED_CTX_NO_ROUTE)
        ctx["verified"] = False
        self.assertIsNone(self._run(ctx, env, row_model="deepseek-v4-flash"))
        self.assertEqual(env["BARE_AI_MODEL"], "claude-sonnet-4-6")

    def test_no_verified_profile_does_not_use_the_row_model(self):
        # Control: no snapshot at all means profile.env is authoritative.
        self._catalogue(self.CATALOGUE)
        env = dict(self.PROFILE_ENV)
        self.assertIsNone(self._run(None, env, row_model="deepseek-v4-flash"))
        self.assertEqual(env["BARE_AI_MODEL"], "claude-sonnet-4-6")
        self.assertEqual(self.out, "")

    def test_an_unknown_row_model_leaves_the_profile_alone(self):
        self._catalogue([])                      # the row's model is not in the list
        env = dict(self.PROFILE_ENV)
        self.assertIsNone(self._run(self.VERIFIED_CTX_NO_ROUTE, env,
                                    row_model="not-in-the-list"))
        self.assertEqual(env["BARE_AI_MODEL"], "claude-sonnet-4-6")
        self.assertIn("ROUTER WARNING", self.out)


class FullMainRouteResolutionTests(unittest.TestCase):
    """END-TO-END: main() must route the child env from the verified profile.

    Rows 697, 698 and 701 all looked fixed in unit tests and still shipped
    profile.env to the model. The reason was a mis-wired context: main() resolved
    the route from load_claim_snapshot() (TASKBUS_CLAIM_FILE / _JSON), while the
    poller ships TASKBUS_CLAIM_PAYLOAD, so that context was None on every real
    dispatch. This test drives main() itself, so a mis-wiring cannot pass again.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="main-route-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".bare-ai"), exist_ok=True)
        # A node profile that would win if the route were not resolved:
        with open(os.path.join(self.home, ".bare-ai", "profile.env"), "w",
                  encoding="utf-8") as fh:
            fh.write("export BARE_AI_PROFILE=pro" + chr(10))
            fh.write("export BARE_AI_ENDPOINT=https://api.anthropic.com/v1/messages" + chr(10))
            fh.write("export BARE_AI_MODEL=claude-sonnet-4-6" + chr(10))
            fh.write("export BARE_AI_API_KEY=not-a-real-key" + chr(10))
        # A real, hash-valid claim payload: execution_config is POLICY ONLY.
        payload = load_fixture()
        self.payload_path = os.path.join(self.tmp, "claim.json")
        with open(self.payload_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        self.prompt_path = os.path.join(self.tmp, "task.prompt")
        with open(self.prompt_path, "w", encoding="utf-8") as fh:
            fh.write("TASK-BUS JOB - row 701 (dispatched by the task-bus poller)" + chr(10))
            fh.write("SHORT DESCRIPTION: canary" + chr(10))

        self.job = load_runner()
        self._saved_env = {}
        for key in ("TASKBUS_CLAIM_PAYLOAD", "TASKBUS_CLAIM_FILE", "TASKBUS_CLAIM_JSON",
                    "TASKBUS_PROFILE_REQUIRED", "TASKBUS_ROW_MODEL", "TASKBUS_CRITERION_JSON",
                    "TASKBUS_AUDIT_FILE"):
            self._saved_env[key] = os.environ.pop(key, None)
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = self.payload_path
        os.environ["TASKBUS_PROFILE_REQUIRED"] = "1"
        os.environ["TASKBUS_ROW_MODEL"] = "deepseek-v4-flash"

        self._originals = {name: getattr(self.job, name)
                           for name in ("HOME", "_central_models",
                                        "_supervisor_enabled", "vault_api_key")}
        self.job.HOME = self.home
        self.job._central_models = lambda: (
            {"models": [{"shortcut": "38", "model_id": "deepseek-v4-flash|deepseek-cn",
                         "base_url": "https://api.deepseek.com"}]}, "")
        self.job._supervisor_enabled = lambda: False
        self.job.vault_api_key = lambda: (None, "not consulted in this test")
        self.spawned = []

        class FakeCompleted:
            returncode = 0
            stdout = b""

            def __init__(self):
                self.stderr = b""

        def fake_run(cmd, **kwargs):
            self.spawned.append({"cmd": cmd, "env": dict(kwargs.get("env") or {})})
            return FakeCompleted()

        # Patch ONLY run: main() also uses subprocess.DEVNULL / PIPE / STDOUT and
        # catches subprocess.TimeoutExpired, so replacing the module breaks it.
        self._real_run = self.job.subprocess.run
        self.job.subprocess.run = fake_run

    def tearDown(self):
        self.job.subprocess.run = self._real_run
        for name in ("HOME", "_central_models", "_supervisor_enabled", "vault_api_key"):
            setattr(self.job, name, self._originals[name])
        for key, value in self._saved_env.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_main(self):
        argv = list(sys.argv)
        sys.argv = ["taskbus-agent-job.py", self.prompt_path]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                self.job.main()
        finally:
            sys.argv = argv
        return buf.getvalue()

    def test_main_routes_the_child_env_from_the_verified_profile(self):
        out = self._run_main()
        self.assertTrue(self.spawned, "main() must have attempted one pass")
        child = self.spawned[0]["env"]
        self.assertEqual(child.get("BARE_AI_MODEL"), "deepseek-v4-flash")
        self.assertEqual(child.get("BARE_AI_ENDPOINT"),
                         "https://api.deepseek.com/v1/chat/completions")
        # The single authoritative line, and never the profile as the winner.
        self.assertIn("route in effect = execution_profile", out)
        self.assertNotIn("route in effect = catalogue/node profile", out)

    def test_main_still_reports_the_node_profile_as_the_route_in_effect(self):
        # Control: with no verified profile the node profile must win, so the
        # assertion above is not vacuous.
        os.environ.pop("TASKBUS_CLAIM_PAYLOAD", None)
        os.environ["TASKBUS_PROFILE_REQUIRED"] = "0"
        out = self._run_main()
        self.assertTrue(self.spawned, "main() must have attempted one pass")
        child = self.spawned[0]["env"]
        self.assertEqual(child.get("BARE_AI_MODEL"), "claude-sonnet-4-6")
        self.assertIn("route in effect = catalogue/node profile", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
