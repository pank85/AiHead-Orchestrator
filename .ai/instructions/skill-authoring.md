# Skill Authoring & Per-Harness Composition

All canonical skills live under `skills/<name>/` and are composed per harness during `aihead skills install`.

## Structure

- **`skills/<name>/SKILL.md`** — Canonical base file (single source of truth). Contains the YAML frontmatter (`name`, `description`, etc.) and all shared, harness-agnostic workflow instructions.
- **`skills/<name>/harnesses/<harness>.md`** *(optional)* — Per-harness overlay (e.g., `agy.md`, `claude.md`, `pi.md`, `opencode.md`, `hermes.md`, `codex.md`, `kilo.md`).

## Writing Merge-Friendly & Composable Skills

1. **No YAML frontmatter in overlays**: Only `skills/<name>/SKILL.md` carries frontmatter. Overlays are appended directly after the base body (`base + "\n\n" + overlay`).
2. **Additive, self-contained sections**: Start each overlay with a clear `## <Harness Name> (<kind>) Harness Notes` heading. Do not duplicate or restate shared rules from `SKILL.md`.
3. **One harness per file**: Keep harness-specific behavior isolated in `harnesses/<harness>.md` so changes to different harnesses never collide during merges.
4. **Bundle new overlays in the wheel**: Whenever you add a new `skills/<name>/harnesses/<harness>.md`, add it to `[tool.hatch.build.targets.wheel.force-include]` in `pyproject.toml`.
5. **Re-install to refresh composed outputs**: `aihead skills install` materializes composed skills into `~/.cache/herdr-master/skills/<harness>/<name>/SKILL.md` (and symlinks directly to `SKILL.md` when no overlay exists).
