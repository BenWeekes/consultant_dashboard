from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List


def render_html_report(results: List[Dict[str, Any]], output_path: str) -> None:
    rows = []
    for result in results:
        aggregate_metrics = result.get("aggregate_metrics") or {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(result.get('case_id', ''))}</td>"
            f"<td>{html.escape(result.get('category', ''))}</td>"
            f"<td>{html.escape(result.get('verdict', ''))}</td>"
            f"<td>{'yes' if result.get('blocking') else 'no'}</td>"
            f"<td>{html.escape(', '.join(result.get('suite_tags') or []))}</td>"
            f"<td>{result.get('requested_trials', 1)}</td>"
            f"<td>{len(result.get('rule_failures', []))}</td>"
            f"<td>{'yes' if result.get('executed') else 'no'}</td>"
            f"<td>{html.escape(str(aggregate_metrics.get('average_turn_latency_seconds', '')))}</td>"
            "</tr>"
        )
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>MindFix Eval Report</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 24px; color: #1f2937; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d1d5db; padding: 8px 10px; text-align: left; }}
    th {{ background: #f3f4f6; }}
    code {{ background: #f3f4f6; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>MindFix Offline Eval Report</h1>
  <p>Total cases: <strong>{len(results)}</strong></p>
  <table>
    <thead>
      <tr>
        <th>Case</th>
        <th>Category</th>
        <th>Verdict</th>
        <th>Blocking</th>
        <th>Suites</th>
        <th>Trials</th>
        <th>Rule failures</th>
        <th>Executed</th>
        <th>Avg latency (s)</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>"""
    Path(output_path).write_text(body)


def write_json_report(results: List[Dict[str, Any]], output_path: str) -> None:
    Path(output_path).write_text(json.dumps(results, indent=2))
