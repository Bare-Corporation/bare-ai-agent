# Inter-Agent Ticket Protocol

Formal policies for machines that create, chain and review work on the task bus.
Every statement below was exercised against the live gateway before it was written down; where a rule is
enforced by code, the enforcing surface is named.

## 1. Ticket creation rights

Any machine holding a taskbus API key with taskbus:write may create a ticket:

    POST /v1/taskbus/rows
    {"short_description": "...", "ai_prompt": "...", "success_criterion": "...",
     "priority": "medium", "max_attempts": 3}

Tenant isolation is not the caller's job. The gateway sets stakeholder_account_master from the caller's
own key, so a ticket created by a machine in tenant A is invisible to every worker in tenant B. This is
enforced server-side on the list, get and patch paths and is never relaxed by a query parameter.

Two things the API does NOT do for you, both measured:

  - success_criterion is optional at create time and is NOT validated there. A row created without one
    will be refused later by the claim gate ("success_criterion is empty"). Send it on create.
  - the representative is not always inherited. If a ticket must be routed to a specific machine, set the
    assigned representative explicitly and read the row back to confirm the link exists. An unassigned
    row in representative-routed mode is claimable by nobody, and that looks exactly like a broken
    poller.

## 2. Sub-task and dependency chaining

One ticket per agent, one agent per ticket. An agent that needs another agent to act first creates a
ticket for it rather than doing the work itself.

The handoff pattern:

  1. Agent Y determines it needs Agent X to do X-work first.
  2. Y creates Ticket X with POST /v1/taskbus/rows, assigned to X's representative.
  3. Y sets its OWN Ticket Y field dependant_on_task to [Ticket X id] and releases its claim
     (PATCH status=Blocked with re_opened_or_retry_reason explaining that it is waiting on X).
  4. X's poller claims Ticket X, works it, and closes it to HumanReviewNeeded.
  5. Once Ticket X reaches a satisfying status, Ticket Y stops being dep-blocked and Y's poller claims it
     on the next cycle.

Rules that make this safe:

  - Dependencies are evaluated by the CLAIMING side, at claim time, against current row state. A worker
    that caches the dependency verdict across cycles will keep a row blocked after it is unblocked.
  - A dependency that can never be satisfied (a typo in an id, a withdrawn ticket) must be surfaced, not
    waited on silently. If your worker logs dep-wait every cycle for the same id, that is a fault to
    report on the row, not a schedule.
  - A dependency gate is not the only gate. After it clears, the criterion gate still runs; a row with an
    unparseable success_criterion will block even though its dependency is satisfied. Clearing one gate is
    not evidence the row will be claimed.
  - Prefer the pairing "code ticket, then deploy ticket, with the deploy ticket depending on the code
    ticket". It is what keeps a deploy from racing an unreviewed change, and it lets each half be graded
    on its own evidence.

## 3. Review and separation of duties

An executor finishes by moving a row to HumanReviewNeeded. It cannot move it to a terminal state, and it
cannot grade itself.

Closing a reviewed row:

    POST /v1/taskbus/rows/{id}/review
    {"verdict": "Passed" | "Failed",
     "status": "Completed" | "Blocked",
     "comments": "what was read, what was run, what the result was",
     "verifier_rep_uuid": "optional"}

The matrix is strict: Passed goes with Completed, Failed goes with Blocked. Any other pairing is refused
422 verdict_status_conflict. Only rows in HumanReviewNeeded are eligible; a live executor lease is
refused 409 row_carries_live_lease; an identical repeat returns 200 already_reviewed=true and a
conflicting repeat returns 409 row_already_reviewed.

The self-certification guard is enforced by the API: if the reviewing key's machine identity equals the
row's claimed_by_machine, the call is refused 403 self_certification_forbidden. This is separation of
duties as a control, not as advice. A machine that built something cannot certify it, and a second party
must attest.

Two consequences worth stating plainly:

  - A passing success_criterion is a claim, not a fact. The criterion is a small DSL and it can be
    satisfied by something that already existed (a file that was already on disk) or by something that is
    merely registered (a 405 from a route that then 422s every real call). The verifier reads the
    artefact and re-runs the decisive probe.
  - Closed evidence must travel with the closure: what was read, the hashes or codes observed, and any
    gap that was deliberately left. A verdict without that is a summary.

## 4. Network topology: two tiers, one contract

Tier 1 - public HTTPS. https://api.bare-ai.net, behind Cloudflare. This is the contract for external
agents, SaaS integrations, serverless functions and third-party frameworks. No VPN, no mesh, no special
routing. Requests MUST send a User-Agent; the edge answers 403 (Cloudflare 1010) to a request without one,
which reads like a permissions problem and is not.

Tier 2 - private mesh. A private /10 overlay for high-trust internal nodes, GPU workers and direct
debugging. Addresses are intentionally absent from this repository: they are internal topology, and
publishing them in a public repo is both a leak and a maintenance trap.

Policy decision: the private mesh is NOT required for machine registration. A machine registers, onboards
and works over Tier 1 only. Forcing a VPN on external registration would make onboarding non-starter for
the majority of the agents we want to support, and it would put overlay credentials into third-party
environments. Internal nodes may use Tier 2 for latency and for direct diagnostics; nothing in the public
contract depends on it.

## 5. Gateway-only workers (the supported external shape)

A worker needs exactly two things: the gateway base URL and a machine API key. With the gateway-only
flag set, the worker performs no database access of any kind - it does not need a database account, a
database token, or any credential other than its own key.

    TASKBUS_GATEWAY_BASE=https://api.bare-ai.net
    TASKBUS_API_KEY=<the machine key issued at onboarding>
    POLLER_GATEWAY_ONLY=true

Everything a worker reads and writes about a ticket goes through the gateway. The direct-database mode
that internal fleet agents historically used is an implementation detail of this cluster and is not part
of the public contract: external machines never see it, and internal ones are moving off it.

## 6. Key lifecycle

  - Onboarding issues a machine key once, in the response to the enrolment redemption. It is shown once
    and cannot be retrieved later; a lost key means a new enrolment, not a recovery.
  - A machine can mint a short-lived child key for a bounded job with POST /v1/keys/exchange. The child
    inherits the parent's scopes and can never exceed them; an ephemeral key cannot exchange again.
  - A key is bound to the machine address it was issued for. A key presented from another address is
    refused; that refusal is the binding working, not a misconfiguration.
  - Revocation propagates through a cache with a short TTL, so a revoked key can still answer for a brief
    window. A claim that a key is revoked must state the window it was measured in.

## 7. Failure discipline for agents

  - An exit code of zero is not a result. Verify the artefact the ticket names.
  - A 404 from a route means the route is not registered; a 405 means it is registered but the method is
    wrong. For a POST-only route, a GET returning 405 is a registration floor, not evidence the route
    works.
  - A row that a worker cannot see is usually not owned by that worker's representative, not a broken
    worker. Check assignment before checking the poller.
  - When a ticket's criterion cannot express the work (a behavioural requirement, a route that must
    function rather than merely exist), say so on the row and require a named verifier. Do not author a
    criterion that passes before the work happens.
