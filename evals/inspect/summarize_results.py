from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class BundleSummary:
    category: str
    cases: int
    clean_cases: int
    cases_with_failures: int
    soft_fails: int
    hard_fails: int
    avg_latency_seconds: float
    median_latency_seconds: float
    worst_turn_seconds: float
    safe_turns: int
    concerning_turns: int
    off_topic_turns: int
    too_generic_turns: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize scripted adversarial review bundles.")
    parser.add_argument(
        "--bundle",
        action="append",
        required=True,
        help="Bundle mapping in the form category=/abs/or/relative/path.json",
    )
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--mode", default="chat")
    parser.add_argument("--prompt-version", default="therapy_current")
    parser.add_argument("--output-json", help="Optional JSON output path.")
    parser.add_argument("--output-md", help="Optional markdown output path.")
    return parser.parse_args()


def parse_bundle_mapping(raw: str) -> tuple[str, Path]:
    if "=" not in raw:
        raise SystemExit(f"Invalid --bundle value: {raw}")
    category, path = raw.split("=", 1)
    return category.strip(), Path(path.strip())


def load_rows(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def summarize_category(category: str, rows: list[dict[str, Any]]) -> BundleSummary:
    latencies = [float(row["average_latency_seconds"]) for row in rows if row.get("average_latency_seconds") is not None]
    maxes = [float(row["max_latency_seconds"]) for row in rows if row.get("max_latency_seconds") is not None]
    label_counts = Counter()
    clean_cases = 0
    cases_with_failures = 0
    soft_fails = 0
    hard_fails = 0

    for row in rows:
        failures = row.get("rule_failures") or []
        if failures:
            cases_with_failures += 1
        else:
            clean_cases += 1
        for failure in failures:
            if failure.get("severity") == "soft_fail":
                soft_fails += 1
            elif failure.get("severity") == "hard_fail":
                hard_fails += 1
        for turn in row.get("turns") or []:
            label = ((turn.get("turn_label") or {}).get("label") or "unknown").strip()
            label_counts[label] += 1

    return BundleSummary(
        category=category,
        cases=len(rows),
        clean_cases=clean_cases,
        cases_with_failures=cases_with_failures,
        soft_fails=soft_fails,
        hard_fails=hard_fails,
        avg_latency_seconds=round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
        median_latency_seconds=round(statistics.median(latencies), 3) if latencies else 0.0,
        worst_turn_seconds=round(max(maxes), 3) if maxes else 0.0,
        safe_turns=label_counts["safe"],
        concerning_turns=label_counts["concerning"],
        off_topic_turns=label_counts["off_topic"],
        too_generic_turns=label_counts["too_generic"],
    )


def render_markdown(model: str, reasoning_effort: str, provider: str, mode: str, prompt_version: str, summaries: list[BundleSummary]) -> str:
    lines = [
        "# Scripted Adversarial Summary",
        "",
        f"- provider: `{provider}`",
        f"- model: `{model}`",
        f"- mode: `{mode}`",
        f"- prompt_version: `{prompt_version}`",
        f"- reasoning_effort: `{reasoning_effort or 'none'}`",
        "",
        "| Category | Cases | Clean | Cases w/ Failures | Soft Fails | Hard Fails | Avg Latency | Median | Worst Turn | Safe Turns | Concerning | Off Topic | Too Generic |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for summary in summaries:
        lines.append(
            f"| {summary.category} | {summary.cases} | {summary.clean_cases} | {summary.cases_with_failures} | "
            f"{summary.soft_fails} | {summary.hard_fails} | {summary.avg_latency_seconds}s | "
            f"{summary.median_latency_seconds}s | {summary.worst_turn_seconds}s | {summary.safe_turns} | "
            f"{summary.concerning_turns} | {summary.off_topic_turns} | {summary.too_generic_turns} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    mappings = [parse_bundle_mapping(item) for item in args.bundle]
    all_rows: list[dict[str, Any]] = []
    summaries: list[BundleSummary] = []
    model = ""
    reasoning_effort = ""

    for category, path in mappings:
        rows = load_rows(path)
        if rows and not model:
            model = rows[0].get("model") or ""
            reasoning_effort = rows[0].get("reasoning_effort") or ""
        summaries.append(summarize_category(category, rows))
        all_rows.extend(rows)

    markdown = render_markdown(
        model=model,
        reasoning_effort=reasoning_effort,
        provider=args.provider,
        mode=args.mode,
        prompt_version=args.prompt_version,
        summaries=summaries,
    )
    print(markdown, end="")

    payload = {
        "provider": args.provider,
        "mode": args.mode,
        "prompt_version": args.prompt_version,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "categories": [summary.__dict__ for summary in summaries],
    }
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(payload, indent=2) + "\n")
    if args.output_md:
        Path(args.output_md).write_text(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
