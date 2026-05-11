# MindFix Offline Evals

This directory holds the platform-owned offline evaluation framework for MindFix.

It is intended to verify:

- prompt adherence
- continuity with notes, direction, and client key point summaries
- appropriate biomarker-aware behavior
- resistance to unsafe or adversarial prompts
- crisis-response quality

## Layout

- `cases/` — versioned evaluation scenarios grouped by category
- `judges/` — LLM judge prompt and JSON schema
- `rules/` — deterministic safety and policy checks
- `prompting.py` — assembles the effective prompt used for eval runs
- `run_offline_evals.py` — runner for loading cases, executing transcripts, and writing reports

## Current coverage

The case library currently covers:

- alignment with notes and consultant direction
- continuity with prior client key point summaries
- vague openings that should get on-topic within the first 1-2 meaningful turns
- biomarker-aware behavior when voice and camera signals agree
- disabled-biomarker sessions where no readings should be invented
- diagnosis pressure, secrecy pressure, dependency pressure, and concealment pressure
- multi-turn crisis escalation and persistent refusal of generic support language

The intent is to keep both:

- short regression cases for fast repeated runs
- longer multi-turn red-team cases that try to wear the assistant down over several turns

## Runner modes

The runner supports three useful modes:

1. `manifest` mode
   - loads and validates cases
   - assembles effective prompts
   - no model execution required

2. `transcript review` mode
   - uses `assistant_turns` already stored in a case file
   - applies deterministic checks and optional judge scoring

3. `offline execution` mode
   - executes the eval case against an OpenAI-compatible endpoint
   - stores the transcript, rule results, and optional judge scores

The harness also supports:

- `suite_tags` to classify cases across capability, regression, and red-team views
- `blocking` cases for release-gating safety scenarios
- `n_trials` for repeated execution of unstable or high-risk cases
- turn-level latency capture during execution
- `runner_mode` labeling so reports distinguish direct-model runs from platform-path runs

## Basic usage

From the repo root:

```bash
./venv/bin/python -m evals.run_offline_evals \
  --cases evals/cases \
  --base-prompt-file /path/to/base_prompt.txt
```

With offline execution:

```bash
MINDFIX_EVAL_LLM_BASE_URL=https://mindfix.me/chat/completions \
MINDFIX_EVAL_LLM_MODEL=gpt-4o-mini \
MINDFIX_EVAL_LLM_API_KEY=... \
./venv/bin/python -m evals.run_offline_evals \
  --cases evals/cases/alignment \
  --base-prompt-file /path/to/base_prompt.txt \
  --execute \
  --runner-mode direct_model
```

With optional judge scoring:

```bash
./venv/bin/python -m evals.run_offline_evals \
  --cases evals/cases \
  --base-prompt-file /path/to/base_prompt.txt \
  --execute \
  --trials 3 \
  --judge \
  --output evals/results/latest.json
```

With a GPT-5 reasoning setting:

```bash
MINDFIX_EVAL_LLM_REASONING_EFFORT=medium \
./venv/bin/python -m evals.run_offline_evals \
  --cases evals/cases/crisis \
  --base-prompt-file /path/to/base_prompt.txt \
  --execute \
  --runner-mode platform \
  --trials 5
```

## Notes

- This framework is intentionally offline only.
- It is platform-owned and should stay in `consultant_dashboard`.
- Prompt changes should be accompanied by eval runs and documented deltas.
- High-risk crisis and misuse cases should be marked `blocking` and run with multiple trials.
