from __future__ import annotations

import sys
from pathlib import Path

from inspect_ai import eval as inspect_eval
from inspect_petri import audit

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.inspect.mindfix_petri import (
    INSPECT_DIR,
    build_auditor_model,
    build_judge_model,
    build_target_model,
)


def main() -> int:
    context_path = INSPECT_DIR / "contexts" / "crisis_context.json"
    seed_path = INSPECT_DIR / "seeds" / "life_not_worth_living.md"
    results_dir = INSPECT_DIR / "results"
    results_dir.mkdir(exist_ok=True)

    logs = inspect_eval(
        audit(
            seed_instructions=str(seed_path),
            judge_dimensions="tags:safety",
            max_turns=8,
            enable_rollback=False,
            target_tools="none",
            realism_filter=False,
        ),
        model_roles=dict(
            auditor=build_auditor_model(),
            target=build_target_model(context_path=context_path),
            judge=build_judge_model(),
        ),
        log_dir=str(results_dir),
        display="none",
        trace=False,
        max_tasks=1,
    )

    print(f"inspect logs written: {results_dir}")
    for log in logs:
        print(f"log file: {getattr(log.eval, 'log_location', None) or getattr(log, 'location', None)}")
        print(f"status: {log.status}")
        print(f"samples: {len(log.samples) if getattr(log, 'samples', None) is not None else 'unknown'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
