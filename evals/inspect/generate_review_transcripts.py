from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.inspect.mindfix_petri import (  # noqa: E402
    INSPECT_DIR,
    effective_target_prompt,
    load_context,
    target_connection_settings,
)
from evals.prompting import biomarker_messages_for_turn  # noqa: E402
from evals.rules.policy_checks import run_policy_checks  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate fixed-script adversarial therapy transcripts for review.")
    parser.add_argument(
        "--scripts-dir",
        default=str(INSPECT_DIR / "scripts"),
        help="Root directory of adversarial script JSON files.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(INSPECT_DIR / "reviews"),
        help="Directory for generated markdown/json review artifacts.",
    )
    parser.add_argument(
        "--categories",
        nargs="*",
        help="Optional category folder names to limit execution.",
    )
    return parser.parse_args()


def load_scripts(root: Path, categories: list[str] | None = None) -> list[dict[str, Any]]:
    paths: list[Path] = []
    if categories:
        for category in categories:
            paths.extend(sorted((root / category).glob("*.json")))
    else:
        paths = sorted(root.rglob("*.json"))
    scripts: list[dict[str, Any]] = []
    for path in paths:
        script = json.loads(path.read_text())
        script["_path"] = str(path)
        scripts.append(script)
    return scripts


def make_client() -> tuple[OpenAI, dict[str, str]]:
    settings = target_connection_settings()
    client = OpenAI(api_key=settings["api_key"], base_url=settings["base_url"])
    return client, settings


def call_target(client: OpenAI, settings: dict[str, str], messages: list[dict[str, str]]) -> tuple[str, float]:
    started = time.perf_counter()
    response = client.chat.completions.create(
        model=settings["model"],
        reasoning_effort=settings["reasoning_effort"] or None,
        messages=messages,
        stream=False,
    )
    elapsed = time.perf_counter() - started
    return (response.choices[0].message.content or "").strip(), elapsed


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def run_script(client: OpenAI, settings: dict[str, str], script: dict[str, Any]) -> dict[str, Any]:
    context_path = REPO_ROOT / str(script["context_path"])
    session_context = load_context(context_path)
    prompt = effective_target_prompt(context_path)
    transcript: list[dict[str, str]] = [{"role": "system", "content": prompt}]
    turns: list[dict[str, Any]] = []
    assistant_turns: list[str] = []
    latencies: list[float] = []

    for turn_index, user_turn in enumerate(script.get("user_turns") or []):
        biomarker_messages = biomarker_messages_for_turn(
            turn_index, session_context.get("biomarker_events") or []
        )
        transcript.extend(biomarker_messages)
        transcript.append({"role": "user", "content": str(user_turn)})
        assistant_text, latency = call_target(client, settings, transcript)
        transcript.append({"role": "assistant", "content": assistant_text})
        assistant_turns.append(assistant_text)
        latencies.append(latency)
        turns.append(
            {
                "turn_index": turn_index + 1,
                "user": str(user_turn),
                "assistant": assistant_text,
                "latency_seconds": round(latency, 3),
            }
        )

    case = {
        "category": script["category"],
        "session_context": session_context,
    }
    rule_failures = run_policy_checks(case, assistant_turns)
    return {
        "id": script["id"],
        "category": script["category"],
        "description": script["description"],
        "context_path": str(context_path),
        "model": settings["model"],
        "reasoning_effort": settings["reasoning_effort"],
        "turns": turns,
        "average_latency_seconds": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "max_latency_seconds": round(max(latencies), 3) if latencies else None,
        "rule_failures": rule_failures,
    }


def write_transcript_markdown(result: dict[str, Any], output_dir: Path) -> Path:
    category_dir = output_dir / result["category"]
    category_dir.mkdir(parents=True, exist_ok=True)
    path = category_dir / f"{slugify(result['id'])}.md"
    lines = [
        f"# {result['id']}",
        "",
        f"- category: `{result['category']}`",
        f"- model: `{result['model']}`",
        f"- reasoning_effort: `{result['reasoning_effort'] or 'none'}`",
        f"- average_latency_seconds: `{result['average_latency_seconds']}`",
        f"- max_latency_seconds: `{result['max_latency_seconds']}`",
        "",
        "## Prompted Goal",
        "",
        result["description"],
        "",
        "## Rule Failures",
        "",
    ]
    if result["rule_failures"]:
        for failure in result["rule_failures"]:
            lines.append(
                f"- `{failure['name']}` `{failure['severity']}` — {failure['evidence']}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Transcript", ""])
    for turn in result["turns"]:
        lines.extend(
            [
                f"### Turn {turn['turn_index']}",
                "",
                f"**User**: {turn['user']}",
                "",
                f"**Assistant**: {turn['assistant']}",
                "",
                f"_latency: {turn['latency_seconds']}s_",
                "",
            ]
        )
    path.write_text("\n".join(lines).strip() + "\n")
    return path


def write_review_bundle(results: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"{stamp}_adversarial_review.json"
    md_path = output_dir / f"{stamp}_adversarial_review.md"
    json_path.write_text(json.dumps(results, indent=2) + "\n")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault(result["category"], []).append(result)

    lines = [
        "# Adversarial Review Bundle",
        "",
        f"Generated at: `{datetime.now(UTC).isoformat()}`",
        "",
    ]
    for category, items in grouped.items():
        lines.extend([f"## {category}", ""])
        for item in items:
            total_failures = len(item["rule_failures"])
            lines.extend(
                [
                    f"### {item['id']}",
                    "",
                    item["description"],
                    "",
                    f"- rule_failures: `{total_failures}`",
                    f"- average_latency_seconds: `{item['average_latency_seconds']}`",
                    "",
                    "First adversarial exchange:",
                    "",
                    f"- user: `{item['turns'][0]['user']}`",
                    f"- assistant: `{item['turns'][0]['assistant'][:300]}`",
                    "",
                ]
            )
    md_path.write_text("\n".join(lines).strip() + "\n")
    return json_path, md_path


def main() -> int:
    args = parse_args()
    scripts = load_scripts(Path(args.scripts_dir), args.categories)
    if not scripts:
        raise SystemExit("No script files found.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    client, settings = make_client()
    results: list[dict[str, Any]] = []
    print(f"running {len(scripts)} scripted adversarial reviews")
    for script in scripts:
        print(f"- {script['id']}")
        result = run_script(client, settings, script)
        transcript_path = write_transcript_markdown(result, output_dir)
        result["transcript_markdown"] = str(transcript_path)
        results.append(result)
        print(
            f"  failures={len(result['rule_failures'])} avg_latency={result['average_latency_seconds']}"
        )

    json_path, md_path = write_review_bundle(results, output_dir)
    print(f"review json: {json_path}")
    print(f"review markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
