#!/usr/bin/env python3
# Made with Grok Bot.
"""Shared CLI helpers for Claudia tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "ledger" / "claudia_ledger.py"

# Subcommands that do not take --approved.
_NO_APPROVED = frozenset(
    {
        "init",
        "check",
        "export-brain",
        "build-brain",
        "backups",
        "doctor",
        "propose-links",
        "snapshot",
        "cleanup-half-built",
    }
)

# Global flags that take a following value (before the subcommand).
_VALUE_FLAGS = frozenset(
    {
        "--home",
        "--bot-id",
        "--ledger",
        "--backup-keep",
        "--approved",
        "--rule",
    }
)

_SUBCOMMANDS = frozenset(
    {
        "init",
        "check",
        "add",
        "update",
        "health",
        "close",
        "merge",
        "forget",
        "export-brain",
        "build-brain",
        "backups",
        "restore",
        "snapshot",
        "doctor",
        "cleanup-half-built",
        "propose-links",
        "apply-links",
    }
)


def with_approved(args: tuple[str, ...] | list[str], approved: str | None = "yes") -> list[str]:
    """Insert --approved before the ledger subcommand when needed."""
    out = list(args)
    if approved is None:
        return out
    if "--approved" in out:
        return out
    i = 0
    while i < len(out):
        tok = out[i]
        if tok in _SUBCOMMANDS:
            if tok in _NO_APPROVED:
                return out
            return out[:i] + ["--approved", approved] + out[i:]
        if tok in _VALUE_FLAGS:
            i += 2
            continue
        if tok.startswith("--") and "=" in tok:
            i += 1
            continue
        if tok.startswith("-"):
            i += 1
            continue
        i += 1
    return out


def run_ledger(
    home: Path,
    *args: str,
    bot_id: str,
    check: bool = True,
    approved: str | None = "yes",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(LEDGER), "--home", str(home), "--bot-id", bot_id]
    use_approved = approved
    if use_approved == "yes" and args and args[0] == "health":
        use_approved = "no-approval-needed"
    cmd.extend(with_approved(args, approved=use_approved))
    full_env = os.environ.copy()
    full_env.pop("CLAUDIA_HOME", None)
    full_env.pop("CLAUDIA_BOT_ID", None)
    if env:
        full_env.update(env)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=full_env)
    if check and proc.returncode != 0:
        raise AssertionError(
            f"failed ({proc.returncode}): {args}\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return proc
