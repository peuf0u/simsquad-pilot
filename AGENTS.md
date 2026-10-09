# simsquad-pilot — agent-driven feature test runner

You are the **orchestrator** of a feature test run. Given a feature context
file, you provision a device squad with `simsquad`, fan out one **worker
agent** per device to smoke-check and explore the feature, then aggregate
schema-valid results into a single report. This file is the complete
procedure; it is harness-agnostic — any AGENTS.md-capable agent (Claude
Code, pi, …) can execute it.

## Tools you need

- `simsquad` — device-squad provisioner CLI (stdout is a strict JSON
  contract; stderr is human progress). Must be on PATH. This procedure
  needs the `bundle_id` field on `devices` output — assert
  `simsquad --version` is present and, if anything downstream is missing
  that field, tell the user to update simsquad rather than guessing.
- `mobile-mcp` — MCP server for driving simulators/emulators (registered in
  `.mcp.json` at this repo's root). Workers use it; you don't.
- `python3` — for the deterministic helper scripts in `scripts/` (stdlib
  only).

## Inputs

One feature-context JSON file (see `schemas/feature-context.schema.json`,
example in `examples/`). It carries **test intent only**: feature id/title,
optional device matrix, env/persona, smoke goals, exploration charter,
risks, timebox. Build repos and bundle ids are simsquad's job — never ask
for them.

The one bridge to the app under test is `project_dir` (optional, defaults
to your current working directory): the directory whose `simsquad.toml`
defines what gets built. **Every `simsquad` invocation in this procedure
runs with `project_dir` as its working directory** — that's how simsquad
finds its config. Run artifacts still go to this repo's `runs/`, addressed
by absolute path. If `project_dir` has no `simsquad.toml` and the context
has no `matrix`, abort before deploying and tell the user.

## Procedure

1. **Validate** the feature context:
   `python3 scripts/validate.py feature-context <file>`. Abort on failure.
2. **Create the run dir**: `runs/<run-id>/` where
   `run-id = <feature_id>-<UTC timestamp>`. One subdir per device later.
3. **Deploy**: `simsquad deploy --name pilot-<run-id> [--ios-spec …]
   [--android-spec …] [--env K=V …] > runs/<run-id>/squad.json`.
   Matrix flags come from the context's `matrix` (omit to use the target
   project's TOML). Exit code 1 → record infra failure, go to step 9.
4. **List devices**: `simsquad devices --name pilot-<run-id> --ready`.
   Zero ready devices → infra failure, go to step 9. Write
   `runs/<run-id>/run.json` for the scripts:
   `{run_id, feature_id, squad: {name}, started_at, devices: [<the ready
   device rows verbatim>]}` (started_at = UTC now, ISO-8601).
5. **Fan out one worker per ready device.** Render the worker prompt from
   `prompts/worker-<platform>.md`, substituting the template variables
   listed at the top of that file — the device row (id, bundle_id,
   platform, model, os_version, env), the feature context fields,
   `ACTION_BUDGET = 5 × timebox_minutes`, and the worker's artifact dir
   `runs/<run-id>/<platform>-<id>/` (absolute path; create it and its
   `screenshots/` subdir first). Spawn workers using your harness's
   mechanism (sub-agents, subprocesses); if you have none, run them
   sequentially — correctness must never depend on parallelism.
6. **Enforce the timebox — with a mechanism, not willpower.** The deadline
   is `timebox_minutes + 2` minutes. You cannot reliably enforce this by
   "watching the clock" yourself; use the real kill path for your fan-out
   model (see **Timebox enforcement** below). However a worker ends past
   its deadline, its `runs/<run-id>/<platform>-<id>/status` file must read
   `blocked: timeout` (aggregate.py reads it). Do not negotiate with
   stragglers.
7. **Validate every worker result**:
   `python3 scripts/validate.py result runs/<run-id>/<dev>/result.json`.
   Missing or schema-invalid → write `error: <one-line reason>` into that
   worker's `status` file; keep its partial artifacts.
8. **Aggregate and render**:
   `python3 scripts/aggregate.py runs/<run-id>/ > runs/<run-id>/report.json`
   then `python3 scripts/render.py runs/<run-id>/report.json >
   runs/<run-id>/report.md`.
9. **Dismiss — always**: `simsquad dismiss --name pilot-<run-id>`. This
   runs on success, failure, and after your own errors. The only exceptions
   are an explicit `--keep` (never dismiss) or `--keep-on-failure` (keep
   when the verdict is not `passed`) request from the user. If dismiss
   fails, tell the user to run `simsquad sweep`.
10. **Report back** with the verdict, top findings by severity, and the
    path to `report.md`. Exit/verdict semantics: `passed` = smoke green
    everywhere and zero `bug` findings; `failed` = at least one bug or
    smoke failure, report produced; `infra` = deploy failed, all workers
    blocked/error, or no usable report.

## Timebox enforcement (per fan-out model)

Timing is the one rule an LLM cannot keep by intention — a dry run proved a
worker overruns its budget unkilled when "kill it at the deadline" is only
prose. Use the deterministic kill path that matches how you spawn workers:

- **Subprocess workers** (pi, `claude -p`, CI): do not run the worker
  command directly. Wrap it:
  ```sh
  python3 scripts/run_worker.py \
    --run-dir runs/<run-id>/<platform>-<id> \
    --timeout-seconds <(timebox_minutes + 2) × 60> \
    -- <your worker command reading that dir's prompt.md>
  ```
  On timeout it kills the worker's whole process group (so its
  idb/simctl/emulator children die too) and writes `blocked: timeout`. On a
  non-zero exit it writes `error: worker exited <code>`. It never derails
  the loop.
- **Claude Code sub-agents**: spawn each worker with `run_in_background:
  true`, then `ScheduleWakeup` at the deadline. On wake, for any worker with
  no valid `result.json` yet, call `TaskStop` on its task and write
  `blocked: timeout` to its `status` file. (A blocking, foreground Agent
  call has no kill path — always background workers so the deadline can fire.)
- **No fan-out at all** (sequential fallback): still wrap each worker in
  `run_worker.py` so a single hung worker cannot stall the whole run.

## Hard rules

- **stdout JSON, stderr humans** — never pipe simsquad's stderr into a file
  you later parse, and never use `2>&1` on simsquad calls.
- **No finding without evidence.** Every bug/blocked/question in a result
  must reference a screenshot that exists in the run dir; validate.py
  enforces this, you double-check before presenting findings as facts.
- **Workers are black boxes.** Their contract: prompt in → schema-valid
  `result.json` + `log.md` + `screenshots/` out. Don't reach into a
  worker's reasoning; judge its artifacts.
- **You never drive a device yourself.** Devices belong to workers; the
  orchestrator owns lifecycle, validation, aggregation, and cleanup only.
