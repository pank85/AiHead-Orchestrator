"""Experimental, explicitly selected Core-hosted AiHead lifecycle.

Pi receives the initial prompt as an argument, which Core exposes in terminal
metadata. A successful spawn does not establish that Pi processed the prompt.
"""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from shutil import which

from herdr_master.core.catalog import build_agent_args, supported_harnesses
from herdr_master.core.config import load_project_config
from herdr_master.herdr import core_client
from herdr_master.pipeline.core_prompt import build_core_aihead_prompt


class CoreAiHeadError(Exception):
    """Discovery or lifecycle could not be completed safely."""


@dataclass(frozen=True)
class CoreAiHeadResult:
    address: str
    created: bool
    state: str
    prompt_status: str


async def ensure_core_aihead(
    *, backend: str, config_path=None, cwd=None, kind=None, model=None,
    effort=None, dangerously_skip_permissions=None, task=None, automode=None,
    executable="hc", timeout=5.0,
) -> CoreAiHeadResult:
    """Find or start a head on the selected Core, without retrying mutations.

    ``argv_unconfirmed`` means the prompt was passed to Pi, not that it was
    delivered or processed. Reuse never sends an additional message.
    """
    if backend != "core":
        raise CoreAiHeadError("Core lifecycle requires backend='core'")
    project = Path(cwd or Path.cwd()).expanduser().resolve()
    if not project.is_dir():
        raise CoreAiHeadError(f"project directory does not exist: {project}")
    config = load_project_config(config_path or project)
    head = config.ai_head
    binding = head.binding
    kind = getattr(kind, "value", kind) or binding.kind.value
    model = model if model is not None else binding.model
    effort = effort if effort is not None else binding.effort
    effort = getattr(effort, "value", effort)
    skip = (head.dangerously_skip_permissions if dangerously_skip_permissions is None
            else dangerously_skip_permissions)
    digest = sha256(str(project).encode("utf-8")).hexdigest()
    name = f"aihead-{digest[:24]}"
    labels = {"aihead.role": "head", "aihead.project": digest}

    listing = await core_client.terminal_list(timeout=timeout, executable=executable)
    if not isinstance(listing, dict) or not isinstance(listing.get("terminals"), list):
        raise CoreAiHeadError("invalid Core terminal list; refusing to start")
    matches = []
    for terminal in listing["terminals"]:
        if not isinstance(terminal, dict) or terminal.get("state") not in ("live", "finished"):
            raise CoreAiHeadError("invalid Core terminal entry; refusing to start")
        if terminal["state"] != "live":
            continue
        terminal_labels = terminal.get("labels")
        if not isinstance(terminal_labels, dict):
            raise CoreAiHeadError("invalid Core terminal labels; refusing to start")
        by_name = terminal.get("name") == name
        by_label = all(terminal_labels.get(k) == v for k, v in labels.items())
        if by_name != by_label:
            raise CoreAiHeadError("conflicting Core AiHead identity; refusing to start")
        if by_name:
            matches.append(terminal)
    if len(matches) > 1:
        raise CoreAiHeadError("multiple Core AiHeads match this project")
    if matches:
        address = matches[0].get("address")
        if not isinstance(address, str) or not address.strip():
            raise CoreAiHeadError("existing Core AiHead has no address")
        if task is not None:
            raise CoreAiHeadError(f"existing Core AiHead {address}; task was not sent")
        state = "spawn_pending" if matches[0].get("reason") == "spawn_pending" else "live"
        return CoreAiHeadResult(address, False, state, "not_sent")

    # Only Pi's Core prompt and effort delivery have been verified.
    if kind != "pi" or kind not in supported_harnesses(mode="core"):
        raise CoreAiHeadError(
            f"unsupported Core harness: {kind}; only pi is verified for Core launch"
        )
    exe = which(kind)
    if not exe:
        raise CoreAiHeadError(f"{kind} executable not found")
    argv = [str(Path(exe).resolve()), *build_agent_args(kind, model, effort, skip_permissions=skip)]
    prompt = build_core_aihead_prompt(
        head_config=head, initial_task=task, origin_cwd=str(project),
        automode=head.automode if automode is None else automode,
        available=config.models.available,
    )
    # Keep the prompt from being parsed as an option.
    argv.extend(["--", prompt])
    try:
        result = await core_client.request(
            "terminal.run", {"argv": argv, "cwd": str(project), "name": name,
                             "labels": labels, "no_parent": True, "wait": False},
            timeout=timeout, executable=executable,
        )
    except core_client.CoreClientError as exc:
        raise CoreAiHeadError(
            f"Core start failed or outcome unknown for {name}; inspect before retrying: {exc}"
        ) from exc
    if (not isinstance(result, dict) or not isinstance(result.get("address"), str)
            or not result["address"].strip()):
        raise CoreAiHeadError(f"Core start outcome unknown for {name}; inspect before retrying")
    address = result["address"]
    if result.get("reason") == "spawn_pending":
        return CoreAiHeadResult(address, True, "spawn_pending", "unknown")
    state = result.get("state")
    if state not in ("live", "finished"):
        raise CoreAiHeadError(f"Core start state unknown for {address}; inspect before retrying")
    return CoreAiHeadResult(address, True, state, "argv_unconfirmed")
