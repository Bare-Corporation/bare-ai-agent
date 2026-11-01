#!/usr/bin/env bash
# start-taskbus-poller.sh - THE supported way to start the taskbus poller.
# Ticket 275. WHY: the ownership filter is the only thing stopping two nodes claiming the same row,
# and it lives in the process environment. Before this launcher POLLER_FILTER_MODE and
# POLLER_SELF_REP were set ad hoc at launch, so any wrapper, cron entry or manual invocation could
# start a poller with no filter at all. THIS SCRIPT FAILS CLOSED: if the node cannot be given
# exactly one rep id and its own name it starts nothing and exits non-zero. There is no
# central-dispatcher fallback.
#
# CONFIGURATION IS PER-NODE AND UNTRACKED (2026-11-01). This launcher used to carry an
# address -> rep -> node table. That is reasonable for a private host and wrong for a PUBLIC
# repository: it published the fleet's overlay addressing together with its ownership mapping, so
# anyone reading the repo learned which node owns which rep id. POLLER_SELF_REP and
# POLLER_SELF_NODE now come from the node's own environment - its systemd unit, an env file the
# host owns, or the invoking shell. Nothing about the fleet belongs in this file.
set -euo pipefail

REPO_DIR="${BARE_AI_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
POLLER="${REPO_DIR}/scripts/lib/taskbus-poller.py"

# --- fail-closed identity ----------------------------------------------------
if [ -z "${POLLER_SELF_REP:-}" ] || [ -z "${POLLER_SELF_NODE:-}" ]; then
    echo "FATAL: POLLER_SELF_REP and POLLER_SELF_NODE must both be set for this node." >&2
    echo "       Without them the poller cannot filter by owner or attest its own verdicts," >&2
    echo "       so it would either claim nothing or claim other nodes' rows." >&2
    echo "       Supply them from the node's environment (systemd unit or an env file the" >&2
    echo "       host owns) - never from a table in this repository." >&2
    exit 78
fi

case "${POLLER_SELF_REP}" in
    ''|*[!0-9]*)
        echo "FATAL: POLLER_SELF_REP must be a numeric representative id, got '${POLLER_SELF_REP}'." >&2
        echo "       An unparseable rep id makes the ownership filter meaningless." >&2
        exit 78
        ;;
esac

if [ ! -f "${POLLER}" ]; then
    echo "FATAL: poller not found at ${POLLER} - is the repo complete?" >&2
    exit 66
fi

# The launcher's whole purpose is to force the rep filter; it is not overridable here.
export POLLER_FILTER_MODE=rep
export POLLER_SELF_REP
export POLLER_SELF_NODE

echo "[taskbus] node=${POLLER_SELF_NODE} rep=${POLLER_SELF_REP} mode=rep (filter enforced by this launcher)"
exec python3 "${POLLER}" "${@:---live --interval 300}"
