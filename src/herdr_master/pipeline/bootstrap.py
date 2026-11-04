"""Canonical skill handoff. Python loads the skill body and appends context.

This module does not copy Scout, Architect, Coder, or Reviewer briefs and does
not claim that later pipeline phases have already run. Absent ``worktree`` and
``run_dir`` mean the skill performs its normal isolation step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from herdr_master.core.skill_resources import load_skill_body

BOOTSTRAP_VERSION = 1
BOOTSTRAP_SENTINEL = "HERDR_MASTER_BOOTSTRAP_V1"

_REQUIRED = (
    "entrypoint",
    "intent",
    "origin_cwd",
    "session",
    "socket",
    "workspace_id",
    "owns_workspace",
)
_OPTIONAL = (
    "pane_id",
    "tab_id",
    "squad",
    "allowed_coder_models",
    "worktree",
    "run_dir",
)


def _bootstrap_document(context: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in _REQUIRED if key not in context]
    if missing:
        names = ", ".join(missing)
        raise ValueError(f"Bootstrap context missing: {names}.")
    origin = context["origin_cwd"]
    if not isinstance(origin, str) or not Path(origin).is_absolute():
        raise ValueError("origin_cwd must be an absolute path.")
    if not isinstance(context["owns_workspace"], bool):
        raise ValueError("owns_workspace must be a bool.")
    if not isinstance(context["entrypoint"], str) or not context["entrypoint"]:
        raise ValueError("entrypoint must be a non-empty string.")
    if not isinstance(context["session"], str) or not context["session"]:
        raise ValueError("session must be a non-empty string.")
    if not isinstance(context["socket"], str) or not context["socket"]:
        raise ValueError("socket must be a non-empty string.")
    intent = context["intent"]
    if intent is not None and not isinstance(intent, str):
        raise ValueError("intent must be a string or null.")
    document: dict[str, Any] = {
        "bootstrap_version": BOOTSTRAP_VERSION,
        "entrypoint": context["entrypoint"],
        "intent": intent,
        "origin_cwd": origin,
        "session": context["session"],
        "socket": context["socket"],
        "workspace_id": context["workspace_id"],
        "owns_workspace": context["owns_workspace"],
    }
    for key in _OPTIONAL:
        if key in context and context[key] is not None:
            document[key] = context[key]
    return document


def build_bootstrap_prompt(skill_name: str, context: dict[str, Any]) -> str:
    """Return the canonical skill body plus a version-1 JSON bootstrap context.

    ``context`` is data. Intent is JSON-encoded and is not interpolated into a
    shell command. The skill body is loaded through the resource helper.
    """
    document = _bootstrap_document(context)
    try:
        encoded = json.dumps(document, indent=2, ensure_ascii=False)
    except TypeError as err:
        raise ValueError("Bootstrap context must be JSON-serializable.") from err
    body = load_skill_body(skill_name).rstrip("\n")
    return f"{body}\n\n{BOOTSTRAP_SENTINEL}\n```json\n{encoded}\n```\n"
