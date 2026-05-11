from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_kps(ai_kps: Dict[str, Any] | None) -> Dict[str, str]:
    ai_kps = ai_kps or {}
    return {
        "headline": _normalize_text(ai_kps.get("headline")),
        "body": _normalize_text(ai_kps.get("body")),
    }


def format_biomarker_event(event: Dict[str, Any]) -> str:
    parts: List[str] = []
    voice = event.get("voice") or {}
    camera = event.get("camera") or {}
    safety = event.get("safety") or {}

    if voice:
        voice_bits = [f"{key}={value}" for key, value in sorted(voice.items())]
        parts.append(f"Voice biomarkers: {', '.join(voice_bits)}")
    if camera:
        camera_bits = [f"{key}={value}" for key, value in sorted(camera.items())]
        parts.append(f"Camera vitals: {', '.join(camera_bits)}")
    if safety:
        safety_bits = [f"{key}={value}" for key, value in sorted(safety.items())]
        parts.append(f"Safety: {', '.join(safety_bits)}")
    return " | ".join(parts).strip()


def build_dashboard_context_block(session_context: Dict[str, Any]) -> str:
    lines = [
        "CONSULTANT DASHBOARD CONTEXT:",
        "- Use this context naturally when helpful, but do not mention a dashboard or internal system.",
        "- Let the client notes, consultant direction, and any previous key point summary guide your opening and early follow-up questions when relevant.",
        "- Prioritize the current conversation if it conflicts with older context.",
        "- Do not ask the user for permission to check biomarkers. If biomarker features are enabled for this session, they are already active. Use them naturally only when helpful.",
    ]

    if session_context.get("audio_biomarkers_enabled") is True:
        lines.append("- Voice biomarkers are enabled for this session.")
    elif session_context.get("audio_biomarkers_enabled") is False:
        lines.append("- Voice biomarkers are disabled for this session. Do not refer to voice biomarker data.")

    if session_context.get("video_biomarkers_enabled") is True:
        lines.append("- Camera biomarkers are enabled for this session.")
    elif session_context.get("video_biomarkers_enabled") is False:
        lines.append("- Camera biomarkers are disabled for this session. Do not refer to camera biomarker data.")

    profile = session_context.get("profile") or {}
    profile_bits = []
    if _normalize_text(profile.get("display_name")):
        profile_bits.append(f"Name: {_normalize_text(profile.get('display_name'))}")
    if profile.get("year_of_birth"):
        profile_bits.append(f"Year of birth: {profile.get('year_of_birth')}")
    if _normalize_text(profile.get("sex")):
        profile_bits.append(f"Sex: {_normalize_text(profile.get('sex')).lower()}")
    if profile_bits:
        lines.append(f"- Client profile: {' · '.join(profile_bits)}")

    notes = _normalize_text(session_context.get("notes"))
    if notes:
        lines.append(f"- Background notes: {notes}")

    direction = _normalize_text(session_context.get("direction"))
    if direction:
        lines.append(f"- Session direction: {direction}")

    ai_kps = _normalize_kps(session_context.get("ai_kps"))
    if ai_kps["headline"]:
        lines.append(f"- Client key point summary: {ai_kps['headline']}")
    if ai_kps["body"]:
        lines.append(f"- Client key point detail: {ai_kps['body']}")

    latest_summary = session_context.get("latest_summary") or {}
    latest_brief = _normalize_text(
        latest_summary.get("brief_overview")
        or latest_summary.get("overview")
        or latest_summary.get("summary")
    )
    latest_full = _normalize_text(latest_summary.get("full_summary"))
    if latest_brief:
        lines.append(f"- Previous session summary: {latest_brief}")
    if latest_full:
        lines.append(f"- Previous session full summary: {latest_full}")

    return "\n".join(lines).strip()


def assemble_effective_prompt(base_prompt: str, session_context: Dict[str, Any]) -> str:
    base_prompt = _normalize_text(base_prompt)
    addition = build_dashboard_context_block(session_context)
    if not addition:
        return base_prompt
    if not base_prompt:
        return addition
    return f"{base_prompt}\n\n{addition}"


def biomarker_messages_for_turn(turn_index: int, biomarker_events: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    for event in biomarker_events or []:
        if int(event.get("turn_index", -1)) != turn_index:
            continue
        content = format_biomarker_event(event)
        if content:
            messages.append({"role": "system", "content": f"[Biomarker Data] {content}"})
    return messages
