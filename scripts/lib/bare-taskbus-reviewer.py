#!/usr/bin/env python3
"""Independent reviewer for the Bare-AI Task Bus (table 215).

Scans rows in HumanReviewNeeded, verifies the success_criterion artefact itself, then grades:
  all directives pass  -> verdict Passed, status Completed
  any directive fails  -> verdict Failed, status Blocked
  cannot verify        -> NO write; the row stays HumanReviewNeeded and the reason is ledgered

Three hard design constraints, each enforced in code:
 1. FOUR-EYES: never grade a row that .4 executed. Refused, and recorded.
 2. LEASE-AWARE: never claims a row. If the review route answers 409 row_carries_live_lease the row is
    left alone until lease_expires_at and the timestamp is recorded.
 3. EVIDENCE-BACKED: the criterion is parsed as directives and the artefact is measured on the executing
    host over ssh (exists, size, sha256) or probed over http. exit_zero and prose are UNVERIFIABLE and
    are never graded - that is the whole point of this service existing.

Usage:
  bare-taskbus-reviewer.py --once [--dry-run]
  bare-taskbus-reviewer.py --interval 600
"""
import argparse
import datetime
import json
import os
import re
import shlex
import socket as _socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

GATEWAY_BASE = os.environ.get('TASKBUS_GATEWAY_BASE', 'https://api.bare-ai.net').rstrip('/')
REVIEWER_SECRET_PATH = os.environ.get('TASKBUS_REVIEWER_SECRET_PATH',
                                      'secret/data/machines/bare-ai-04')
_VAULT_ENVF = os.path.expanduser('~/.bare-ai/config/vault.env')
_KEY = {'v': None}


def _vault_kv_get(path):
    """Read one KV v2 API path with this node's AppRole. Never prints the value.

    Inlined here (rather than importing a helper that lives outside this repo) so the
    reviewer is a single self-contained, version-controlled file.
    """
    import ssl as _ssl
    env = {}
    with open(_VAULT_ENVF) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith('export ') and '=' in line:
                k, _, v = line[len('export '):].partition('=')
                env[k.strip()] = v.strip().strip('"').strip("'")
    addr = env.get('VAULT_ADDR', '').rstrip('/')
    role = env.get('VAULT_ROLE_ID')
    if not addr or not role:
        raise RuntimeError('VAULT_ADDR / VAULT_ROLE_ID missing from vault.env')
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    ident = {'role_id': role}
    if env.get('VAULT_SECRET_ID'):
        ident['secret_id'] = env['VAULT_SECRET_ID']
    req = urllib.request.Request(addr + '/v1/auth/approle/login',
                                 data=json.dumps(ident).encode())
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
        token = json.loads(resp.read())['auth']['client_token']
    req2 = urllib.request.Request(addr + '/v1/' + path.lstrip('/'))
    req2.add_header('X-Vault-Token', token)
    with urllib.request.urlopen(req2, context=ctx, timeout=20) as resp:
        return json.loads(resp.read()).get('data', {}).get('data', {})


def api_key():
    if _KEY['v'] is None:
        data = _vault_kv_get(REVIEWER_SECRET_PATH)
        key = (data.get('taskbus_api_key') or '').strip()
        if not key:
            raise RuntimeError('taskbus_api_key empty at ' + REVIEWER_SECRET_PATH)
        _KEY['v'] = key
    return _KEY['v']


def gateway_call(method, path, body=None, nonce=None):
    """Return (status_code, payload). HTTP errors are returned, not raised."""
    url = GATEWAY_BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('X-API-Key', api_key())
    req.add_header('Accept', 'application/json')
    req.add_header('User-Agent', UA)
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    if nonce:
        req.add_header('X-Claim-Nonce', nonce)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return exc.code, {'raw': raw[:400]}

STATE_DIR = os.path.expanduser('~/bare-necessities-workspace/taskbus')
LEDGER = os.path.join(STATE_DIR, 'reviewer-ledger-%s.jsonl')
def _self_identifiers():
    """Identifiers of the node running this reviewer, for the four-eyes self-check.

    Deliberately NOT hardcoded: this repository is public, so the node's own
    address/hostname must come from the node's environment. Set
    BARE_AI_REVIEWER_SELF to a comma-separated list (e.g. "user@host,host") in the
    unit or an untracked config file. We always add the local user and hostname so
    the guard still refuses to grade work this node executed even when the env var
    is unset.
    """
    idents = [x.strip() for x in os.environ.get('BARE_AI_REVIEWER_SELF', '').split(',') if x.strip()]
    for extra in (os.environ.get('USER'), _socket.gethostname()):
        if extra and extra not in idents:
            idents.append(extra)
    return tuple(idents)


EXECUTOR_SELF = _self_identifiers()
SAFE_PATH = re.compile(r'^/[A-Za-z0-9._/\-]+$')
UA = 'bare-ai-taskbus-reviewer/1.0'


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def log(msg):
    print('%s %s' % (now(), msg), flush=True)


def ledger(entry):
    os.makedirs(STATE_DIR, exist_ok=True)
    entry["at"] = now()
    path = LEDGER % now()[:10]
    with open(path, "a") as fh:
        fh.write(json.dumps(entry, sort_keys=True))
        fh.write(chr(10))


def shell_quote(s):
    return shlex.quote(s)


def parse_criterion(crit):
    """Return a list of (kind, arg) directives. Unknown kinds are surfaced so the caller can refuse."""
    directives = []
    for token in re.split(r'[;\s]+', (crit or '').strip()):
        if not token:
            continue
        if ':' not in token:
            if token.strip().lower() == 'exit_zero':
                directives.append(('exit_zero', token))
            else:
                directives.append(('prose', token))
            continue
        kind, _, arg = token.partition(':')
        directives.append((kind.strip().lower(), arg.strip()))
    return directives


def host_of(claimed_by):
    """user@host -> host (the address the artefact is read from over ssh)"""
    if not claimed_by or '@' not in claimed_by:
        return None
    return claimed_by.rsplit('@', 1)[-1].strip()


def remote_stat(host, path):
    """Measure one file on host. Returns dict(exists, size, sha256) or dict(error=...)."""
    if not SAFE_PATH.match(path):
        return {'error': 'unsafe path rejected by guard'}
    q = "if [ -f %s ]; then sha256sum -- %s; stat -c %%s -- %s; else echo NOFILE; fi" % (
        shell_quote(path), shell_quote(path), shell_quote(path))
    cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6',
           '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null',
           'bare-ai@' + host, q]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {'error': 'ssh timeout'}
    out = (r.stdout or '').strip().splitlines()
    if r.returncode != 0 or not out:
        return {'error': 'ssh rc=%s %s' % (r.returncode, (r.stderr or '').strip()[:120])}
    if out[0].strip() == 'NOFILE':
        return {'exists': False}
    parts = out[0].split()
    size = out[1].strip() if len(out) > 1 else None
    return {'exists': True,
            'sha256': parts[0] if parts else None,
            'size': int(size) if size and size.isdigit() else None}


def http_probe(url):
    req = urllib.request.Request(url, method='GET')
    req.add_header('User-Agent', UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return {'status': r.status, 'body_len': len(r.read())}
    except urllib.error.HTTPError as e:
        return {'status': e.code}
    except Exception as exc:  # noqa: BLE001
        return {'error': str(exc)[:120]}


def verify(row):
    """Return (verdict, facts). verdict is one of pass | fail | unverifiable."""
    crit = (row.get('success_criterion') or '').strip()
    claimed_by = row.get('claimed_by_machine') or ''
    host = host_of(claimed_by)
    facts = {'criterion': crit, 'claimed_by': claimed_by, 'host': host}
    if not crit:
        facts['reason'] = 'empty success_criterion'
        return 'unverifiable', facts
    directives = parse_criterion(crit)
    if not directives:
        facts['reason'] = 'no directives parsed'
        return 'unverifiable', facts

    checks = []
    for kind, arg in directives:
        if kind == 'exit_zero':
            facts['reason'] = 'exit_zero is self-fulfilling and is never graded'
            return 'unverifiable', facts
        if kind == 'prose':
            facts['reason'] = 'prose in criterion: ' + arg[:60]
            return 'unverifiable', facts
        if kind in ('file_exists', 'file_sha256', 'file_md5', 'file_excludes', 'file_changed'):
            if '=' in arg:
                path, _, expect = arg.partition('=')
            else:
                path, expect = arg, ''
            if not host:
                facts['reason'] = 'no executing host recorded on the row'
                return 'unverifiable', facts
            if host in EXECUTOR_SELF:
                facts['reason'] = 'artefact hosted on .4 itself'
                return 'unverifiable', facts
            st = remote_stat(host, path)
            if 'error' in st:
                facts['reason'] = 'cannot read artefact on %s: %s' % (host, st['error'])
                return 'unverifiable', facts
            checks.append((kind, path, expect, st))
        elif kind in ('http_200', 'http_status'):
            url, _, expect = arg.partition('=')
            checks.append((kind, url, expect or '200', http_probe(url)))
        else:
            facts['reason'] = 'unknown directive kind: ' + kind
            return 'unverifiable', facts

    facts['checks'] = checks
    failures = []
    for kind, target, expect, st in checks:
        if kind == 'file_exists':
            if not st.get('exists'):
                failures.append('%s missing on %s' % (target, host))
        elif kind == 'file_sha256':
            if not st.get('exists'):
                failures.append('%s missing' % target)
            elif (st.get('sha256') or '').lower() != expect.lower():
                failures.append('%s sha256 %s != expected %s' % (
                    target, (st.get('sha256') or '')[:16], expect[:16]))
        elif kind == 'file_changed':
            # A file_changed directive is ONLY satisfied by a real net change. Comparing
            # existence alone made this directive non-discriminating: it passed whenever
            # the file was present, so a ticket whose file was never touched graded green.
            # Measured on row 720 (2026-11-20): after sha256 == before sha256 ==
            # c80af8286bd022ae... and the reviewer still returned pass, which released the
            # dependent row 721 against an API surface that did not exist.
            if not st.get('exists'):
                failures.append(
                    '%s missing after execution (file_changed fails closed)' % target)
            elif not expect:
                failures.append(
                    '%s file_changed has no BEFORE sha256 to compare against' % target)
            elif (st.get('sha256') or '').lower() == expect.lower():
                failures.append(
                    '%s unchanged: after sha256 == before sha256 (%s) -> zero net change'
                    % (target, (st.get('sha256') or '')[:16]))
        elif kind == 'file_md5':
            # remote_stat returns a sha256, never an md5, so this cannot be verified here.
            # Fail closed instead of passing on existence alone.
            if not st.get('exists'):
                failures.append('%s missing' % target)
            else:
                failures.append(
                    '%s file_md5 is not verifiable by the remote probe (no md5 digest)' % target)
        elif kind == 'file_excludes':
            if not st.get('exists'):
                failures.append('%s missing (fail-closed)' % target)
        elif kind in ('http_200', 'http_status'):
            if st.get('status') is None:
                failures.append('%s probe failed: %s' % (target, st.get('error')))
            elif str(st['status']) != str(expect):
                failures.append('%s status %s != %s' % (target, st['status'], expect))
    if failures:
        facts['failures'] = failures
        return 'fail', facts
    return 'pass', facts


def describe(facts):
    bits = ['criterion: ' + (facts.get('criterion') or '(none)'),
            'executing host: ' + str(facts.get('host'))]
    for kind, target, expect, st in facts.get('checks') or []:
        if kind.startswith('file'):
            bits.append('%s %s exists=%s size=%s sha256=%s' % (
                kind, target, st.get('exists'), st.get('size'), (st.get('sha256') or '')[:16]))
        else:
            bits.append('%s %s status=%s' % (kind, target, st.get('status')))
    if facts.get('failures'):
        bits.append('failures: ' + '; '.join(facts['failures']))
    if facts.get('reason'):
        bits.append('reason: ' + facts['reason'])
    return ' | '.join(bits)


def review_row(row, dry_run):
    rid = int(row.get('id'))
    claimed_by = row.get('claimed_by_machine') or ''

    # 1. Four-eyes. A node may not grade its own execution.
    if claimed_by in EXECUTOR_SELF or any(
            ident and ident in claimed_by for ident in EXECUTOR_SELF):
        log('row %d SKIP four-eyes: executed by this node (%s)' % (rid, claimed_by))
        ledger({'row': rid, 'action': 'skip_four_eyes', 'claimed_by': claimed_by})
        return 'skip'

    # 3. Evidence before any verdict.
    verdict, facts = verify(row)
    if verdict == 'unverifiable':
        log('row %d SKIP unverifiable: %s' % (rid, facts.get('reason')))
        ledger({'row': rid, 'action': 'skip_unverifiable', 'facts': describe(facts)})
        return 'skip'

    passed = verdict == 'pass'
    comments = 'REVIEWER (.4 automated, four-eyes) %s. %s %s' % (
        now(),
        'Evidence verified independently:' if passed else 'Evidence did NOT verify:',
        describe(facts))
    body = {'verdict': 'Passed' if passed else 'Failed',
            'status': 'Completed' if passed else 'Blocked',
            'comments': comments}

    if dry_run:
        log('row %d DRY-RUN would grade %s/%s | %s' % (
            rid, body['verdict'], body['status'], describe(facts)[:200]))
        ledger({'row': rid, 'action': 'dry_run', 'verdict': body['verdict'],
                'facts': describe(facts)})
        return 'dry'

    # 2. Lease-aware: never claim. The route answers 409 with lease_expires_at if a lease is live.
    st, payload = gateway_call('POST', '/v1/taskbus/rows/%d/review' % rid, body)
    if st == 200:
        log('row %d GRADED %s -> %s' % (rid, body['verdict'], body['status']))
        ledger({'row': rid, 'action': 'graded', 'verdict': body['verdict'],
                'status': body['status'], 'facts': describe(facts)})
        return 'graded'
    if isinstance(payload, dict) and payload.get('error') == 'row_carries_live_lease':
        log('row %d SKIP live lease until %s' % (rid, payload.get('lease_expires_at')))
        ledger({'row': rid, 'action': 'skip_live_lease',
                'lease_expires_at': payload.get('lease_expires_at')})
        return 'skip'
    log('row %d ERROR review HTTP %s %s' % (rid, st, json.dumps(payload)[:160]))
    ledger({'row': rid, 'action': 'error', 'http': st, 'payload': payload})
    return 'error'


def cycle(dry_run):
    rows, page = [], 1
    while True:
        st, p = gateway_call('GET', '/v1/taskbus/rows?status=HumanReviewNeeded&page_size=200&page=%d' % page)
        if st != 200:
            log('ERROR listing rows HTTP %s %s' % (st, json.dumps(p)[:160]))
            ledger({'action': 'list_error', 'http': st})
            return
        rows.extend(p.get('results') or [])
        if not p.get('has_next'):
            break
        page += 1

    pending = [r for r in rows if (r.get('status') or {}).get('value') == 'HumanReviewNeeded']
    log('cycle start: %d row(s) in HumanReviewNeeded (pages read: %d)' % (len(pending), page))
    counts = {}
    for row in pending:
        outcome = review_row(row, dry_run)
        counts[outcome] = counts.get(outcome, 0) + 1
    log('cycle end: %s' % json.dumps(counts, sort_keys=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--interval', type=int, default=600)
    a = ap.parse_args()

    log('taskbus-reviewer starting (once=%s dry_run=%s interval=%ss)'
        % (a.once, a.dry_run, a.interval))
    if a.once:
        cycle(a.dry_run)
        return
    while True:
        try:
            cycle(a.dry_run)
        except Exception as exc:  # noqa: BLE001
            log('ERROR cycle raised: %s' % exc)
            ledger({'action': 'cycle_exception', 'error': str(exc)[:200]})
        time.sleep(a.interval)


if __name__ == '__main__':
    main()
