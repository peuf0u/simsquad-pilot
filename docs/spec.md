# simsquad-pilot — design spec

This document defines `simsquad-pilot`, the first execution layer above
`simsquad`: an
agent-driven exploratory and smoke-test runner for one feature, producing a
reviewable report with evidence.

`simsquad` remains the device-squad provisioner. The execution layer consumes
its CLI contract, drives the devices, and owns test intent, agent prompts,
result aggregation, and reporting.

## Decisions (2026-06-12)

- **The runner is an agent, not a program.** An orchestrator agent receives a
  feature context, holds tools (`simsquad`, mobile-mcp, a few helper
  scripts), fans out one worker agent per device, and assembles the report.
- **Portable via `AGENTS.md`, not `CLAUDE.md`.** The orchestrator procedure
  lives in an `AGENTS.md` any harness can read — Claude Code, pi
  (earendil-works/pi), Codex-style CLIs. No harness is a dependency; pi was
  evaluated and is a supported runtime, not a foundation.
- **Only the worker loop needs intelligence.** Everything deterministic —
  schema validation, aggregation, report rendering — is a small script the
  agent calls, so behavior stays identical across harnesses and models.
- **The durable contract is four artifacts**, pinned regardless of harness:
  1. the feature-context input schema,
  2. the `report.json` output schema,
  3. the worker prompt template,
  4. the worker completion contract (each worker writes one schema-valid
     `result.json` into its run dir, or it didn't happen).
- **No Maestro in the MVP.** Smoke is an agent-driven checklist through
  mobile-mcp — generating YAML assertions for localized UI strings nobody
  has seen is the flaky path. Phase 2 promotes *successful exploratory
  traces* into frozen Maestro flows for cheap regression re-runs, after the
  agent has seen the real labels.

## Goal

Given a feature context, the runner should:

1. Provision a fresh squad with the requested device matrix.
2. Run a short deterministic-in-intent smoke checklist on each device.
3. Let a worker agent explore the feature on each assigned device.
4. Capture screenshots, screen observations, actions, failures, and notes.
5. Emit one machine-readable report and one human-readable summary.
6. Dismiss the squad unless explicitly told to keep it.

This MVP is for fast product feedback and regression discovery. It is not a
replacement for a full scripted E2E suite.

## Non-goals

- No leasing or device ownership inside `simsquad`.
- No test execution inside `internal/ios` or `internal/android`.
- No long-running device lab daemon.
- No cloud-device abstraction in v1.
- No automatic PR comments until the local report format stabilizes.
- No promise that exploratory steps are reproducible. Reproducible flows are
  generated later from successful or interesting traces (phase 2, Maestro).
- No harness framework of our own. The runner is instructions + schemas +
  scripts; the agent harness is whatever the user already runs.

## Architecture

Two tiers of agents, one tier of scripts:

```text
orchestrator agent  (reads AGENTS.md; any harness)
  tools: simsquad CLI, helper scripts, harness fan-out mechanism
  owns:  lifecycle, timebox enforcement, schema validation, cleanup
    |
    +-- worker agent per device  (spawned by the harness)
          tools: mobile-mcp (standard MCP), simsquad reset
          owns:  smoke checklist + exploration on exactly one device
          output: result.json + log.md + screenshots/ in its run dir
    |
deterministic helper scripts (no model, no deps beyond python3 stdlib)
  validate.py   — result.json / report.json against schemas, hard fail
  aggregate.py  — merge per-device result.json into report.json
  render.py     — report.json → report.md
```

Why this split survives a harness swap: simsquad is a plain CLI, mobile-mcp
is standard MCP, and the scripts are the parts that must never vary with the
model. The only harness-specific behavior is *how workers are spawned* — see
"Fan-out per harness" below.

## Inputs

The feature context carries **test intent only**. Build repos, device
matrices, and bundle ids are simsquad's job: repos and the default matrix
come from `simsquad.toml`, and `bundle_id` arrives on every
`simsquad devices` row, derived from the actual build. Duplicating them here
only creates drift.

```json
{
  "feature_id": "login-magic-link",
  "title": "Login with magic link",
  "matrix": {
    "ios": ["iPhone 17:iOS 26.4:1"]
  },
  "env": {
    "api": "staging",
    "persona": "alice"
  },
  "smoke": [
    "App launches without crashing",
    "Login entry point is visible",
    "Feature entry point is reachable"
  ],
  "explore": {
    "charter": "Explore the magic-link login path, including invalid email, resend, and successful login.",
    "risks": [
      "Error states may be unclear",
      "Deep link may fail to return to the app",
      "Loading state may get stuck"
    ],
    "timebox_minutes": 8
  }
}
```

`matrix` is optional and maps verbatim onto `--ios-spec` / `--android-spec`
(omit it to use the project's TOML matrix). `env` passes through
`simsquad deploy --env`. `project_dir` (optional, defaults to the
orchestrator's cwd) is the app project whose `simsquad.toml` defines the
build — simsquad resolves its config by walking up from the working
directory, so every simsquad invocation runs with `project_dir` as cwd.
Everything else never touches simsquad state.

## Lifecycle

```text
feature context
  |
  v
orchestrator validates context (scripts/validate.py)
  |
  v
simsquad deploy --name agent-<run-id> ... > squad.json
  |
  v
simsquad devices --name agent-<run-id> --ready
  |
  v
fan out: one worker agent per ready device
  |     each worker, on its device:
  |       1. reset app state          (simsquad reset / terminate+wipe)
  |       2. smoke checklist          (mobile-mcp, verdict per item)
  |       3. reset app state again    (explore starts from known state)
  |       4. exploratory loop         (charter, risks, timebox)
  |       5. write result.json + log.md + screenshots/
  |
  v
orchestrator enforces timebox        (stale worker -> kill, mark blocked)
  |
  v
scripts/validate.py per result.json  (schema-invalid -> worker FAILED)
  |
  v
scripts/aggregate.py -> report.json
scripts/render.py    -> report.md
  |
  v
simsquad dismiss --name agent-<run-id>   (always, unless --keep[-on-failure])
```

Two rules the orchestrator enforces rather than requests:

- **Timebox is a kill, not a suggestion.** A worker past
  `timebox_minutes + grace` is terminated and recorded as `blocked` with
  reason `timeout`. Workers cannot be trusted to self-limit.
- **Dismiss always runs.** Cleanup happens on success, failure, and
  orchestrator error alike; `--keep` / `--keep-on-failure` are the only
  exceptions. No leaked simulators.

State reset between passes is mandatory: smoke mutates app state, and
exploration must start from a known point. The reset belongs in `simsquad`
(a `reset` verb: terminate app + wipe data container, both platforms) so the
platform landmines — `idb file rm` on Apple Silicon, `pm clear` on Android —
live in the provisioner, not in every worker prompt. Until the verb exists,
the worker prompt carries the explicit per-platform commands.

## Smoke pass

The smoke pass is a checklist the worker verifies through mobile-mcp —
launch the app, look at the real screen, record a verdict per item:

```json
"smoke": {
  "status": "failed",
  "checks": [
    { "goal": "App launches without crashing", "verdict": "pass" },
    { "goal": "Login entry point is visible",  "verdict": "fail",
      "observation": "Home screen shows onboarding carousel; no login affordance",
      "screenshot": "screenshots/smoke-02.png" }
  ]
}
```

This reads the actual (possibly localized) UI instead of guessing label
strings, which is why Maestro generation was cut from the MVP. Smoke failures
do not cancel exploration unless the app cannot launch at all — a failed
smoke is useful context for the explorer.

## Exploratory pass

The worker prompt gives the agent:

- Feature title and charter.
- Smoke goals and the smoke results from its own pass.
- Known risks.
- Its device row from `simsquad devices` — `id`, `bundle_id`, `platform`,
  `model`, `os_version`, `env` (persona/credentials arrive here; there is no
  side-channel prereqs file).
- A strict timebox and action budget.

The loop is structured:

```text
observe screen (a11y tree + screenshot)
summarize current state
choose next action
execute via mobile-mcp
record action and evidence
repeat until charter complete, blocked, or budget exhausted
```

Findings are classified, and **no finding exists without evidence** — every
`bug`/`blocked`/`question` must reference at least one screenshot path that
exists in the run dir, or validate.py rejects the result:

- `bug`: likely product defect.
- `blocked`: cannot continue because setup, credentials, or environment failed.
- `question`: unclear expected behavior.
- `note`: useful observation, not a defect.

The worker prompt template also carries the platform quirks list (iOS:
"Save Password?" SpringBoard dialog, keyboard occlusion, re-list elements
after sheet dismissal, diacritics via direct key events; Android equivalent
grows as we learn). The quirks list is a maintained artifact — it is most of
what makes agent-driven UI testing reliable.

## Worker completion contract

A worker is a black box to the orchestrator. The contract:

- Input: a prompt file, a run dir, a device row, MCP access to mobile-mcp.
- Output: `result.json` (schema-valid), `log.md` (narrative), `screenshots/`.
- A worker that exits without a schema-valid `result.json` is recorded as
  `status: "error"` by the orchestrator; its partial artifacts are kept.

This contract is what keeps the runner model- and harness-agnostic: anything
that can read a prompt, call MCP tools, and write files can be a worker.

## Fan-out per harness

The only section that mentions harnesses by name:

- **Claude Code**: orchestrator spawns workers as background sub-agents (one
  per device), schedules a wakeup at the deadline, and on wake stops any
  still-running worker (`TaskStop`) and marks it `blocked: timeout`.
- **pi**: orchestrator drives workers as `pi` subprocesses (or via the SDK),
  each wrapped in `scripts/run_worker.py` for hard timeout enforcement.
- **Fallback (any harness)**: run workers sequentially, one device at a
  time, still wrapped in `run_worker.py`. Slower, identical artifacts.
  Correctness must never depend on parallelism.

Timebox enforcement is deterministic in every model (see AGENTS.md
*Timebox enforcement*): subprocess workers go through `run_worker.py`, which
kills the worker's whole process group on overrun and records the status;
sub-agent workers are background + deadline-wakeup + `TaskStop`. The prose
rule "kill it at the deadline" is never left to the orchestrator's judgment
— a dry run showed an LLM orchestrator cannot keep a wall-clock deadline by
intention.

## Report contract

The primary artifact is `report.json` (schema-validated). Shape:

```json
{
  "run_id": "agent-20260612-153000",
  "feature_id": "login-magic-link",
  "started_at": "2026-06-12T13:30:00Z",
  "finished_at": "2026-06-12T13:41:22Z",
  "squad": { "name": "agent-20260612-153000" },
  "summary": {
    "verdict": "failed",
    "devices_total": 1,
    "smoke_passed": 0,
    "smoke_failed": 1,
    "bugs": 2,
    "blocked": 0,
    "questions": 1
  },
  "devices": [
    {
      "platform": "ios",
      "id": "ABC-123",
      "model": "iPhone 17",
      "os_version": "iOS 26.4",
      "bundle_id": "com.example.app",
      "smoke": { "status": "failed", "checks": [] },
      "exploration": {
        "status": "completed",
        "actions": [
          {
            "index": 1,
            "kind": "tap",
            "target": "Log in",
            "observation": "Login screen opened",
            "screenshot": "artifacts/ios-ABC-123/step-001.png"
          }
        ],
        "findings": [
          {
            "type": "bug",
            "severity": "high",
            "title": "Magic link opens browser but does not return to app",
            "evidence": ["artifacts/ios-ABC-123/step-008.png"],
            "repro_steps": [
              "Launch app",
              "Open login",
              "Enter alice test email",
              "Open received magic link"
            ],
            "expected": "The app completes login and shows the home screen.",
            "actual": "The browser stays open after the link is tapped."
          }
        ]
      }
    }
  ]
}
```

`report.md` is derived from the JSON and optimized for review: verdict,
devices tested, smoke results, top findings by severity with repro steps and
screenshots, open questions, links to raw artifacts.

Runner exit codes (defined now because changing them later breaks CI):

- `0` — smoke passed everywhere, no `bug` findings.
- `1` — at least one `bug` finding or smoke failure; report was produced.
- `2` — infrastructure failure: deploy failed, all workers `blocked`/`error`,
  or no usable report.

## Repository layout

The runner is a standalone repo (`simsquad-pilot`), separate from simsquad:
it is a *consumer* of the released CLI contract, and `AGENTS.md` at a repo
root is what other harnesses actually discover.

```
AGENTS.md                          # orchestrator procedure (the skill)
CLAUDE.md                          # thin pointer at AGENTS.md
.mcp.json                          # mobile-mcp registration
schemas/
  feature-context.schema.json
  result.schema.json               # per-worker
  report.schema.json
prompts/
  worker-ios.md                    # template incl. quirks list
  worker-android.md
scripts/
  validate.py                      # stdlib only
  aggregate.py
  render.py
examples/
  feature-context.example.json
docs/spec.md                       # this document
runs/                              # artifacts, gitignored
```

## First implementation slices

1. Schemas: feature-context, result, report. `validate.py`.
2. `AGENTS.md` orchestrator procedure: deploy → devices → fan-out →
   validate → aggregate → dismiss, with the timebox and cleanup rules.
3. Worker prompt template (iOS first) with the quirks list and the
   completion contract.
4. `aggregate.py` + `render.py`.
5. Dry run against a real squad on one iOS device; iterate the worker
   prompt until findings are evidence-clean.
6. `--keep` / `--keep-on-failure` semantics + leaked-device check
   (`simsquad sweep` as the backstop).
7. `simsquad reset` verb; remove the per-platform wipe commands from the
   worker prompts.
8. Android worker prompt + platform routing.

Phase 2 (explicitly out of MVP): promote successful traces to Maestro flows;
JUnit XML export from aggregate.py for CI; PR-comment rendering.

## Success criteria

The MVP is useful when a developer can hand any AGENTS.md-capable agent a
feature context and receive:

- A clear pass/fail smoke verdict per device.
- A concise exploratory narrative.
- Evidence for every claimed bug.
- Repro steps good enough for a developer to act on.
- No leaked devices after the run unless requested.
- The same artifacts regardless of which harness or model ran it.
