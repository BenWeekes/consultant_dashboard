from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List

from evals.prompting import assemble_effective_prompt, biomarker_messages_for_turn
from evals.reports.render_report import render_html_report, write_json_report
from evals.rules.policy_checks import run_policy_checks


REQUIRED_CASE_FIELDS = {
    "id",
    "category",
    "risk_level",
    "description",
    "session_context",
    "user_turns",
    "expected_behaviors",
    "forbidden_behaviors",
}


def load_case(path: Path) -> Dict[str, Any]:
    case = json.loads(path.read_text())
    missing = sorted(REQUIRED_CASE_FIELDS - set(case))
    if missing:
        raise ValueError(f"{path}: missing required fields: {', '.join(missing)}")
    case["_path"] = str(path)
    return case


def load_cases(paths: Iterable[str]) -> List[Dict[str, Any]]:
    loaded: List[Dict[str, Any]] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for candidate in sorted(path.rglob("*.json")):
                loaded.append(load_case(candidate))
        else:
            loaded.append(load_case(path))
    return loaded


def _load_base_prompt(args: argparse.Namespace) -> str:
    if args.base_prompt_file:
        return Path(args.base_prompt_file).read_text().strip()
    return (os.environ.get("MINDFIX_EVAL_BASE_PROMPT") or "").strip()


def _post_chat_completion(base_url: str, api_key: str, model: str, messages: List[Dict[str, str]]) -> str:
    body = json.dumps({
        "model": model,
        "messages": messages,
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return (
        payload.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )


def execute_case(case: Dict[str, Any], base_prompt: str, args: argparse.Namespace) -> List[str]:
    base_url = (args.llm_base_url or os.environ.get("MINDFIX_EVAL_LLM_BASE_URL") or "").strip()
    model = (args.llm_model or os.environ.get("MINDFIX_EVAL_LLM_MODEL") or "").strip()
    api_key = (args.llm_api_key or os.environ.get("MINDFIX_EVAL_LLM_API_KEY") or "").strip()
    if not (base_url and model and api_key):
        raise RuntimeError("Execution requested but eval LLM credentials are incomplete.")

    prompt = assemble_effective_prompt(base_prompt, case["session_context"])
    transcript: List[Dict[str, str]] = [{"role": "system", "content": prompt}]
    assistant_turns: List[str] = []
    for turn_index, user_turn in enumerate(case.get("user_turns") or []):
        transcript.extend(biomarker_messages_for_turn(turn_index, case["session_context"].get("biomarker_events") or []))
        transcript.append({"role": "user", "content": str(user_turn)})
        assistant = _post_chat_completion(base_url, api_key, model, transcript)
        assistant_turns.append(assistant)
        transcript.append({"role": "assistant", "content": assistant})
    return assistant_turns


def run_optional_judge(case: Dict[str, Any], prompt: str, assistant_turns: List[str], rule_failures: List[Dict[str, Any]], args: argparse.Namespace) -> Dict[str, Any] | None:
    if not args.judge:
        return None
    base_url = (args.judge_base_url or args.llm_base_url or os.environ.get("MINDFIX_EVAL_LLM_BASE_URL") or "").strip()
    model = (args.judge_model or args.llm_model or os.environ.get("MINDFIX_EVAL_LLM_MODEL") or "").strip()
    api_key = (args.judge_api_key or args.llm_api_key or os.environ.get("MINDFIX_EVAL_LLM_API_KEY") or "").strip()
    if not (base_url and model and api_key):
        raise RuntimeError("Judge requested but judge LLM credentials are incomplete.")

    judge_prompt = Path("evals/judges/judge_prompt.md").read_text().strip()
    payload = {
        "case": case,
        "effective_prompt": prompt,
        "assistant_turns": assistant_turns,
        "rule_failures": rule_failures,
        "response_schema": json.loads(Path("evals/judges/judge_schema.json").read_text()),
    }
    content = _post_chat_completion(
        base_url,
        api_key,
        model,
        [
            {"role": "system", "content": judge_prompt},
            {"role": "user", "content": json.dumps(payload, indent=2)},
        ],
    )
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {
            "verdict": "soft_fail",
            "hard_fail": False,
            "scores": {},
            "reasons": [],
            "concerns": ["Judge returned invalid JSON."],
            "quoted_evidence": [content[:240]],
        }


def summarize_verdict(rule_failures: List[Dict[str, Any]], assistant_turns: List[str]) -> str:
    if any(item.get("severity") == "hard_fail" for item in rule_failures):
        return "hard_fail"
    if rule_failures:
        return "soft_fail"
    if not assistant_turns:
        return "not_executed"
    return "pass"


def evaluate_case(case: Dict[str, Any], base_prompt: str, args: argparse.Namespace) -> Dict[str, Any]:
    prompt = assemble_effective_prompt(base_prompt, case["session_context"])
    assistant_turns = case.get("assistant_turns") or []
    executed = False
    if args.execute:
        assistant_turns = execute_case(case, base_prompt, args)
        executed = True

    rule_failures = run_policy_checks(case, assistant_turns)
    verdict = summarize_verdict(rule_failures, assistant_turns)
    judge_result = run_optional_judge(case, prompt, assistant_turns, rule_failures, args)
    if judge_result and judge_result.get("verdict") == "hard_fail":
        verdict = "hard_fail"

    return {
        "case_id": case["id"],
        "category": case["category"],
        "risk_level": case["risk_level"],
        "case_path": case["_path"],
        "executed": executed,
        "verdict": verdict,
        "effective_prompt": prompt,
        "user_turns": case.get("user_turns") or [],
        "assistant_turns": assistant_turns,
        "rule_failures": rule_failures,
        "judge_result": judge_result,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MindFix offline eval cases.")
    parser.add_argument("--cases", nargs="+", required=True, help="Case files or directories.")
    parser.add_argument("--base-prompt-file", help="Path to a text file containing the base prompt.")
    parser.add_argument("--execute", action="store_true", help="Execute cases against an OpenAI-compatible endpoint.")
    parser.add_argument("--llm-base-url", help="OpenAI-compatible completions URL.")
    parser.add_argument("--llm-model", help="Model name for offline execution.")
    parser.add_argument("--llm-api-key", help="API key for offline execution.")
    parser.add_argument("--judge", action="store_true", help="Run optional judge scoring.")
    parser.add_argument("--judge-base-url", help="Judge endpoint override.")
    parser.add_argument("--judge-model", help="Judge model override.")
    parser.add_argument("--judge-api-key", help="Judge API key override.")
    parser.add_argument("--output", help="Write JSON report to this file.")
    parser.add_argument("--html-output", help="Write HTML report to this file.")
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    base_prompt = _load_base_prompt(args)
    cases = load_cases(args.cases)
    results = [evaluate_case(case, base_prompt, args) for case in cases]

    if args.output:
        write_json_report(results, args.output)
    if args.html_output:
        render_html_report(results, args.html_output)

    summary = {
        "total_cases": len(results),
        "pass": sum(1 for item in results if item["verdict"] == "pass"),
        "soft_fail": sum(1 for item in results if item["verdict"] == "soft_fail"),
        "hard_fail": sum(1 for item in results if item["verdict"] == "hard_fail"),
        "not_executed": sum(1 for item in results if item["verdict"] == "not_executed"),
    }
    json.dump({"summary": summary, "results": results}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
