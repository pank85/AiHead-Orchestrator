## Antigravity (`agy`) Harness Notes

- Put the base skill's mandatory first-prompt directive in `Prompt` for `invoke_subagent` or `brief` for `swarm(action="add")`, after any required effort/persona prefix. Both routes use only the invoking supervisor's native result/message channel. Treat a blocking call's terminal result/error as its outcome; only dispatch detached work if the adapter can query the exact returned run identity.

- Check `[orchestration].agy_mode` in `.herdr-master.toml` (default: `"subagents"`):
  - **`subagents`**: use `invoke_subagent` with `EnableWriteTools: false` for read-only roles (Scout, Architect, Reviewer) and tier IDs only (`flash_lite`, `flash`, `pro`).
  - **`swarm`**: use `swarm(action="add", ...)` with exact `agy` model IDs via `model`, `workspace: "inherit"` for read-only roles, `workspace: "share"` for parallel Coders, and `swarm(action="kill", ...)` for cleanup after landing.
