"""Import-cycle-safe harness catalog and the sole product default squad.

This module must not import Pydantic or ``herdr_master.core.models``. Domain
models re-export :class:`HarnessKind` from here. There is one product squad and
no per-host or native default squad.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Literal

SCHEMA_VERSION = 1
CANONICAL_SKILLS = ("ergo", "herdr-master", "orchestration")
LaunchMode = Literal["herdr", "native"]

_EFFORT_ALIASES = {"xhigh": "max"}
_VALID_EFFORTS = ("low", "medium", "high", "max")
_EFFORT_PHRASES = {
    "low": "think",
    "medium": "think hard",
    "high": "think harder",
    "max": "ultrathink",
}
_AGY_TIERS = ("flash_lite", "flash", "pro")
_AGY_TIER_ALIASES = {"flash-lite": "flash_lite"}
_FLASH_BASE = "gemini-3.8-flash"
_FLASH_BY_EFFORT = {
    "low": "gemini-3.8-flash-low",
    "medium": "gemini-3.8-flash-medium",
    "high": "gemini-3.8-flash-high",
}
_EFFORT_BY_FLASH = {slug: effort for effort, slug in _FLASH_BY_EFFORT.items()}
_ROLES = ("scout", "architect", "coder", "reviewer", "master", "watcher")
_ROLE_ALIASES = {"worker": "coder", "coders": "coder"}
_PERMISSION_FLAGS = ("--dangerously-skip-permissions",)


class CatalogError(ValueError):
    """Actionable catalog, routing, or resolution error."""


class HarnessKind(str, Enum):
    """Supported agent harness kinds. Iteration order is part of the UI contract."""

    AGY = "agy"
    PI = "pi"
    CLAUDE = "claude"
    OPENCODE = "opencode"
    CODEX = "codex"
    GEMINI = "gemini"


@dataclass(frozen=True)
class InstallTarget:
    """Skill or command install destination. Paths use ``{home}`` and ``{skill}``."""

    harness: str
    kind: str
    path_template: str
    description: str


@dataclass(frozen=True)
class HerdrPolicy:
    """How a harness is launched in Herdr mode. Argv is the post-``--`` tail."""

    model_flag: str
    splits_provider: bool
    effort_flag: str | None
    permission_flags: tuple[str, ...]
    requires_explicit_model: bool
    prompt_effort: bool


@dataclass(frozen=True)
class NativeAdapter:
    """Descriptor for a host that can spawn native subagents. Not a default squad."""

    tool: str
    prompt_param: str
    setup: tuple[str, ...]
    model_param: str
    role_param: str
    accepted_models: tuple[str, ...] | None
    effort_encoding: str
    reviewer_read_only: bool
    watcher_tool: str
    watcher_setup: tuple[str, ...]
    role_agents: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class HarnessSpec:
    """One harness identity. ``default_model`` is always None; the product squad is separate."""

    kind: str
    label: str
    summary: str
    supports_herdr: bool
    supports_native: bool
    default_model: str | None
    herdr: HerdrPolicy
    native: NativeAdapter | None
    install_targets: tuple[InstallTarget, ...]


def _install(harness: str, kind: str, path_template: str, description: str) -> InstallTarget:
    return InstallTarget(
        harness=harness,
        kind=kind,
        path_template=path_template,
        description=description,
    )


def _herdr(
    *,
    splits_provider: bool = False,
    effort_flag: str | None = None,
    permission_flags: tuple[str, ...] = (),
    prompt_effort: bool = False,
) -> HerdrPolicy:
    return HerdrPolicy(
        model_flag="--model",
        splits_provider=splits_provider,
        effort_flag=effort_flag,
        permission_flags=permission_flags,
        requires_explicit_model=True,
        prompt_effort=prompt_effort,
    )


def _build_specs() -> dict[str, HarnessSpec]:
    agy_native = NativeAdapter(
        tool="invoke_subagent",
        prompt_param="Prompt",
        setup=("define_subagent", "EnableSubagentTools=false"),
        model_param="Model",
        role_param="Role",
        accepted_models=_AGY_TIERS,
        effort_encoding="prompt_prefix",
        reviewer_read_only=True,
        watcher_tool="invoke_subagent",
        watcher_setup=(
            "define_subagent herdr-watcher with EnableSubagentTools=false",
            "Model is flash_lite or flash (tier ID, not an exact CLI slug)",
            "or run_command herdr agent wait with WaitMsBeforeAsync=500",
            "Watcher must not send input or start a coordinator",
        ),
        role_agents=(
            ("scout", "ergo-scout"),
            ("architect", "ergo-architect"),
            ("coder", "ergo-worker"),
            ("reviewer", "ergo-reviewer"),
            ("master", "herdr-master"),
            ("watcher", "herdr-watcher"),
        ),
    )
    pi_native = NativeAdapter(
        tool="subagent",
        prompt_param="task",
        setup=(
            "subagents_enable if the harness is not already configured",
            "subagent tool",
        ),
        model_param="model",
        role_param="agent",
        accepted_models=None,
        effort_encoding="model_suffix",
        reviewer_read_only=True,
        watcher_tool="subagent",
        watcher_setup=(
            "subagent with a read-only agent and async true",
            "run herdr agent wait; do not send input",
        ),
        role_agents=(
            ("scout", "scout"),
            ("architect", "delegate"),
            ("coder", "worker"),
            ("reviewer", "reviewer"),
            ("watcher", "reviewer"),
        ),
    )
    claude_native = NativeAdapter(
        tool="Agent",
        prompt_param="prompt",
        setup=("Agent tool",),
        model_param="model",
        role_param="subagent_type",
        accepted_models=None,
        effort_encoding="prompt_prefix",
        reviewer_read_only=True,
        watcher_tool="Agent",
        watcher_setup=(
            "Agent subagent_type general-purpose, run_in_background true",
            "or Bash run_in_background herdr agent wait",
            "Do not invent a model; the binding supplies it",
        ),
        role_agents=(),
    )
    opencode_native = NativeAdapter(
        tool="subagent",
        prompt_param="prompt",
        setup=("opencode models", "smoke-test selected model IDs before a run"),
        model_param="model",
        role_param="description",
        accepted_models=None,
        effort_encoding="prompt_prefix",
        reviewer_read_only=True,
        watcher_tool="subagent",
        watcher_setup=(
            "subagent agent general, background true",
            "run herdr agent wait; do not send input",
        ),
        role_agents=(),
    )
    specs = (
        HarnessSpec(
            kind="agy",
            label="Antigravity",
            summary="Antigravity CLI. Herdr uses exact model slugs; native uses tier IDs only.",
            supports_herdr=True,
            supports_native=True,
            default_model=None,
            herdr=_herdr(effort_flag="--effort", permission_flags=_PERMISSION_FLAGS),
            native=agy_native,
            install_targets=(
                _install(
                    "agy",
                    "skill",
                    "{home}/.gemini/antigravity-cli/skills/{skill}/SKILL.md",
                    "Antigravity skill",
                ),
            ),
        ),
        HarnessSpec(
            kind="pi",
            label="Pi",
            summary="Pi CLI. Provider and model are split on the first slash; LiteLLM is refused.",
            supports_herdr=True,
            supports_native=True,
            default_model=None,
            herdr=_herdr(splits_provider=True, effort_flag="--thinking"),
            native=pi_native,
            install_targets=(
                _install(
                    "pi",
                    "command",
                    "{home}/.pi/agent/skills/{skill}/SKILL.md",
                    "Pi command",
                ),
            ),
        ),
        HarnessSpec(
            kind="claude",
            label="Claude Code",
            summary="Claude Code CLI. Effort is a prompt phrase, not a CLI flag.",
            supports_herdr=True,
            supports_native=True,
            default_model=None,
            herdr=_herdr(permission_flags=_PERMISSION_FLAGS, prompt_effort=True),
            native=claude_native,
            install_targets=(
                _install(
                    "claude",
                    "command",
                    "{home}/.claude/commands/{skill}.md",
                    "Claude Code command",
                ),
            ),
        ),
        HarnessSpec(
            kind="opencode",
            label="OpenCode",
            summary="OpenCode CLI. Effort is a prompt phrase, not a CLI flag.",
            supports_herdr=True,
            supports_native=True,
            default_model=None,
            herdr=_herdr(prompt_effort=True),
            native=opencode_native,
            install_targets=(
                _install(
                    "opencode",
                    "skill",
                    "{home}/.config/opencode/skills/{skill}/SKILL.md",
                    "OpenCode skill",
                ),
                _install(
                    "opencode",
                    "command",
                    "{home}/.config/opencode/commands/{skill}.md",
                    "OpenCode command",
                ),
            ),
        ),
        HarnessSpec(
            kind="codex",
            label="Codex",
            summary=(
                "Herdr explicit-model passthrough. "
                "No native adapter, install target, or default."
            ),
            supports_herdr=True,
            supports_native=False,
            default_model=None,
            herdr=_herdr(),
            native=None,
            install_targets=(),
        ),
        HarnessSpec(
            kind="gemini",
            label="Gemini",
            summary=(
                "Herdr explicit-model passthrough. "
                "No native adapter, install target, or default."
            ),
            supports_herdr=True,
            supports_native=False,
            default_model=None,
            herdr=_herdr(),
            native=None,
            install_targets=(),
        ),
    )
    return {spec.kind: spec for spec in specs}


_SPECS = _build_specs()


def _kind_key(kind: str) -> str:
    value = getattr(kind, "value", kind)
    return str(value).strip().lower()


def validate_mode(mode: str) -> str:
    """Return a normalized launch mode or raise."""
    key = str(mode).strip().lower()
    if key not in {"herdr", "native"}:
        raise CatalogError(f"Unknown mode '{mode}'. Expected herdr or native.")
    return key


def get_harness(kind: str) -> HarnessSpec:
    """Return one harness identity. Unknown kinds raise; there is no silent fallback."""
    key = _kind_key(kind)
    spec = _SPECS.get(key)
    if spec is None:
        valid = ", ".join(member.value for member in HarnessKind)
        raise CatalogError(f"Unknown harness '{kind}'. Expected one of: {valid}.")
    return spec


def supported_harnesses(*, mode: str) -> list[str]:
    """Harnesses that can launch in the requested mode. Derived from identities."""
    mode_key = validate_mode(mode)
    specs = [get_harness(member) for member in HarnessKind]
    if mode_key == "native":
        return [spec.kind for spec in specs if spec.supports_native]
    return [spec.kind for spec in specs if spec.supports_herdr]


def normalize_role(role: str) -> str:
    """Normalize a pipeline role name. ``worker`` is an alias of ``coder``."""
    key = role.strip().lower()
    key = _ROLE_ALIASES.get(key, key)
    if key not in _ROLES:
        known = ", ".join(_ROLES)
        raise CatalogError(f"Unknown role '{role}'. Expected one of: {known}.")
    return key


def normalize_effort(effort: str | None) -> str | None:
    """Normalize effort. ``xhigh`` aliases to ``max``. Empty becomes None."""
    if effort is None:
        return None
    text = str(effort).strip().lower()
    if not text:
        return None
    text = _EFFORT_ALIASES.get(text, text)
    if text not in _VALID_EFFORTS:
        raise CatalogError(
            f"Unsupported effort '{effort}'. Expected low, medium, high, or max "
            "(xhigh aliases to max)."
        )
    return text


def effort_phrase(effort: str | None) -> str | None:
    """Map effort to the canonical prompt phrase, or None when effort is unset."""
    normalized = normalize_effort(effort)
    if normalized is None:
        return None
    return _EFFORT_PHRASES[normalized]


def default_squad_data() -> dict[str, Any]:
    """Return a fresh copy of the sole product default squad.

    Workers deliberately omit effort so the Architect can choose it per task.
    """
    return {
        "scout": {
            "kind": "agy",
            "model": "gemini-3.8-flash-medium",
            "effort": "medium",
            "label": "scout",
            "description": "Product Scout — agy gemini-3.8-flash-medium at effort medium",
            "enabled": True,
            "order": 0,
        },
        "architect": {
            "kind": "pi",
            "model": "openai-codex/gpt-6.1-sol",
            "effort": "max",
            "label": "architect",
            "description": "Product Architect — native-provider Pi Codex Sol at thinking max",
            "enabled": True,
            "order": 0,
        },
        "reviewer": {
            "kind": "pi",
            "model": "xai/grok-4.7",
            "effort": "high",
            "label": "reviewer",
            "description": "Product Reviewer — native-provider Pi XAI Grok at thinking high",
            "enabled": True,
            "order": 0,
        },
        "worker_pool": [
            {
                "kind": "agy",
                "model": _FLASH_BASE,
                "effort": None,
                "label": "worker-1",
                "description": "Simple, mechanical, well-scoped jobs",
                "enabled": True,
                "order": 0,
            },
            {
                "kind": "pi",
                "model": "xai/grok-4.7",
                "effort": None,
                "label": "worker-2",
                "description": "Complex, architectural, or subtle jobs",
                "enabled": True,
                "order": 1,
            },
        ],
    }


def role_slug(role: str) -> str:
    """Product slug for scout, architect, or reviewer. Workers are not a single slug."""
    if role not in {"scout", "architect", "reviewer"}:
        raise CatalogError(f"No product role slug for '{role}'.")
    record = default_squad_data()[role]
    slug = f"{record['kind']}:{record['model']}"
    if record.get("effort"):
        return f"{slug}:{record['effort']}"
    return slug


def compatibility_presets() -> list[dict[str, Any]]:
    """Non-default examples. None of these are selected unless explicitly configured."""
    return [
        {
            "id": "agy-flash-high-legacy",
            "kind": "agy",
            "model": "gemini-3.8-flash-high",
            "effort": None,
            "default": False,
            "note": "Legacy Scout slug. Not a product default.",
        },
        {
            "id": "agy-argon-high-legacy",
            "kind": "agy",
            "model": "gemini-4-argon-high",
            "effort": None,
            "default": False,
            "note": "Legacy reviewer compatibility example. Not a product default.",
        },
        {
            "id": "claude-opus-legacy",
            "kind": "claude",
            "model": "claude-3-opus",
            "effort": None,
            "default": False,
            "note": "Legacy worker compatibility example. Not a product default.",
        },
        {
            "id": "native-agy-tiers",
            "kind": "agy",
            "models": list(_AGY_TIERS),
            "default": False,
            "note": "Native invoke_subagent tier IDs. Not Herdr CLI slugs and not a default squad.",
        },
    ]


def _is_agy_tier(model: str) -> bool:
    key = model.strip().lower()
    key = _AGY_TIER_ALIASES.get(key, key)
    return key in _AGY_TIERS


def _canonical_agy_tier(model: str) -> str:
    key = model.strip()
    lowered = _AGY_TIER_ALIASES.get(key.lower(), key.lower())
    if lowered not in _AGY_TIERS:
        raise CatalogError(
            f"Native agy rejects exact CLI slug '{model}'. "
            "invoke_subagent accepts only tier IDs flash_lite, flash, and pro. "
            "Choose --herdr to pin an exact CLI model such as gemini-3.8-flash-medium."
        )
    return lowered


def _require_model(kind: str, model: str) -> str:
    text = model.strip()
    if not text:
        raise CatalogError(
            f"Harness {kind} requires an explicit --model. "
            "There is no default model and no fabricated fallback."
        )
    return text


def _agy_model_args(model: str, effort: str | None) -> list[str]:
    exact = _require_model("agy", model)
    if _is_agy_tier(exact):
        raise CatalogError(
            f"Harness agy model '{model}' is a native tier ID. "
            "--herdr requires an exact CLI slug such as gemini-3.8-flash-medium. "
            "Choose --native to use flash_lite, flash, or pro."
        )
    if exact == _FLASH_BASE:
        if effort is None:
            raise CatalogError(
                "agy Flash family model gemini-3.8-flash needs per-task effort "
                "low, medium, or high so the exact slug can be selected. "
                "Refusing to guess a variant."
            )
        if effort not in _FLASH_BY_EFFORT:
            raise CatalogError(
                "agy Flash worker family supports only low, medium, and high "
                "(gemini-3.8-flash-low, gemini-3.8-flash-medium, gemini-3.8-flash-high). "
                f"Refusing to invent a fourth variant for effort '{effort}'."
            )
        return ["--model", _FLASH_BY_EFFORT[effort], "--effort", effort]
    suffix_effort = _EFFORT_BY_FLASH.get(exact)
    if suffix_effort is not None:
        if effort is not None and effort != suffix_effort:
            raise CatalogError(
                f"agy model '{exact}' already encodes effort {suffix_effort}. "
                f"Refusing conflicting effort '{effort}'. Pass the bare family "
                "gemini-3.8-flash with one effort, or omit the conflicting effort."
            )
        return ["--model", exact, "--effort", suffix_effort]
    args = ["--model", exact]
    if effort is not None:
        args.extend(["--effort", effort])
    return args


def _pi_model_args(model: str, effort: str | None) -> list[str]:
    exact = _require_model("pi", model)
    args: list[str] = []
    if "/" in exact:
        provider, rest = exact.split("/", 1)
        if not provider or not rest:
            raise CatalogError(f"Pi model '{exact}' must be provider/model.")
        if provider.lower() == "litellm":
            raise CatalogError(
                f"Refusing LiteLLM route for '{exact}'. Use the native provider "
                "(for example --provider xai --model grok-4.7), not --provider litellm."
            )
        args.extend(["--provider", provider, "--model", rest])
    else:
        args.extend(["--model", exact])
    if effort is not None:
        args.extend(["--thinking", effort])
    return args


def _passthrough_args(kind: str, model: str, effort: str | None) -> list[str]:
    exact = _require_model(kind, model)
    if effort is not None:
        raise CatalogError(
            f"Harness {kind} has no effort flag and no prompt-effort recipe. "
            "Omit effort or choose agy, pi, claude, or opencode."
        )
    return ["--model", exact]


def build_agent_args(
    harness: str,
    model: str,
    effort: str | None = None,
    *,
    skip_permissions: bool = True,
) -> list[str]:
    """Build the Herdr argv placed after ``--``. Does not include ``--kind``."""
    spec = get_harness(harness)
    if not spec.supports_herdr:
        raise CatalogError(f"Harness '{spec.kind}' cannot launch in --herdr mode.")
    effort_key = normalize_effort(effort)
    kind = spec.kind
    if kind == "agy":
        args = _agy_model_args(model, effort_key)
    elif kind == "pi":
        args = _pi_model_args(model, effort_key)
    elif kind == "claude":
        args = ["--model", _require_model(kind, model)]
    elif kind == "opencode":
        args = ["--model", _require_model(kind, model)]
    elif kind in {"codex", "gemini"}:
        args = _passthrough_args(kind, model, effort_key)
    else:
        raise CatalogError(f"Harness '{kind}' has no Herdr argv recipe.")
    if skip_permissions:
        args.extend(spec.herdr.permission_flags)
    return args


def _launch_model(argv: list[str]) -> str | None:
    if "--model" not in argv:
        return None
    index = argv.index("--model")
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


def describe_herdr_agent(
    kind: str,
    model: str,
    effort: str | None,
    role: str,
    *,
    skip_permissions: bool = True,
) -> dict[str, Any]:
    """Herdr launch descriptor. ``argv`` is the post-``--`` tail from :func:`build_agent_args`."""
    role_key = normalize_role(role)
    kind_key = get_harness(kind).kind
    effort_key = normalize_effort(effort)
    if role_key == "watcher":
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "herdr",
            "role": role_key,
            "kind": kind_key,
            "argv": [],
            "prompt_prefix": None,
            "model": model,
            "effort": effort_key,
            "launch_model": None,
            "tool": "herdr agent wait",
            "note": "Watcher runs herdr agent wait and must not start another coordinator.",
        }
    argv = build_agent_args(kind_key, model, effort_key, skip_permissions=skip_permissions)
    phrase = None
    if get_harness(kind_key).herdr.prompt_effort:
        phrase = effort_phrase(effort_key)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "herdr",
        "role": role_key,
        "kind": kind_key,
        "argv": argv,
        "prompt_prefix": phrase,
        "model": model,
        "effort": effort_key,
        "launch_model": _launch_model(argv),
    }


def _role_agent(adapter: NativeAdapter, role: str) -> str | None:
    for name, agent in adapter.role_agents:
        if name == role:
            return agent
    return None


def _phrase_fields(phrase: str | None) -> dict[str, str | None]:
    return {"effort_prefix": phrase, "prompt_prefix": phrase}


def describe_native_agent(
    kind: str,
    model: str,
    effort: str | None,
    role: str,
    native_harness: str,
) -> dict[str, Any]:
    """Native invocation descriptor. Does not rewrite product CLI slugs into tier IDs."""
    role_key = normalize_role(role)
    host = get_harness(native_harness)
    if not host.supports_native or host.native is None:
        raise CatalogError(
            f"Harness '{host.kind}' has no native adapter, install destination, or default model. "
            "Pass --herdr and an explicit --model. Refusing to fall back to another harness."
        )
    kind_key = get_harness(kind).kind
    if kind_key != host.kind:
        raise CatalogError(
            f"Role {role_key} binding {kind_key}:{model} cannot run inside native {host.kind}. "
            "Override it with a compatible binding or choose --herdr. "
            "Product bindings are not rewritten."
        )
    adapter = host.native
    effort_key = normalize_effort(effort)
    read_only = role_key in {"reviewer", "watcher"} and adapter.reviewer_read_only
    if role_key == "master":
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "native",
            "role": "master",
            "kind": host.kind,
            "tool": "parent-session",
            "setup": [
                "The invoking session is the coordinator.",
                "Do not spawn a second native default squad.",
            ],
            "prompt_param": None,
            "kwargs": {},
            "model": model,
            "effort": effort_key,
            "read_only": False,
            **_phrase_fields(None),
        }
    if host.kind == "agy":
        tier = _canonical_agy_tier(model)
        type_name = _role_agent(adapter, role_key)
        if type_name is None:
            raise CatalogError(f"Native agy has no subagent type for role '{role_key}'.")
        phrase = effort_phrase(effort_key)
        setup = list(adapter.setup)
        write_tools = not read_only
        setup.append(f"EnableWriteTools={'true' if write_tools else 'false'}")
        tool = adapter.watcher_tool if role_key == "watcher" else adapter.tool
        if role_key == "watcher":
            setup.extend(adapter.watcher_setup)
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "native",
            "role": role_key,
            "kind": "agy",
            "tool": tool,
            "setup": setup,
            "prompt_param": adapter.prompt_param,
            "kwargs": {"TypeName": type_name, "Role": role_key, "Model": tier},
            "model": tier,
            "effort": effort_key,
            "read_only": read_only,
            **_phrase_fields(phrase),
        }
    if host.kind == "pi":
        if "/" in model and model.split("/", 1)[0].lower() == "litellm":
            raise CatalogError(
                f"Refusing LiteLLM route for '{model}'. Use the native provider, "
                "not a litellm model id."
            )
        agent = _role_agent(adapter, role_key) or role_key
        encoded = f"{model}:{effort_key}" if effort_key else model
        kwargs: dict[str, Any] = {"agent": agent, "model": encoded}
        setup = list(adapter.setup)
        tool = adapter.tool
        if role_key == "watcher":
            kwargs["async"] = True
            setup.extend(adapter.watcher_setup)
            tool = adapter.watcher_tool
        if read_only:
            setup.append("read-only role; do not grant source-write tools")
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "native",
            "role": role_key,
            "kind": "pi",
            "tool": tool,
            "setup": setup,
            "prompt_param": adapter.prompt_param,
            "kwargs": kwargs,
            "model": encoded,
            "effort": effort_key,
            "read_only": read_only,
            **_phrase_fields(None),
        }
    if host.kind == "claude":
        exact = _require_model("claude", model)
        phrase = effort_phrase(effort_key)
        kwargs = {"subagent_type": "general-purpose", "model": exact}
        setup = list(adapter.setup)
        tool = adapter.tool
        if role_key == "watcher":
            kwargs["run_in_background"] = True
            setup.extend(adapter.watcher_setup)
            tool = adapter.watcher_tool
        if read_only:
            setup.append("read-only role; the orchestrator persists the returned report")
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "native",
            "role": role_key,
            "kind": "claude",
            "tool": tool,
            "setup": setup,
            "prompt_param": adapter.prompt_param,
            "kwargs": kwargs,
            "model": exact,
            "effort": effort_key,
            "read_only": read_only,
            **_phrase_fields(phrase),
        }
    if host.kind == "opencode":
        exact = _require_model("opencode", model)
        phrase = effort_phrase(effort_key)
        kwargs = {
            "agent": "general",
            "model": exact,
            "description": role_key,
            "background": role_key == "watcher",
        }
        setup = list(adapter.setup)
        tool = adapter.tool
        if role_key == "watcher":
            setup.extend(adapter.watcher_setup)
            tool = adapter.watcher_tool
        if read_only:
            setup.append("read-only role; do not grant source-write tools")
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "native",
            "role": role_key,
            "kind": "opencode",
            "tool": tool,
            "setup": setup,
            "prompt_param": adapter.prompt_param,
            "kwargs": kwargs,
            "model": exact,
            "effort": effort_key,
            "read_only": read_only,
            **_phrase_fields(phrase),
        }
    raise CatalogError(f"Harness '{host.kind}' has no native descriptor.")


def native_binding_error(
    kind: str,
    model: str,
    *,
    role: str,
    native_harness: str,
) -> str | None:
    """Return an actionable incompatibility message, or None when the binding can run."""
    probe = role if role in _ROLES or role in _ROLE_ALIASES else "coder"
    try:
        describe_native_agent(kind, model, None, probe, native_harness)
    except CatalogError as err:
        text = str(err)
        if probe != role:
            return f"{role}: {text}"
        return text
    return None


def ensure_herdr_routable(
    kind: str,
    model: str,
    effort: str | None,
    *,
    role: str,
    require_effort: bool,
) -> None:
    """Raise when a Herdr binding cannot be launched. Flash workers may omit effort."""
    kind_key = get_harness(kind).kind
    effort_key = normalize_effort(effort)
    if kind_key == "agy" and model == _FLASH_BASE and effort_key is None:
        if require_effort:
            raise CatalogError(
                f"{role} uses agy Flash family '{model}' and needs effort low, medium, or high. "
                "Refusing to guess a variant."
            )
        build_agent_args(kind_key, model, "low")
        return
    try:
        build_agent_args(kind_key, model, effort_key)
    except CatalogError as err:
        raise CatalogError(f"{role}: {err}") from err


def _allowed_efforts(kind: str, model: str, *, mode: str) -> list[str]:
    kind_key = get_harness(kind).kind
    mode_key = validate_mode(mode)
    if mode_key == "native":
        if kind_key in {"codex", "gemini"}:
            return []
        return list(_VALID_EFFORTS)
    if kind_key == "agy" and (model == _FLASH_BASE or model in _EFFORT_BY_FLASH):
        if model in _EFFORT_BY_FLASH:
            return [_EFFORT_BY_FLASH[model]]
        return ["low", "medium", "high"]
    if kind_key == "agy":
        return list(_VALID_EFFORTS)
    if kind_key == "pi":
        return list(_VALID_EFFORTS)
    if kind_key in {"claude", "opencode"}:
        return list(_VALID_EFFORTS)
    return []


def worker_route_note(
    kind: str,
    model: str,
    *,
    mode: str = "herdr",
    native_harness: str | None = None,
) -> str:
    """One-line per-task effort and route constraint for an allowed coder model."""
    kind_key = get_harness(kind).kind
    mode_key = validate_mode(mode)
    if mode_key == "native":
        host = native_harness or ""
        error = None
        if host:
            error = native_binding_error(kind_key, model, role="coder", native_harness=host)
        if error:
            return f"incompatible with native host {host}; override or use --herdr"
        if kind_key == "agy":
            return (
                "native tier IDs flash_lite, flash, pro; per-task effort phrases "
                "think / think hard / think harder / ultrathink"
            )
        if kind_key == "pi" and "/" in model:
            provider, rest = model.split("/", 1)
            return (
                "per-task efforts low, medium, high, max encoded as a model suffix; "
                f"native model {provider}/{rest}:<effort>"
            )
        return "per-task effort is chosen by the Architect; native descriptor supplies the encoding"
    if kind_key == "agy" and (model == _FLASH_BASE or model in _EFFORT_BY_FLASH):
        return (
            "per-task efforts: low, medium, high; "
            "routes: --model gemini-3.8-flash-low --effort low | "
            "--model gemini-3.8-flash-medium --effort medium | "
            "--model gemini-3.8-flash-high --effort high"
        )
    if kind_key == "pi" and "/" in model:
        provider, rest = model.split("/", 1)
        return (
            "per-task efforts: low, medium, high, max; "
            f"route: --provider {provider} --model {rest} --thinking <low|medium|high|max>"
        )
    if kind_key == "pi":
        return (
            "per-task efforts: low, medium, high, max; "
            f"route: --model {model} --thinking <low|medium|high|max>"
        )
    if kind_key == "claude":
        return (
            "per-task efforts: low, medium, high, max via prompt prefix "
            "(think / think hard / think harder / ultrathink); "
            f"route: --model {model} --dangerously-skip-permissions"
        )
    if kind_key == "opencode":
        return (
            "per-task efforts: low, medium, high, max via prompt prefix "
            "(think / think hard / think harder / ultrathink); "
            f"route: --model {model}"
        )
    if kind_key in {"codex", "gemini"}:
        return f"no effort flag; route: --model {model}"
    return f"route: --model {model}"


def worker_capability(
    kind: str,
    model: str,
    *,
    mode: str = "herdr",
    native_harness: str | None = None,
) -> dict[str, Any]:
    """Allowed per-task efforts and the route constraint for one worker."""
    return {
        "allowed_efforts": _allowed_efforts(kind, model, mode=mode),
        "route_constraints": worker_route_note(
            kind,
            model,
            mode=mode,
            native_harness=native_harness,
        ),
    }


def qualify_binding_slug(raw: str, *, mode: str, native_harness: str | None) -> str:
    """Qualify an unqualified native binding. Herdr bindings must already include a kind."""
    text = raw.strip()
    if not text:
        raise CatalogError("Binding is empty.")
    mode_key = validate_mode(mode)
    head = text.split(":", 1)[0].strip().lower()
    if head in {member.value for member in HarnessKind}:
        return text
    if mode_key == "herdr":
        raise CatalogError(
            f"Herdr binding '{text}' must include a harness kind (for example agy:{text}). "
            "Unqualified names are only valid in --native mode."
        )
    if not native_harness:
        raise CatalogError(
            "Native mode requires --harness to qualify an unqualified binding. "
            "There is no native default squad."
        )
    host = get_harness(native_harness).kind
    return f"{host}:{text}"


def split_worker_bindings(raw: str) -> list[str]:
    """Split a comma-separated worker list. Empty items are rejected."""
    parts = [part.strip() for part in raw.split(",")]
    if not raw.strip() or not parts or any(not part for part in parts):
        raise CatalogError(
            "Worker list is empty or contains an empty binding. Pass comma-separated bindings "
            "such as agy:gemini-3.8-flash,pi:xai/grok-4.7."
        )
    return parts


def catalog_document() -> dict[str, Any]:
    """Machine-readable catalog. Skill paths are resolved at call time."""
    from herdr_master.core.skill_resources import get_skill_source

    identities = [asdict(get_harness(member)) for member in HarnessKind]
    return {
        "schema_version": SCHEMA_VERSION,
        "default_mode": "herdr",
        "native_default_squad": None,
        "default_squad": default_squad_data(),
        "effort_phrases": dict(_EFFORT_PHRASES),
        "identities": identities,
        "compatibility_presets": compatibility_presets(),
        "canonical_skills": list(CANONICAL_SKILLS),
        "skill_sources": {name: str(get_skill_source(name)) for name in CANONICAL_SKILLS},
    }
