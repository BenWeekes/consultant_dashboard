from __future__ import annotations

from typing import Dict, List


def compute_biomarker_history_snapshot(
    storage,
    db,
    *,
    client_id: str,
    session_id: str,
    session_at: str,
    limit: int = 10,
):
    rows = db.execute(
        """
        SELECT biomarker_storage_key
        FROM sessions
        WHERE client_id = ?
          AND biomarker_storage_key IS NOT NULL
          AND id != ?
          AND COALESCE(ended_at, started_at, created_at) <= ?
        ORDER BY COALESCE(ended_at, started_at, created_at) DESC
        LIMIT ?
        """,
        (client_id, session_id, session_at, limit),
    ).fetchall()
    metrics: Dict[str, List[float]] = {}
    successful_payloads = 0
    for row in rows:
        payload = storage.get_json(row["biomarker_storage_key"], client_id)
        if not payload:
            continue
        successful_payloads += 1
        saw_group_metrics = False
        for group_name in ("voice", "vitals"):
            group = payload.get(group_name) or {}
            for key, metric in group.items():
                if not isinstance(metric, dict):
                    continue
                saw_group_metrics = True
                avg_value = metric.get("avg")
                if isinstance(avg_value, (int, float)):
                    metrics.setdefault(key, []).append(float(avg_value))
        if not saw_group_metrics:
            for key, metric in (payload.get("averages") or {}).items():
                if isinstance(metric, (int, float)):
                    metrics.setdefault(key, []).append(float(metric))
                    continue
                if not isinstance(metric, dict):
                    continue
                avg_value = metric.get("avg")
                if isinstance(avg_value, (int, float)):
                    metrics.setdefault(key, []).append(float(avg_value))
        safety = payload.get("safety") or {}
        level_stats = safety.get("level_stats") if isinstance(safety, dict) else None
        if isinstance(level_stats, dict):
            avg_value = level_stats.get("avg")
            if isinstance(avg_value, (int, float)):
                metrics.setdefault("safety_level", []).append(float(avg_value))
    return {
        "window_sessions": successful_payloads,
        "averages": {
            key: round(sum(values) / len(values), 4)
            for key, values in metrics.items()
            if values
        },
    }
