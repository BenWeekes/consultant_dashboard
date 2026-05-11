from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_petri import audit

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.inspect.mindfix_petri import (  # noqa: E402
    INSPECT_DIR,
    build_auditor_model,
    build_judge_model,
    build_target_model,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MindFix exploratory Inspect/Petri seeds.")
    parser.add_argument(
        "--context",
        default=str(INSPECT_DIR / "contexts" / "crisis_context.json"),
        help="Path to the dashboard-style context fixture.",
    )
    parser.add_argument(
        "--seeds",
        nargs="*",
        help="Optional explicit seed paths. Defaults to all markdown files in evals/inspect/seeds.",
    )
    parser.add_argument("--max-turns", type=int, default=8, help="Petri max turns per audit.")
    parser.add_argument(
        "--judge-dimensions",
        default="tags:safety",
        help="Judge dimensions expression passed to inspect_petri.audit.",
    )
    parser.add_argument(
        "--results-dir",
        default=str(INSPECT_DIR / "results"),
        help="Directory for Inspect log artifacts and generated summaries.",
    )
    return parser.parse_args()


def resolve_seed_paths(seed_args: list[str] | None) -> list[Path]:
    if seed_args:
        return [Path(seed).resolve() for seed in seed_args]
    return sorted((INSPECT_DIR / "seeds").glob("*.md"))


def score_summary(log: Any) -> dict[str, float]:
    scores: dict[str, float] = {}
    if not log.results:
        return scores
    for score in log.results.scores or []:
        metric = (score.metrics or {}).get("mean")
        if metric is not None:
            scores[score.name] = metric.value
    return scores


def run_seed(seed_path: Path, *, context_path: Path, results_dir: Path, max_turns: int, judge_dimensions: str) -> dict[str, Any]:
    logs = inspect_eval(
        audit(
            seed_instructions=str(seed_path),
            judge_dimensions=judge_dimensions,
            max_turns=max_turns,
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

    log_info = logs[0]
    log_path = Path(getattr(log_info.eval, "log_location", None) or getattr(log_info, "location", None))
    log = read_eval_log(str(log_path))
    return {
        "seed": seed_path.name,
        "log_path": str(log_path),
        "status": str(log.status),
        "completed_samples": log.results.completed_samples if log.results else None,
        "total_samples": log.results.total_samples if log.results else None,
        "scores": score_summary(log),
        "error": str(log.error.message) if getattr(log, "error", None) else None,
    }


def main() -> int:
    args = parse_args()
    started_at = datetime.now(UTC)
    context_path = Path(args.context).resolve()
    seed_paths = resolve_seed_paths(args.seeds)
    if not seed_paths:
        raise SystemExit("No seed files found.")

    results_dir = Path(args.results_dir).resolve()
    results_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "started_at": started_at.isoformat(),
        "context_path": str(context_path),
        "max_turns": args.max_turns,
        "judge_dimensions": args.judge_dimensions,
        "runs": [],
    }

    print(f"running {len(seed_paths)} inspect seeds against {context_path.name}")
    for seed_path in seed_paths:
        print(f"- {seed_path.name}")
        run = run_seed(
            seed_path,
            context_path=context_path,
            results_dir=results_dir,
            max_turns=args.max_turns,
            judge_dimensions=args.judge_dimensions,
        )
        summary["runs"].append(run)
        print(f"  status={run['status']} samples={run['completed_samples']}/{run['total_samples']}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary["completed_at"] = datetime.now(UTC).isoformat()
    summary_path = results_dir / f"{stamp}_inspect_suite_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"summary written: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
