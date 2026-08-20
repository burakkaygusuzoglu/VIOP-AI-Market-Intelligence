#!/usr/bin/env python3
"""Block git commands that publish history.

The master specification forbids committing or pushing without an explicit
instruction (sections 116, 117 and 119). Relying on that rule being remembered
across sessions and context compactions is not a control; this hook makes it
mechanical.

Blocks:   git commit, git push  (including chained and flag-prefixed forms)
Allows:   every read-only git command - status, diff, log, branch, show, ...

Reads the PreToolUse payload on stdin and emits a permission decision. Any
internal failure allows the command through rather than jamming the session;
this is a guard rail, not a security boundary.
"""

from __future__ import annotations

import json
import shlex
import sys

BLOCKED_SUBCOMMANDS = {"commit", "push"}

# git global options that consume the following token as their value.
OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}

# Shell operators that separate one command from the next.
SEPARATORS = ("&&", "||", ";", "|", "\n")


def _segments(command: str) -> list[str]:
    """Split a shell line into individually executed commands."""
    parts = [command]
    for separator in SEPARATORS:
        parts = [piece for part in parts for piece in part.split(separator)]
    return [part.strip() for part in parts if part.strip()]


def _tokenize(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _git_subcommand(tokens: list[str]) -> str | None:
    """Return the git subcommand in this segment, if it invokes git."""
    index = 0
    while index < len(tokens):
        name = tokens[index].replace("\\", "/").rsplit("/", 1)[-1].lower()
        if name in {"git", "git.exe"}:
            break
        # Skip a leading environment assignment such as GIT_DIR=... git push
        if "=" in tokens[index] and not tokens[index].startswith("-"):
            index += 1
            continue
        return None
    else:
        return None

    index += 1
    while index < len(tokens):
        token = tokens[index]
        if token in OPTIONS_WITH_VALUE:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token.lower()
    return None


def blocked_subcommand(command: str) -> str | None:
    """Return the blocked git subcommand found in ``command``, if any."""
    for segment in _segments(command):
        subcommand = _git_subcommand(_tokenize(segment))
        if subcommand in BLOCKED_SUBCOMMANDS:
            return subcommand
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0

    subcommand = blocked_subcommand(command)
    if subcommand is None:
        return 0

    reason = (
        f"Blocked: 'git {subcommand}' is not permitted in this project.\n"
        "The VIOP AI Market Intelligence specification (sections 116, 117, 119) "
        "forbids committing or pushing unless the user explicitly instructs it.\n"
        "Read-only git commands (status, diff, log, branch, show) are allowed.\n"
        "If the user has just asked for a commit or push, they must run it "
        "themselves or remove this hook."
    )
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
