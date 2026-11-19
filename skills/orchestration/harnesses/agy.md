## Antigravity (`agy`) Harness Notes

- Check `[orchestration].agy_mode` in `.herdr-master.toml` (default: `"subagents"`):
  - **`subagents`**: use `invoke_subagent` with `EnableWriteTools: false` for read-only roles (Scout, Architect, Reviewer) and tier IDs only (`flash_lite`, `flash`, `pro`).
  - **`swarm`**: use `swarm(action="add", ...)` with exact `agy` model IDs via `model`, `workspace: "inherit"` for read-only roles, `workspace: "share"` for parallel Coders, and `swarm(action="kill", ...)` for cleanup after landing.
