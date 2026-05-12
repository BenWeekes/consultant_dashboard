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
- per-turn review labels in each transcript:
  - `safe`
  - `concerning`
  - `off_topic`
  - `too_generic`

Use this path when you want repeatable, browser-readable transcripts for:

- life-not-worth-living pressure
- self-harm / secrecy pressure
- addiction normalization pressure

Current scripted review shape:

- fixed user-side adversarial scripts live in `evals/inspect/scripts/`
- each category can be rerun independently with:
  - `./venv/bin/python evals/inspect/generate_review_transcripts.py --categories <category>`
- the review markdown is the primary artifact for manual inspection
- the JSON bundle is useful for aggregate counts, but manual transcript review still matters most for borderline cases
- `evals/inspect/summarize_results.py` can turn category bundles into a comparison-ready markdown / JSON summary

Example summary run:

```bash
cd consultant_dashboard
./venv/bin/python evals/inspect/summarize_results.py \
  --provider openai \
  --mode chat \
  --prompt-version therapy_current \
  --bundle life_not_worth_living=evals/inspect/reviews/<life_bundle>.json \
  --bundle addiction_normalization=evals/inspect/reviews/<addiction_bundle>.json \
  --bundle self_harm_secrecy=evals/inspect/reviews/<self_harm_bundle>.json
```

This emits a table with:

- cases
- clean cases
- cases with failures
- soft / hard fail counts
- average / median / worst-turn latency
- turn-label counts:
  - `safe`
  - `concerning`
  - `off_topic`
  - `too_generic`

Recommended comparison columns across providers:

- `provider`
- `model`
- `mode`
- `prompt_version`
- `reasoning_effort`
- `category`
- `cases`
- `clean_cases`
- `cases_with_failures`
- `soft_fails`
- `hard_fails`
- `avg_latency_seconds`
- `median_latency_seconds`
- `worst_turn_seconds`
- `safe_turns`
- `concerning_turns`
- `off_topic_turns`
- `too_generic_turns`

Recommended artifact outputs:

- markdown summary in `evals/inspect/reviews/*_summary_<provider>_<mode>.md`
- JSON summary in `evals/inspect/reviews/*_summary_<provider>_<mode>.json`

Use the same schema when comparing:

- OpenAI chat
- Anthropic
- xAI
- OpenAI Realtime

Important scoring note:

- case-level rule failures and turn-level labels are intentionally different
- case-level failures are the release-style rule checks
- turn-level labels are a reviewer aid for scanning long transcripts quickly
- crisis-first openings may still produce `opening_ignored_direction` soft fails at case level even when the individual turns are correctly labeled `safe`

## Near-term adoption plan

Keep the split explicit:

- fixed harness in `evals/` remains the release gate
- Inspect / Petri in `evals/inspect/` is for exploratory discovery

Next work should focus on:

- adding more therapy-specific Petri seeds
- adding a small helper for repeated seed runs
- comparing candidate prompts/models against the same seed set
- documenting any remaining auditor-tool compatibility gaps before enabling rollback
