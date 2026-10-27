#!/usr/bin/env bash
# start-taskbus-poller.sh - THE supported way to start the taskbus poller.
# Ticket 275. WHY: the ownership filter is the only thing stopping two nodes claiming the same row,
# and it lives in the process environment. Before this launcher POLLER_FILTER_MODE and
# POLLER_SELF_REP were set ad hoc at launch, so any wrapper, cron entry or manual invocation could
# start a poller with no filter at all. THIS SCRIPT FAILS CLOSED: if the node cannot be mapped to
# exactly one rep id it starts nothing and exits non-zero. There is no central-dispatcher fallback.
set -euo pipefail

REPO_DIR="${BARE_AI_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
POLLER="${REPO_DIR}/scripts/lib/taskbus-poller.py"
NODE_FILE="${BARE_AI_NODE_FILE:-/etc/bare-ai/node.conf}"

detect_ip() {
    if [ -n "${BARE_AI_SELF_IP:-}" ]; then printf '%s' "${BARE_AI_SELF_IP}"; return; fi
    if [ -r "${NODE_FILE}" ]; then
        local ip
        ip="$(awk -F= '/^[[:space:]]*ip[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2; exit}' "${NODE_FILE}" || true)"
        if [ -n "${ip}" ]; then printf '%s' "${ip}"; return; fi
    fi
    if command -v tailscale >/dev/null 2>&1; then
        local tip
        tip="$(tailscale ip -4 2>/dev/null | head -1 || true)"
        if [ -n "${tip}" ]; then printf '%s' "${tip}"; return; fi
    fi
    hostname -I 2>/dev/null | awk '{print $1}'
}

IP="$(detect_ip)"
if [ -z "${IP}" ]; then
    echo "FATAL: cannot determine this node address; refusing to start an unfiltered poller." >&2
    exit 78
fi

case "${IP}" in
    100.64.0.4)  REP=271; SELF_NODE="bare-ai-brain@100.64.0.4" ;;
    100.64.0.13) REP=272; SELF_NODE="bare-ai-council-manager@100.64.0.13" ;;
    100.64.0.11) REP=273; SELF_NODE="bare-ai-developer@100.64.0.11" ;;
    100.64.0.19) REP=274; SELF_NODE="bare-ai-bare-ai.net-and-baserow-manager@100.64.0.19" ;;
    100.64.0.18) REP=275; SELF_NODE="bare-connectfi-manager@100.64.0.18" ;;
    100.64.0.2)  REP=276; SELF_NODE="bare-ai-secrets-manager@100.64.0.2" ;;
    *)
        echo "FATAL: address ${IP} is not mapped to a rep id. Refusing to start." >&2
        echo "       An unfiltered poller either claims nothing or claims other nodes rows." >&2
        echo "       Add this node to the rep map deliberately; do not bypass this check." >&2
        exit 78
        ;;
esac

if [ ! -f "${POLLER}" ]; then
    echo "FATAL: poller not found at ${POLLER} - is the repo complete?" >&2
    exit 66
fi

export POLLER_FILTER_MODE=rep
export POLLER_SELF_REP="${REP}"
export POLLER_SELF_NODE="${SELF_NODE}"

echo "[taskbus] node=${SELF_NODE} rep=${REP} mode=rep (filter enforced by this launcher)"
exec python3 "${POLLER}" "${@:---live --interval 300}"
