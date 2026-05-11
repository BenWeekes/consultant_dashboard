# Eval Stack

This repo now has two evaluation layers for AI-therapy work.

## 1. Product-owned fixed harness

Primary release-gating harness:

- `consultant_dashboard/evals/`

This layer owns:

- fixed JSON cases
- deterministic rule checks
- latency capture
- HTML / JSON reporting
- blocking crisis and misuse regressions

Use this layer for:

- prompt changes
- model comparisons
- dashboard-context continuity checks
- release gating

## 2. Inspect / Petri exploratory spike

Exploratory audit layer:

- `consultant_dashboard/evals/inspect/`

This layer is intentionally separate from the fixed harness and is used for:

- Petri seed-based exploratory audits
- multi-turn adversarial probing
- early `inspect_ai` / `inspect_petri` integration work

Current files:

- `evals/inspect/mindfix_petri.py`
- `evals/inspect/run_smoke.py`
- `evals/inspect/run_suite.py`
- `evals/inspect/generate_review_transcripts.py`
- `evals/inspect/contexts/`
- `evals/inspect/seeds/`
- `evals/inspect/scripts/`
- `evals/inspect/reviews/`
- `evals/inspect/results/`

## Target wiring

The Inspect spike targets the same local OpenAI-compatible chat path used by the therapy stack:

- provider: `openai-api`
- service name: `mindfix`
- base URL default: `http://127.0.0.1:8101`

The target prompt is assembled from:

- the live therapy base prompt in `../agent-samples/simple-backend/.env`
- dashboard-style session context via `evals.prompting.assemble_effective_prompt(...)`

This keeps exploratory audits aligned with the real therapy prompt shape.

## Current compatibility note

The first working smoke path runs with:

- `enable_rollback=False`

Reason:

- the current OpenAI-compatible chat path rejects one Petri auditor restart-tool schema
- disabling rollback/restart avoids that schema incompatibility while keeping the target-side therapy interaction intact

This is an auditor-tool compatibility limitation, not a therapy-target limitation.

## Smoke test

Run from repo root:

```bash
cd consultant_dashboard
./venv/bin/python -m py_compile evals/inspect/mindfix_petri.py evals/inspect/run_smoke.py
./venv/bin/python evals/inspect/run_smoke.py
```

Expected result:

- an Inspect `.eval` log is written under `evals/inspect/results/`
- log status is `success`

Useful inspection helper:

```bash
cd consultant_dashboard
./venv/bin/python - <<'PY'
from inspect_ai.log import read_eval_log
log = read_eval_log('evals/inspect/results/<logfile>.eval')
print(log.status)
print(log.results)
PY
```

Small multi-seed run:

```bash
cd consultant_dashboard
./venv/bin/python evals/inspect/run_suite.py
```

This produces:

- one `.eval` log per seed run
- one JSON suite summary in `evals/inspect/results/`

Fixed scripted adversarial review run:

```bash
cd consultant_dashboard
./venv/bin/python evals/inspect/generate_review_transcripts.py
```

This produces:

- one markdown transcript per scripted adversarial case
- one aggregate markdown/json review bundle in `evals/inspect/reviews/`

Use this path when you want repeatable, browser-readable transcripts for:

- life-not-worth-living pressure
- self-harm / secrecy pressure
- addiction normalization pressure

## Near-term adoption plan

Keep the split explicit:

- fixed harness in `evals/` remains the release gate
- Inspect / Petri in `evals/inspect/` is for exploratory discovery

Next work should focus on:

- adding more therapy-specific Petri seeds
- adding a small helper for repeated seed runs
- comparing candidate prompts/models against the same seed set
- documenting any remaining auditor-tool compatibility gaps before enabling rollback
