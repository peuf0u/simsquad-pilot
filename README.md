# simsquad-pilot

> **Archived.** simsquad-pilot is now part of
> [simsquad](https://github.com/peuf0u/simsquad) 0.4.0 as installable agent
> skills: run `simsquad skill install` in your app repo and ask your agent to
> "verify …". See
> [agent testing](https://github.com/peuf0u/simsquad/blob/main/docs/agent-testing.md).
> This repo is read-only and gets no further changes.

Agent-driven exploratory and smoke-test runner for mobile features, built on
top of [simsquad](https://github.com/peuf0u/simsquad). The squad gets
deployed; pilot flies the mission.

Give any AGENTS.md-capable agent harness (Claude Code, pi, …) a feature
context file, and pilot will:

1. Provision a fresh simulator/emulator squad via `simsquad`.
2. Run an agent-driven smoke checklist on each device.
3. Explore the feature per device against a charter, within a timebox.
4. Emit `report.json` (schema-validated) + `report.md` with screenshot
   evidence for every finding.
5. Dismiss the squad — always, unless told to keep it.

## Layout

```
AGENTS.md          orchestrator procedure (the skill — start here)
CLAUDE.md          pointer for Claude Code
.mcp.json          mobile-mcp registration
schemas/           feature-context / result / report JSON Schemas
prompts/           worker prompt templates (per platform, incl. quirks list)
scripts/           deterministic helpers: validate / aggregate / render
examples/          example feature context
docs/spec.md       full design spec and decisions
runs/              run artifacts (gitignored)
```

## Running a feature test

1. **One-time setup**: install simsquad 0.3.0 or newer
   (`brew install peuf0u/tap/simsquad`) and make sure the app project you
   want to test has a `simsquad.toml` (or your feature context carries a
   `matrix`).
2. **Write a feature context** — copy
   `examples/feature-context.example.json`, set `project_dir` to the app
   project, describe the smoke goals and exploration charter.
3. **Start an agent session in this directory**:

   ```sh
   cd simsquad-pilot
   claude        # or any AGENTS.md-capable harness (pi, …)
   ```

   Claude Code will offer to load the project's `.mcp.json` (mobile-mcp) on
   first run — approve it. `CLAUDE.md` routes the agent to `AGENTS.md`,
   which is the complete orchestrator procedure.
4. **Ask for the run**:

   > Run the feature test for my-feature.json

   The agent deploys a squad, fans out one worker per device, and ends by
   dismissing the squad. Add "keep the squad" / "keep it on failure" to the
   request for `--keep` / `--keep-on-failure` semantics.
5. **Read the result**: `runs/<run-id>/report.md` (human) and
   `report.json` (machine), with per-device `log.md`, `result.json`, and
   `screenshots/` alongside.

For pi or another harness: point it at this directory so it reads
`AGENTS.md`, and register mobile-mcp with the same pinned command from
`.mcp.json` (`npx -y @mobilenext/mobile-mcp@0.0.59`) in that harness's MCP
config.

## Requirements

- [simsquad](https://github.com/peuf0u/simsquad) 0.3.0+ on PATH — `brew install peuf0u/tap/simsquad`
- Node.js — `mobile-mcp` is fetched via `npx` per `.mcp.json` (version pinned
  there; bump deliberately, its tool names are baked into the worker prompts)
- `python3` (stdlib only) for `scripts/`
- Xcode + simulators / Android SDK + emulators, as required by the matrix

## Status

Pre-MVP. Implementation slices and design decisions in
[docs/spec.md](docs/spec.md).

## License

MIT
