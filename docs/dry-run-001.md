# Dry run 001 — 2026-06-12, onboarding/registration flow on a real iOS app

First full flight of the AGENTS.md procedure (slice 5). Orchestrator:
Claude Code session; worker: one sub-agent driving an iPhone 17 / iOS 26.4
simsquad sim via the idb/simctl CLI substitution (mobile-mcp was not loaded
in the session — the worker contract survived the tool swap, which is the
harness-agnosticism story working).

**Outcome:** verdict FAILED — smoke 3/3 passed, exploration completed,
2 bugs (a broken in-app link; a text field accepting input past its
specified length limit), 5 questions, 1 note. Every finding evidence-backed; `result.json`
validated on first try after the worker self-checked with validate.py.
Squad dismissed cleanly; no leaked simulators.

## Lessons → changes

1. **Timebox enforcement was prose, not mechanism.** The worker ran ~14 min
   against a 10+2 budget and was not killed — this orchestrator had no
   timer wired up. **Resolved (2026-06-16):** `scripts/run_worker.py` wraps
   subprocess workers with a hard timeout that kills the process group and
   writes `blocked: timeout`; AGENTS.md *Timebox enforcement* specifies the
   per-fan-out kill path (subprocess → run_worker.py; Claude Code sub-agent
   → background + deadline wakeup + TaskStop). The worker's self-imposed
   action budget remains a useful second line of defence.
2. **Stale provisioner binary almost mimicked a contract bug** — `devices`
   lacked `bundle_id` because the installed simsquad predated the field
   (added in simsquad 0.2.0). Consider asserting a minimum simsquad version
   in step 1 (e.g. parse `simsquad --version`, which reports a real version
   for every install method since 0.3.0).
3. **simsquad observation:** `ios-provision` logged `installd-wait-timeout`
   on iOS 26.4 but the install succeeded. **Resolved in simsquad 0.3.0:**
   the probe matched a launchd label that no longer exists on current
   runtimes.
4. **Worker self-validation pays.** Telling the worker to run validate.py
   on its own result.json before exiting produced a first-try-valid result.
   Promoted into the worker prompt as a standard step (already present).
5. **Non-English UI was handled correctly** by quoting labels verbatim
   (the app under test was Czech-localized) — the no-translation rule held
   up and made repro steps unambiguous.
