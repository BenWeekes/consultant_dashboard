from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
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

OPTIONAL_CASE_DEFAULTS = {
    "suite_tags": [],
    "blocking": False,
    "n_trials": 1,
}


def load_case(path: Path) -> Dict[str, Any]:
    case = json.loads(path.read_text())
    missing = sorted(REQUIRED_CASE_FIELDS - set(case))
    if missing:
        raise ValueError(f"{path}: missing required fields: {', '.join(missing)}")
    for key, default in OPTIONAL_CASE_DEFAULTS.items():
        case.setdefault(key, default if not isinstance(default, list) else list(default))
    if not case["suite_tags"]:
        case["suite_tags"] = [case["category"]]
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


def _post_chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    reasoning_effort: str = "",
) -> Dict[str, Any]:
    request_body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if reasoning_effort:
        request_body["reasoning_effort"] = reasoning_effort
    body = json.dumps(request_body).encode("utf-8")
    req = urllib.request.Request(
        base_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    elapsed = time.perf_counter() - started
    return {
        "content": (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        ),
        "usage": payload.get("usage") or {},
        "latency_seconds": elapsed,
    }


def _resolve_reasoning_effort(args: argparse.Namespace) -> str:
    return (
        args.reasoning_effort
        or os.environ.get("MINDFIX_EVAL_LLM_REASONING_EFFORT")
        or ""
    ).strip()


def execute_case(case: Dict[str, Any], base_prompt: str, args: argparse.Namespace) -> Dict[str, Any]:
    base_url = (args.llm_base_url or os.environ.get("MINDFIX_EVAL_LLM_BASE_URL") or "").strip()
    model = (args.llm_model or os.environ.get("MINDFIX_EVAL_LLM_MODEL") or "").strip()
    api_key = (args.llm_api_key or os.environ.get("MINDFIX_EVAL_LLM_API_KEY") or "").strip()
    if not (base_url and model and api_key):
        raise RuntimeError("Execution requested but eval LLM credentials are incomplete.")

    prompt = assemble_effective_prompt(base_prompt, case["session_context"])
    reasoning_effort = _resolve_reasoning_effort(args)
    transcript: List[Dict[str, str]] = [{"role": "system", "content": prompt}]
    assistant_turns: List[str] = []
    turn_latencies: List[float] = []
    usage_records: List[Dict[str, Any]] = []
    for turn_index, user_turn in enumerate(case.get("user_turns") or []):
        transcript.extend(biomarker_messages_for_turn(turn_index, case["session_context"].get("biomarker_events") or []))
        transcript.append({"role": "user", "content": str(user_turn)})
        response = _post_chat_completion(base_url, api_key, model, transcript, reasoning_effort=reasoning_effort)
        assistant = response["content"]
        assistant_turns.append(assistant)
        turn_latencies.append(float(response["latency_seconds"]))
        usage_records.append(response.get("usage") or {})
        transcript.append({"role": "assistant", "content": assistant})
    return {
        "assistant_turns": assistant_turns,
        "turn_latencies_seconds": turn_latencies,
        "usage_records": usage_records,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "runner_mode": args.runner_mode,
    }


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
    )["content"]
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


def _prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _aggregate_trial_metrics(trials: List[Dict[str, Any]]) -> Dict[str, Any]:
    latencies = [
        latency
        for trial in trials
        for latency in (trial.get("operational_metrics", {}) or {}).get("turn_latencies_seconds", [])
    ]
    return {
        "trial_count": len(trials),
        "average_turn_latency_seconds": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "max_turn_latency_seconds": round(max(latencies), 3) if latencies else None,
    }


def _aggregate_verdict(trials: List[Dict[str, Any]]) -> str:
    verdicts = [trial.get("verdict") for trial in trials]
    if "hard_fail" in verdicts:
        return "hard_fail"
    if "soft_fail" in verdicts:
        return "soft_fail"
    if "pass" in verdicts:
        return "pass"
    return "not_executed"


def evaluate_case(case: Dict[str, Any], base_prompt: str, args: argparse.Namespace) -> Dict[str, Any]:
    prompt = assemble_effective_prompt(base_prompt, case["session_context"])
    requested_trials = int(case.get("n_trials") or 1)
    if args.trials is not None:
        requested_trials = max(1, int(args.trials))

    trials: List[Dict[str, Any]] = []
    for trial_index in range(requested_trials):
        assistant_turns = case.get("assistant_turns") or []
        executed = False
        execution_result: Dict[str, Any] = {}
        if args.execute:
            execution_result = execute_case(case, base_prompt, args)
            assistant_turns = execution_result["assistant_turns"]
            executed = True

        rule_failures = run_policy_checks(case, assistant_turns)
        verdict = summarize_verdict(rule_failures, assistant_turns)
        judge_result = run_optional_judge(case, prompt, assistant_turns, rule_failures, args)
        if judge_result and judge_result.get("verdict") == "hard_fail":
            verdict = "hard_fail"

        trial_metrics = {
            "model": execution_result.get("model") or args.llm_model or os.environ.get("MINDFIX_EVAL_LLM_MODEL") or "",
            "reasoning_effort": execution_result.get("reasoning_effort") or _resolve_reasoning_effort(args),
            "runner_mode": execution_result.get("runner_mode") or args.runner_mode,
            "assistant_turn_count": len(assistant_turns),
            "user_turn_count": len(case.get("user_turns") or []),
            "total_turn_count": len(assistant_turns) + len(case.get("user_turns") or []),
            "turn_latencies_seconds": execution_result.get("turn_latencies_seconds") or [],
            "average_turn_latency_seconds": (
                round(sum(execution_result.get("turn_latencies_seconds") or []) / len(execution_result.get("turn_latencies_seconds") or []), 3)
                if execution_result.get("turn_latencies_seconds")
                else None
            ),
            "max_turn_latency_seconds": (
                round(max(execution_result.get("turn_latencies_seconds") or []), 3)
                if execution_result.get("turn_latencies_seconds")
                else None
            ),
            "usage_records": execution_result.get("usage_records") or [],
        }

        trials.append({
            "trial_index": trial_index,
            "executed": executed,
            "verdict": verdict,
            "assistant_turns": assistant_turns,
            "rule_failures": rule_failures,
            "judge_result": judge_result,
            "transcript_grades": judge_result.get("scores") if judge_result else {},
            "outcome_grades": {"verdict": verdict},
            "operational_metrics": trial_metrics,
        })

    return {
        "case_id": case["id"],
        "category": case["category"],
        "risk_level": case["risk_level"],
        "suite_tags": case.get("suite_tags") or [],
        "blocking": bool(case.get("blocking")),
        "requested_trials": requested_trials,
        "case_path": case["_path"],
        "executed": any(trial["executed"] for trial in trials),
        "verdict": _aggregate_verdict(trials),
        "effective_prompt": prompt,
        "effective_prompt_hash": _prompt_hash(prompt),
        "base_prompt_hash": _prompt_hash(base_prompt),
        "user_turns": case.get("user_turns") or [],
        "assistant_turns": trials[0]["assistant_turns"] if trials else [],
        "rule_failures": trials[0]["rule_failures"] if trials else [],
        "judge_result": trials[0]["judge_result"] if trials else None,
        "trials": trials,
        "aggregate_metrics": _aggregate_trial_metrics(trials),
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
    parser.add_argument("--reasoning-effort", help="Reasoning effort override for supported models.")
    parser.add_argument("--trials", type=int, help="Override case trial count.")
    parser.add_argument("--runner-mode", choices=["direct_model", "platform"], default="direct_model", help="Label the agent harness used for the run.")
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
