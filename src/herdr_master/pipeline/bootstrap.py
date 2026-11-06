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
    "models",
    "native_harness",
    "dangerously_skip_permissions",
    "worktree",
    "run_dir",
    "council",
    "ai_head",
    "config_path",
)


def _binding_dict(binding: Any) -> dict[str, Any]:
    if hasattr(binding, "model_dump"):
        d = binding.model_dump()
        if hasattr(d.get("kind"), "value"):
            d["kind"] = d["kind"].value
        if hasattr(d.get("effort"), "value"):
            d["effort"] = d["effort"].value
        return d
    elif isinstance(binding, dict):
        return dict(binding)
    return {"model": str(binding)}


def _format_council_data(council: Any) -> dict[str, Any]:
    """Format CouncilConfig for the bootstrap document.

    Formats member identities, bindings, and hard maximum of 5 rounds.
    """
    if hasattr(council, "coordinator") and hasattr(council, "members"):
        members = [_binding_dict(m) for m in council.members]
        coord = _binding_dict(council.coordinator)
        scout = _binding_dict(council.scout)
        return {
            "coordinator": coord,
            "scout": scout,
            "members": members,
            "max_rounds": 5,
        }
    elif isinstance(council, dict):
        res = dict(council)
        res.setdefault("max_rounds", 5)
        return res
    return council


_AI_HEAD_READY_INSTRUCTIONS = (
    "AiHead is available for autonomous technical decisions within delegated scope. "
    "Consult AiHead using correlated requests and structured JSON responses. "
    "Escalate ambiguous requirements or destructive operations to the user."
)
_AI_HEAD_BUSY_INSTRUCTIONS = (
    "AiHead is currently busy or unavailable. Do not consult AiHead; "
    "escalate decisions directly to the user."
)


def _normalize_ai_head(ai_head: Any) -> dict[str, Any] | None:
    """Normalize AiHead (dict or model) into a block with readiness-aware instructions.

    Returns None when AiHead is absent or disabled.
    """
    if ai_head is None:
        return None
    if isinstance(ai_head, dict):
        if not ai_head.get("enabled", False):
            return None
        res = dict(ai_head)
    else:
        if not getattr(ai_head, "enabled", False):
            return None
        res = ai_head.model_dump() if hasattr(ai_head, "model_dump") else dict(ai_head)
    unavailable = (
        res.get("status") in ("busy", "pending", "unavailable")
        or res.get("busy") is True
        or res.get("reconciled") is False
    )
    res["instructions"] = _AI_HEAD_BUSY_INSTRUCTIONS if unavailable else _AI_HEAD_READY_INSTRUCTIONS
    return res


def _format_ai_head_data(ai_head: Any) -> dict[str, Any] | None:
    """Format AiHead discovery block when enabled; return None when disabled/None."""
    return _normalize_ai_head(ai_head)


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
        if key == "council":
            if "council" in context and context["council"] is not None:
                document["council"] = _format_council_data(context["council"])
        elif key == "ai_head":
            if "ai_head" in context and context["ai_head"] is not None:
                formatted_head = _format_ai_head_data(context["ai_head"])
                if formatted_head is not None:
                    document["ai_head"] = formatted_head
        elif key == "config_path":
            if "config_path" in context and context["config_path"] is not None:
                document["config_path"] = str(context["config_path"])
        else:
            if key in context and context[key] is not None:
                document[key] = context[key]
    return document


def build_bootstrap_prompt(
    skill_name: str,
    context: dict[str, Any],
    *,
    council: Any = None,
    ai_head: Any = None,
    config_path: Path | str | None = None,
) -> str:
    """Return the canonical skill body plus a version-1 JSON bootstrap context.

    ``context`` is data. Intent is JSON-encoded and is not interpolated into a
    shell command. The skill body is loaded through the resource helper.
    """
    ctx = dict(context)
    if council is not None:
        ctx["council"] = council
    if ai_head is not None:
        ctx["ai_head"] = ai_head
    if config_path is not None:
        ctx["config_path"] = str(config_path)
    document = _bootstrap_document(ctx)
    try:
        encoded = json.dumps(document, indent=2, ensure_ascii=False)
    except TypeError as err:
        raise ValueError("Bootstrap context must be JSON-serializable.") from err
    body = load_skill_body(skill_name).rstrip("\n")
    return f"{body}\n\n{BOOTSTRAP_SENTINEL}\n```json\n{encoded}\n```\n"
