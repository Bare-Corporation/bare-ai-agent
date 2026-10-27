# The Taskbus

The Taskbus is how work is queued, routed and evidenced across the Bare-AI fleet. It is a set of Baserow
tables plus a poller that reads them and a runner that executes what it finds. This directory documents the
implemented behaviour, not an aspiration.

## The tables

| table | what it holds |
|---|---|
| 215 | tickets/rows: the unit of work |
| 43 | machine representatives; the rep ids used for routing |
| 212 | roles and skills (the registry) |

## How a row moves

1. A row is written with a success_criterion, an ai_prompt, a priority and a
   stakeholder_representatives_master value.
2. Each node runs a poller. The poller claims ONLY rows whose representative set contains its own rep id
   (POLLER_SELF_REP, with POLLER_FILTER_MODE=rep). A row with no representative is UNOWNED and is claimed by
   nobody by design; a row owned by another rep is left to that node poller.
3. On claim the poller writes InProgress, a job uuid into corresponding_machine_task_id, and freezes a
   snapshot of the criterion so the row cannot be regraded against a criterion edited mid-flight.
4. taskbus-agent-job.py runs the work on the target node with that node own agent identity, invoking a CLI
   with a prompt file (--prompt-file), up to a pass limit.
5. The result is written back to machines_response; a completion is graded by the criterion, and a verdict
   plus verified_by is recorded only on a pass.

## Criteria - directives only

success_criterion is parsed as SEMICOLON-separated directives. Prose is refused at claim time, so a
human-written sentence in that field makes the row unclaimable. Supported:

    exit_zero                          transport bypass only; proves nothing
    file_exists:/abs/path
    file_md5:/abs/path=hex
    file_sha256:/abs/path=hex
    file_excludes:/abs/path=LITERAL    passes only if the file exists AND the text is absent
    http_200:url
    http_status:url=code
    git_ls_remote:repo@branch=sha

file_excludes exists because "remove X" tickets were being completed while X was still present: it is the
only directive here that cannot be satisfied by doing nothing. Justification for a weak criterion belongs in
human_notes, never beside the directive.

## Statuses that matter

Draft - Not Ready for Poller is inert (not claimable). New, Retry and Reopened are claimable. InProgress is
claimed. HumanReviewNeeded means work stopped and a human must look. A worker never sets Completed; the
dispatcher or a human does.

## Two honest limitations

- There is no dedicated Taskbus API credential provisioned yet: the gateway taskbus routes exist but reject
  the model key, so clients read and write the tables directly with their own node credential.
- Machine identity is not yet recorded on claim. Attribution currently lives in machines_response, which is a
  machine describing itself. See the machine-identity design contract before relying on attribution.
