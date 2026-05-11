# MindFix Inspect Spike

This directory contains the first `inspect_ai` / `inspect_petri` adoption spike for MindFix.

It is intentionally separate from the existing fixed-case harness in `evals/`.

Use this area for:

- exploratory multi-turn audits
- seed-based adversarial probing
- early Inspect integration experiments

Do not use this area as the primary release gate.

## Layout

- `contexts/` — sample session context fixtures for the target prompt
- `seeds/` — Petri-style exploratory seed instructions
- `mindfix_petri.py` — shared helpers for loading prompt/context and building models
- `run_smoke.py` — minimal runnable smoke audit against the MindFix-compatible target
- `run_suite.py` — small multi-seed exploratory runner with JSON summary output
- `scripts/` — fixed adversarial user-side scripts for direct therapy-target review
- `generate_review_transcripts.py` — runs those fixed scripts and writes markdown transcripts
- `reviews/` — generated markdown/json review bundles

## Target

The target model is wired to the same OpenAI-compatible Chat Completions path used by MindFix:

- provider: `openai-api`
- service name: `mindfix`
- base URL default: `http://127.0.0.1:8101`

Important implementation detail:

- the local target endpoint defaults to streaming
- Inspect needs explicit `stream: false` in `extra_body`
- the helper code in `mindfix_petri.py` sets that automatically
- the current smoke task runs with `enable_rollback=False`
- this avoids a Petri auditor restart tool schema that the current OpenAI-compatible chat path rejects

## Running the smoke audit

From the repo root:

```bash
cd consultant_dashboard
./venv/bin/python evals/inspect/run_smoke.py
```

By default this will:

- load the current therapy base prompt from `../agent-samples/simple-backend/.env`
- inject the sample crisis context in `contexts/crisis_context.json`
- run one seed against the target with Petri in conversation-only mode
- write Inspect logs under `evals/inspect/results/`

Run the small exploratory suite:

```bash
cd consultant_dashboard
./venv/bin/python evals/inspect/run_suite.py
```

This will:

- run every seed in `evals/inspect/seeds/`
- write normal Inspect `.eval` logs
- write a compact JSON summary under `evals/inspect/results/`

Run fixed scripted adversarial reviews:

```bash
cd consultant_dashboard
./venv/bin/python evals/inspect/generate_review_transcripts.py
```

This will:

- run the fixed scripted review cases under `evals/inspect/scripts/`
- call the real therapy target directly with the real prompt/context assembly
- write readable markdown transcripts under `evals/inspect/reviews/`
- write an aggregate review markdown/json bundle

## Environment

The smoke runner resolves defaults from local files, but you can override:

- `MINDFIX_INSPECT_TARGET_BASE_URL`
- `MINDFIX_INSPECT_TARGET_API_KEY`
- `MINDFIX_INSPECT_TARGET_MODEL`
- `MINDFIX_INSPECT_AUDITOR_MODEL`
- `MINDFIX_INSPECT_JUDGE_MODEL`
- `MINDFIX_INSPECT_REASONING_EFFORT`

If `MINDFIX_INSPECT_TARGET_API_KEY` is unset, the runner will try to load `LLM_API_KEY` from `../server-custom-llm/node/.env`.

## What this spike proves

This spike is successful if:

- Inspect can run against the MindFix-compatible target
- the target uses the real current therapy prompt and session context shape
- at least one exploratory seed produces a plausible audit transcript
- logs are reviewable

Once that works, the next step is to:

- expand the seed library
- tune judge dimensions
- document the integration in `docs/ai`
