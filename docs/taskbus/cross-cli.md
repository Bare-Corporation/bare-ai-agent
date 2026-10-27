# Cross-CLI use: any CLI, multiple CLIs, and CLIs talking to each other

The Taskbus is not tied to one agent implementation. It is a work queue with an evidence trail, and the thing
that executes a row is whatever CLI the runner is configured to invoke.

## A job can invoke any CLI

taskbus-agent-job.py invokes a CLI with a prompt file rather than calling a model API directly:

    CLI = os.path.join(HOME, "bare-ai-cli", "bundle", "bare-ai.js")
    cmd = [NODE, CLI, "--yolo", "-p", "", "--prompt-file", tmp.name, "-o", "text"]

Nothing in the queue, the routing or the criterion grammar assumes that CLI. A node that runs a different CLI
(another vendor agent, a local model wrapper, a script) is executing the same contract: read the prompt, do
the work, write evidence back to the row.

## Different CLIs on different machines

Routing is by machine representative, not by software:

- assigned_to_machine names the TARGET machine.
- stakeholder_representatives_master (field 6378, table 43) decides OWNERSHIP, and each poller claims only
  rows whose rep set contains its own POLLER_SELF_REP.
- A per-machine rep to node map turns the rep id into a host.

So row A can be executed by one CLI on node X while row B, in the same table, is executed by a different CLI
on node Y. The queue is shared; the executors are not.

## CLIs answering each other (cross-communication)

The bus IS the channel. A row ai_prompt carries the request, and machines_response carries the reply,
including evidence and any findings. Cross-communication therefore works as:

    node X (CLI A) --writes a row--> table 215 <--reads and claims-- node Y (CLI B)
                                     |
                                     +-- B answer lands in machines_response
                                     +-- A (or a human) reads it from the same row

A job can raise follow-up work for another agent by creating another row, and can answer work raised for it by
writing the response field. What makes that safe is the same three properties the bus gives any work: an
explicit owner (rep id), a machine-checkable criterion, and a durable response trail.

## What this is NOT (stated so nobody assumes it)

- It is not a live message channel between agents. Communication is asynchronous and durable: rows and
  response fields, not a socket. There is no chat, no streaming handoff, and no conversational memory shared
  between two CLIs through the bus.
- It does not negotiate capability. A row does not declare "run me on a CLI that supports X"; a human or the
  dispatcher chooses the target machine, and therefore the executor.
- It does not yet record WHICH machine (let alone which CLI) claimed a row. Attribution is prose in
  machines_response until the machine-identity contract is implemented.
- Cross-CLI work is not sandboxed from itself: two agents can still write contradictory evidence to the same
  row. The response trail shows the contradiction; it does not prevent it.

## Why this matters commercially

You are not buying a wrapper around one model vendor. You are buying a queue where the executor is
replaceable - including swapping in a different vendor CLI per machine - while the work, the ownership and the
evidence stay in one auditable place.
