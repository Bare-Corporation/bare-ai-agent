#!/usr/bin/env python3
"""
Bare-AI Todo Manager
====================

A lightweight, CSV-backed todo system designed for AI agents.

Design goals
------------
* Todos grow large over time. Instead of one giant file, todos are split across
  six small stage files. The AI almost always reads only `not_started.csv` or
  `in_progress.csv` (small + fast); the remaining files are touched only when
  answering occasional user queries.
* The filename *is* the lifecycle stage. A row moves between files by being
  written to the new file and removed from the old one.
* Every file shares an identical header row, so rows move losslessly.

Stages (one file each)
----------------------
    not_started.csv   queued, not begun
    in_progress.csv   actively worked on
    issue.csv         blocker / problem needing attention
    on_hold.csv       parked / waiting on something external
    completed.csv     done
    withdrawn.csv     cancelled / no longer relevant

Shared header (identical across all files)
------------------------------------------
    id,title,description,status,priority,created_at,updated_at,due_date,tags,notes

Append policy (where a newly added/moved row lands in a file)
-------------------------------------------------------------
    top    -> inserted directly beneath the header (LIFO: most recent first)
    bottom -> appended to the end (FIFO: chronological order)

    not_started : bottom  (backlog - keep oldest first so it is picked up FIFO)
    in_progress : top     (focus - newest active task first)
    issue       : top     (attention - newest blocker first)
    on_hold     : bottom  (parked - chronological)
    completed   : top     (history - most recently completed first)
    withdrawn   : bottom  (archive - chronological)

Usage
-----
    todo.py init                               create/verify files + headers
    todo.py add "Title" [--stage ...] [--priority high] [--due 2026-09-01]
    todo.py move <id> --to completed [--from in_progress]
    todo.py list [--stage not_started] [--json]
    todo.py find <id>
    todo.py summary                            row counts per stage
    todo.py validate                           check files + header integrity
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import uuid
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# The todo files live next to this script (self-contained folder).
TODO_DIR = os.path.dirname(os.path.realpath(__file__))

HEADERS = [
    "id",
    "title",
    "description",
    "status",
    "priority",
    "created_at",
    "updated_at",
    "due_date",
    "tags",
    "notes",
]

STAGES = [
    "not_started",
    "in_progress",
    "issue",
    "on_hold",
    "completed",
    "withdrawn",
]

PRIORITIES = ["low", "medium", "high", "critical"]

# Where a newly added or moved row lands in each stage file.
APPEND_POLICY = {
    "not_started": "bottom",
    "in_progress": "top",
    "issue": "top",
    "on_hold": "bottom",
    "completed": "top",
    "withdrawn": "bottom",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _now() -> str:
    """ISO-8601 UTC timestamp (seconds precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    """Short, practically-unique, human-friendly id."""
    return "td-" + uuid.uuid4().hex[:12]


def _path(stage: str) -> str:
    if stage not in STAGES:
        raise ValueError(f"unknown stage '{stage}'; expected one of {STAGES}")
    return os.path.join(TODO_DIR, f"{stage}.csv")


def _read(stage: str) -> list[dict]:
    """Read a stage file into a list of dicts (header row excluded)."""
    path = _path(stage)
    if not os.path.exists(path):
        return []
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for raw in reader:
            row = {k: (raw.get(k) or "").strip() for k in HEADERS}
            if row["id"]:  # skip fully-empty rows (e.g. a trailing blank line)
                rows.append(row)
        return rows


def _write(stage: str, rows: list[dict]) -> None:
    """Atomically write rows beneath the canonical header for a stage."""
    path = _path(stage)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def _append(stage: str, row: dict) -> None:
    """Insert `row` into the stage file per APPEND_POLICY (top or bottom)."""
    rows = _read(stage)
    if APPEND_POLICY.get(stage, "bottom") == "top":
        rows.insert(0, row)
    else:
        rows.append(row)
    _write(stage, rows)


def _ensure_files() -> None:
    """Create any missing stage file with the canonical header."""
    for stage in STAGES:
        path = _path(stage)
        if not os.path.exists(path):
            _write(stage, [])


# --------------------------------------------------------------------------- #
# Core operations
# --------------------------------------------------------------------------- #

def add(title, stage="not_started", description="", priority="medium",
        due_date="", tags="", notes=""):
    """Create a new todo and append it to `stage` per the append policy."""
    _ensure_files()
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    now = _now()
    row = {
        "id": _new_id(),
        "title": title,
        "description": description,
        "status": stage,
        "priority": priority,
        "created_at": now,
        "updated_at": now,
        "due_date": due_date,
        "tags": tags,
        "notes": notes,
    }
    _append(stage, row)
    return row


def find(todo_id):
    """Return (stage, row) for a todo id, or (None, None) if not found."""
    for stage in STAGES:
        for row in _read(stage):
            if row["id"] == todo_id:
                return stage, row
    return None, None


def move(todo_id, to_stage, from_stage=None):
    """Move a todo between stage files. Returns (from_stage, row)."""
    _ensure_files()
    if to_stage not in STAGES:
        raise ValueError(f"unknown stage '{to_stage}'; expected one of {STAGES}")

    if from_stage is None:
        from_stage, row = find(todo_id)
        if row is None:
            raise ValueError(f"todo '{todo_id}' not found in any stage file")
    else:
        if from_stage not in STAGES:
            raise ValueError(f"unknown stage '{from_stage}'")
        row = next((r for r in _read(from_stage) if r["id"] == todo_id), None)
        if row is None:
            raise ValueError(f"todo '{todo_id}' not found in {from_stage}.csv")

    if from_stage == to_stage:
        raise ValueError(f"todo '{todo_id}' is already in {to_stage}.csv")

    # Remove from source.
    _write(from_stage, [r for r in _read(from_stage) if r["id"] != todo_id])

    # Update status + timestamp, then append to destination.
    row["status"] = to_stage
    row["updated_at"] = _now()
    _append(to_stage, row)

    return from_stage, row


def list_todos(stage=None):
    """Return [(stage, row), ...] for one stage or all stages."""
    stages = [stage] if stage else STAGES
    out = []
    for s in stages:
        for row in _read(s):
            out.append((s, row))
    return out


def summary():
    """Return row counts per stage."""
    return {s: len(_read(s)) for s in STAGES}


def validate():
    """Return a list of integrity problems (empty list == healthy)."""
    problems = []
    for stage in STAGES:
        path = _path(stage)
        if not os.path.exists(path):
            problems.append(f"{stage}.csv: missing")
            continue
        with open(path, "r", newline="", encoding="utf-8") as f:
            first = f.readline().rstrip()
        expected = ",".join(HEADERS)
        if first != expected:
            problems.append(
                f"{stage}.csv: header mismatch (expected {expected!r}, got {first!r})"
            )
            continue
        for row in _read(stage):
            if row["status"] and row["status"] != stage:
                problems.append(
                    f"{stage}.csv: row {row['id']} has status {row['status']!r}"
                )
    return problems


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="todo.py",
        description="Bare-AI Todo Manager - CSV-backed, stage-separated todos.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create missing stage files with the canonical header")

    p_add = sub.add_parser("add", help="add a new todo")
    p_add.add_argument("title", help="short task title")
    p_add.add_argument("--stage", default="not_started", choices=STAGES)
    p_add.add_argument("--description", default="")
    p_add.add_argument("--priority", default="medium", choices=PRIORITIES)
    p_add.add_argument("--due", default="", dest="due_date")
    p_add.add_argument("--tags", default="")
    p_add.add_argument("--notes", default="")

    p_move = sub.add_parser("move", help="move a todo to another stage")
    p_move.add_argument("todo_id")
    p_move.add_argument("--to", required=True, choices=STAGES, dest="to_stage")
    p_move.add_argument("--from", choices=STAGES, default=None, dest="from_stage")

    p_list = sub.add_parser("list", help="list todos")
    p_list.add_argument("--stage", choices=STAGES, default=None)
    p_list.add_argument("--json", action="store_true", dest="as_json")

    p_find = sub.add_parser("find", help="locate a todo by id and show full details")
    p_find.add_argument("todo_id")

    sub.add_parser("summary", help="print row counts per stage")

    sub.add_parser("validate", help="check file existence + header integrity")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "init":
            _ensure_files()
            print("initialised stage files:")
            for s in STAGES:
                print(f"  {s}.csv")
            return 0

        if args.cmd == "add":
            row = add(
                args.title,
                stage=args.stage,
                description=args.description,
                priority=args.priority,
                due_date=args.due_date,
                tags=args.tags,
                notes=args.notes,
            )
            print(f"added {row['id']} -> {row['status']}.csv")
            return 0

        if args.cmd == "move":
            from_stage, row = move(args.todo_id, args.to_stage,
                                   from_stage=args.from_stage)
            print(f"moved {row['id']}: {from_stage}.csv -> {args.to_stage}.csv")
            return 0

        if args.cmd == "list":
            items = list_todos(args.stage)
            if args.as_json:
                print(json.dumps([{"stage": s, **r} for s, r in items], indent=2))
            else:
                for s, r in items:
                    print(f"{r['id']}	{s}	{r['priority']}	{r['title']}")
            return 0

        if args.cmd == "find":
            stage, row = find(args.todo_id)
            if row is None:
                print(f"todo '{args.todo_id}' not found")
                return 1
            print(f"id:          {row['id']}")
            print(f"stage:       {stage}")
            print(f"title:       {row['title']}")
            print(f"description: {row['description']}")
            print(f"priority:    {row['priority']}")
            print(f"created_at:  {row['created_at']}")
            print(f"updated_at:  {row['updated_at']}")
            print(f"due_date:    {row['due_date']}")
            print(f"tags:        {row['tags']}")
            print(f"notes:       {row['notes']}")
            return 0

        if args.cmd == "summary":
            counts = summary()
            for s in STAGES:
                print(f"{s:<12} {counts[s]}")
            return 0

        if args.cmd == "validate":
            problems = validate()
            if problems:
                print("problems found:")
                for p in problems:
                    print(f"  {p}")
                return 1
            print("OK - all stage files present with correct headers")
            return 0

    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
