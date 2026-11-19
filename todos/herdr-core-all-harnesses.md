# Support All Harnesses in Herdr Core

## Goal

Support every applicable AiHead harness with Herdr Core while retaining the existing Herdr TUI backend and its behavior. Do not claim a harness or workflow is supported until its actual runtime route has been verified.

## Current State

- The catalog contains `agy`, `pi`, `claude`, `opencode`, `codex`, `gemini`, `hermes`, and `kilo`.
- Core catalog resolution currently lists `pi`, `agy`, `claude`, `opencode`, and `hermes`, but actual Core AiHead and task launches are Pi-only. A catalog entry is not proof of a working launch.
- Ergo, Council, Orchestration, and Herdr-Master launch workflows are not yet available through the Core backend and must remain fail-closed until verified.

## Work

- [ ] Define per-harness Core capabilities for catalog resolution, head and worker launch, prompt delivery, and workflow roles; reject unsupported routes before mutation.
- [ ] Verify each installed harness's interactive launch, model/provider and effort flags, permissions, initial-task delivery, status, and resume behavior. Preserve explicit bindings, enabled workers, frozen squads, and model allowlists without silent substitution.
- [ ] Implement Core head and worker launch for all applicable harnesses, preserving project-scoped discovery, direct-parent communication, explicit standalone behavior, and owned-only cleanup.
- [ ] Extend AiHead, Ergo, Council, Orchestration, and Herdr-Master workflows where applicable, preserving their legacy Herdr paths, review gates, worktree isolation, and ownership safeguards. Remove each fail-closed guard only after its corresponding Core workflow is verified.
- [ ] Handle ambiguous spawn/send outcomes without blind retries, and do not infer completion from missing status or an unverified report. Document prompt visibility in Core terminal metadata.
- [ ] Update the CLI, catalog, setup, documentation, and App integration with a tested backend and harness capability matrix.

## Verification

- [ ] Parameterize tests across the complete harness catalog, covering configuration/model precedence, unsupported routes, parent/child reporting, ambiguous outcomes, and nonblocking coordination.
- [ ] Exercise supported routes against an isolated real Core with unique session, socket, and temporary user state using safe fake harnesses; stop only the test-owned server. Then verify real interactive startup, task delivery, and parent reports where available.
- [ ] Run the relevant legacy Herdr regression suite and verify Windows and Linux behavior without modifying active user sessions or services. Clearly distinguish simulated tests from real-agent verification.

Remove this todo only after the work above is complete and verified.
