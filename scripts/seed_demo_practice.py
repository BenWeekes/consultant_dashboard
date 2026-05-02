#!/usr/bin/env python3
import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from dotenv import load_dotenv

from consultant_dashboard.app import create_app
from consultant_dashboard.core.db import (
    create_client,
    create_client_access_link,
    create_client_message,
    create_scheduled_meeting,
    get_client_by_email,
    get_db,
    upsert_session,
)
from consultant_dashboard.core.meetings import build_join_window, get_pair_channel, iso_utc
from consultant_dashboard.core.messaging import default_expiry, hash_access_token, new_access_token
from consultant_dashboard.core.storage import EncryptedStorage
from consultant_dashboard.core.vendors import storage_root_for_client
from consultant_dashboard.core.web import _refresh_client_derived_state


@dataclass(frozen=True)
class DemoClient:
    slug: str
    first_name: str
    last_name: str
    year_of_birth: int
    sex: str
    email: str
    phone_number: str
    notification_email: str
    notes: str
    direction: str
    ai_kps: str
    human_kps: str
    session_focuses: List[str]
    support_style: str
    stress_base: float
    distress_base: float
    burnout_base: float
    fatigue_base: float
    anxiety_base: float
    depression_base: float
    heart_rate_base: int
    hrv_base: int
    stress_index_base: float
    breathing_rate_base: int
    systolic_base: int
    diastolic_base: int
    cardiac_workload_base: int
    safety_level_base: float


DEMO_CLIENTS: List[DemoClient] = [
    DemoClient(
        slug="emma-carter",
        first_name="Emma",
        last_name="Carter",
        year_of_birth=1989,
        sex="female",
        email="emma.carter@harborwell.co.uk",
        phone_number="+447700910101",
        notification_email="benweekes73@gmail.com",
        notes="Senior project manager. High functioning but carries persistent work tension into evenings. Tends to minimise stress until sleep starts to suffer.",
        direction="Keep sessions practical: boundaries after work, shorter evening wind-down, and noticing early signs of overload.",
        ai_kps="Emma uses AI sessions well for decompression after difficult workdays. She benefits from short, concrete reflection prompts, sleep-protection routines, and reminders to name pressure before it builds into shutdown or irritability.",
        human_kps="Human sessions with Emma focus on perfectionism, pressure from leadership responsibilities, and how quickly she moves into over-functioning. Useful themes are boundary setting, recovery planning, and keeping empathy for herself when work intensity spikes.",
        session_focuses=["work stress", "sleep routine", "delegation", "self-pressure", "recovery planning"],
        support_style="brief, structured, and practical",
        stress_base=0.48,
        distress_base=0.34,
        burnout_base=0.39,
        fatigue_base=0.29,
        anxiety_base=0.36,
        depression_base=0.14,
        heart_rate_base=74,
        hrv_base=34,
        stress_index_base=1.9,
        breathing_rate_base=16,
        systolic_base=118,
        diastolic_base=77,
        cardiac_workload_base=42,
        safety_level_base=0.0,
    ),
    DemoClient(
        slug="daniel-ng",
        first_name="Daniel",
        last_name="Ng",
        year_of_birth=1978,
        sex="male",
        email="daniel.ng@northfieldmail.com",
        phone_number="+447700910102",
        notification_email="benweekes73@gmail.com",
        notes="Recently promoted. Worries about letting people down and often revisits conversations after the fact. Mood generally stable, anxiety more prominent than low mood.",
        direction="Support confidence in leadership decisions and reduce rumination by anchoring sessions in what was actually said versus feared.",
        ai_kps="Daniel returns to themes of leadership anxiety, social over-analysis, and needing permission to stop replaying conversations. He responds well to reframing, evidence checks, and end-of-day closure rituals.",
        human_kps="Human work with Daniel centres on confidence, self-trust, and the gap between his performance and his internal sense of adequacy. Sessions should keep linking anxiety spikes to over-responsibility rather than actual failure.",
        session_focuses=["promotion anxiety", "rumination", "confidence", "team conversations", "decision fatigue"],
        support_style="warm but gently challenging",
        stress_base=0.42,
        distress_base=0.28,
        burnout_base=0.31,
        fatigue_base=0.21,
        anxiety_base=0.41,
        depression_base=0.11,
        heart_rate_base=71,
        hrv_base=37,
        stress_index_base=1.7,
        breathing_rate_base=15,
        systolic_base=116,
        diastolic_base=75,
        cardiac_workload_base=39,
        safety_level_base=0.0,
    ),
    DemoClient(
        slug="sophia-rahman",
        first_name="Sophia",
        last_name="Rahman",
        year_of_birth=1994,
        sex="female",
        email="sophia.rahman@oaklaneliving.co.uk",
        phone_number="+447700910103",
        notification_email="benweekes73@gmail.com",
        notes="Balancing freelance work and parenting. Tired, often emotionally flat rather than overtly distressed. Feels guilty whenever she prioritises rest.",
        direction="Make space for self-compassion, fatigue tracking, and realistic pacing rather than ideal routines.",
        ai_kps="Sophia tends to use AI sessions when exhaustion is high and decision-making feels heavy. Helpful interventions include permission-giving around rest, simplifying the next step, and separating fatigue from failure.",
        human_kps="Human sessions explore chronic fatigue, guilt around parenting and productivity, and the pressure to appear fine while depleted. Therapist focus should stay on pacing, permission, and reducing self-criticism.",
        session_focuses=["fatigue", "parenting load", "guilt", "rest", "pacing"],
        support_style="calm, validating, and non-judgmental",
        stress_base=0.39,
        distress_base=0.33,
        burnout_base=0.44,
        fatigue_base=0.52,
        anxiety_base=0.29,
        depression_base=0.19,
        heart_rate_base=69,
        hrv_base=31,
        stress_index_base=1.6,
        breathing_rate_base=15,
        systolic_base=114,
        diastolic_base=73,
        cardiac_workload_base=36,
        safety_level_base=0.0,
    ),
    DemoClient(
        slug="michael-reed",
        first_name="Michael",
        last_name="Reed",
        year_of_birth=1968,
        sex="male",
        email="michael.reed@copperbridge.net",
        phone_number="+447700910104",
        notification_email="benweekes73@gmail.com",
        notes="Recently separated. Presents as practical but is carrying grief, loneliness, and a lot of anger at himself for not seeing the relationship strain earlier.",
        direction="Keep grief and identity themes in view while building healthier routines for evenings and weekends.",
        ai_kps="Michael uses AI sessions to steady himself in the evenings when loneliness is strongest. Useful support includes naming grief directly, grounding anger without feeding it, and planning social contact before weekends.",
        human_kps="Human sessions with Michael focus on grief after separation, identity loss, and self-blame. He benefits from direct language, emotional naming, and concrete behavioural anchors that reduce isolation.",
        session_focuses=["grief", "loneliness", "self-blame", "weekend planning", "identity after separation"],
        support_style="direct, grounded, and emotionally clear",
        stress_base=0.46,
        distress_base=0.41,
        burnout_base=0.24,
        fatigue_base=0.27,
        anxiety_base=0.24,
        depression_base=0.26,
        heart_rate_base=73,
        hrv_base=32,
        stress_index_base=1.8,
        breathing_rate_base=16,
        systolic_base=120,
        diastolic_base=78,
        cardiac_workload_base=43,
        safety_level_base=0.1,
    ),
    DemoClient(
        slug="olivia-bennett",
        first_name="Olivia",
        last_name="Bennett",
        year_of_birth=1985,
        sex="female",
        email="olivia.bennett@mapleridge.co.uk",
        phone_number="+447700910105",
        notification_email="benweekes73@gmail.com",
        notes="Persistent low self-esteem beneath a competent exterior. Finds praise hard to absorb and swings between over-preparing and avoidance.",
        direction="Reinforce evidence of competence, reduce all-or-nothing thinking, and keep helping her notice when avoidance is shame-led.",
        ai_kps="Olivia’s AI sessions often return to self-doubt, over-preparing, and the fear of being exposed as inadequate. She benefits from evidence-based reflection, compassionate challenge, and naming avoidance early.",
        human_kps="Human sessions focus on shame, self-worth, and the gap between external capability and internal narrative. Work should continue linking low self-esteem to avoidance and overcompensation rather than actual performance problems.",
        session_focuses=["low self-esteem", "shame", "avoidance", "performance anxiety", "evidence checking"],
        support_style="supportive, reflective, and confidence-building",
        stress_base=0.37,
        distress_base=0.36,
        burnout_base=0.28,
        fatigue_base=0.22,
        anxiety_base=0.33,
        depression_base=0.21,
        heart_rate_base=70,
        hrv_base=35,
        stress_index_base=1.5,
        breathing_rate_base=14,
        systolic_base=115,
        diastolic_base=74,
        cardiac_workload_base=35,
        safety_level_base=0.0,
    ),
    DemoClient(
        slug="james-khan",
        first_name="James",
        last_name="Khan",
        year_of_birth=1991,
        sex="male",
        email="james.khan@westbrookmail.com",
        phone_number="+447700910106",
        notification_email="benweekes73@gmail.com",
        notes="Burnout risk from long hours and minimal recovery. Wants to be more present with family but keeps slipping back into work on weekends.",
        direction="Make progress visible, support rest without guilt, and keep reconnecting his values to his actual weekly schedule.",
        ai_kps="James uses AI sessions when he notices himself edging back toward burnout. He benefits from practical reset plans, values reminders, and prompts that move him from vague guilt into a concrete next step.",
        human_kps="Human work with James is about chronic overwork, difficulty switching off, and conflict between provider identity and health. Sessions should support behaviour change while protecting his sense of competence.",
        session_focuses=["burnout", "work-life balance", "family time", "weekend recovery", "values"],
        support_style="clear, practical, and accountability-oriented",
        stress_base=0.51,
        distress_base=0.31,
        burnout_base=0.49,
        fatigue_base=0.38,
        anxiety_base=0.27,
        depression_base=0.12,
        heart_rate_base=76,
        hrv_base=30,
        stress_index_base=2.1,
        breathing_rate_base=16,
        systolic_base=121,
        diastolic_base=79,
        cardiac_workload_base=46,
        safety_level_base=0.0,
    ),
    DemoClient(
        slug="lily-owens",
        first_name="Lily",
        last_name="Owens",
        year_of_birth=2000,
        sex="female",
        email="lily.owens@birchandco.uk",
        phone_number="+447700910107",
        notification_email="benweekes73@gmail.com",
        notes="University student managing deadlines, social anxiety, and irregular sleep. Uses AI sessions well for short check-ins before assignments.",
        direction="Keep sessions short, practical, and reassuring without colluding with avoidance.",
        ai_kps="Lily uses AI sessions as a steadying space before deadlines and socially stressful events. Helpful themes are reducing catastrophic thinking, improving sleep consistency, and narrowing the focus to one manageable action.",
        human_kps="Human sessions centre on social anxiety, academic pressure, and self-criticism. Therapy is helping Lily move from avoidance toward gradual exposure and more stable routines.",
        session_focuses=["social anxiety", "deadlines", "sleep", "catastrophic thinking", "exposure"],
        support_style="gentle, encouraging, and focused",
        stress_base=0.43,
        distress_base=0.29,
        burnout_base=0.26,
        fatigue_base=0.33,
        anxiety_base=0.39,
        depression_base=0.13,
        heart_rate_base=72,
        hrv_base=36,
        stress_index_base=1.8,
        breathing_rate_base=15,
        systolic_base=113,
        diastolic_base=72,
        cardiac_workload_base=34,
        safety_level_base=0.0,
    ),
    DemoClient(
        slug="noah-foster",
        first_name="Noah",
        last_name="Foster",
        year_of_birth=1982,
        sex="male",
        email="noah.foster@riverviewmail.net",
        phone_number="+447700910108",
        notification_email="benweekes73@gmail.com",
        notes="Chronic sleep disruption with a pattern of late-night overthinking. Mood dips after poor sleep but rebounds when routine improves.",
        direction="Prioritise sleep stabilisation, reduce late-night cognitive spirals, and keep daytime structure simple.",
        ai_kps="Noah turns to AI support when poor sleep makes everything feel heavier. The most useful interventions are grounding, sleep protection, and gentle interruption of late-night over-analysis.",
        human_kps="Human sessions focus on the interaction between sleep, mood, and overthinking. Progress is strongest when plans stay realistic and anchored to evening routine rather than willpower alone.",
        session_focuses=["sleep", "night-time overthinking", "daytime structure", "mood dips", "routines"],
        support_style="steady, grounding, and realistic",
        stress_base=0.34,
        distress_base=0.27,
        burnout_base=0.22,
        fatigue_base=0.47,
        anxiety_base=0.31,
        depression_base=0.17,
        heart_rate_base=68,
        hrv_base=33,
        stress_index_base=1.4,
        breathing_rate_base=14,
        systolic_base=112,
        diastolic_base=71,
        cardiac_workload_base=33,
        safety_level_base=0.0,
    ),
    DemoClient(
        slug="amelia-stone",
        first_name="Amelia",
        last_name="Stone",
        year_of_birth=1975,
        sex="female",
        email="amelia.stone@willowfield.co.uk",
        phone_number="+447700910109",
        notification_email="benweekes73@gmail.com",
        notes="Caring for an elderly parent while working part-time. Frequently exhausted and conflicted about asking siblings for more help.",
        direction="Support boundaries, reduce guilt, and keep reinforcing that asking for practical help is not a moral failure.",
        ai_kps="Amelia uses AI sessions to regulate after difficult caring days. She benefits from compassionate language, permission to ask for support, and prompts that reduce guilt-based overfunctioning.",
        human_kps="Human sessions keep returning to caring burden, guilt, and family dynamics. Therapeutic focus should stay on boundaries, grief, and sharing responsibility more evenly.",
        session_focuses=["caregiver stress", "guilt", "family roles", "asking for help", "fatigue"],
        support_style="compassionate, validating, and steady",
        stress_base=0.44,
        distress_base=0.35,
        burnout_base=0.37,
        fatigue_base=0.46,
        anxiety_base=0.22,
        depression_base=0.18,
        heart_rate_base=71,
        hrv_base=30,
        stress_index_base=1.7,
        breathing_rate_base=15,
        systolic_base=117,
        diastolic_base=76,
        cardiac_workload_base=38,
        safety_level_base=0.0,
    ),
    DemoClient(
        slug="ethan-price",
        first_name="Ethan",
        last_name="Price",
        year_of_birth=1996,
        sex="male",
        email="ethan.price@clearpathmail.com",
        phone_number="+447700910110",
        notification_email="benweekes73@gmail.com",
        notes="Recovering from a difficult year of low mood and isolation. More stable recently, but still vulnerable when routines break and he withdraws socially.",
        direction="Support momentum, keep social reconnection active, and track early signs of withdrawal before mood dips deepen.",
        ai_kps="Ethan uses AI sessions well for maintaining momentum and catching isolation early. Helpful work includes planning contact, naming low-mood cues, and separating temporary dips from hopeless conclusions.",
        human_kps="Human sessions focus on maintaining recovery, rebuilding social confidence, and monitoring early signs of withdrawal. Therapist guidance should remain active around routine and connection.",
        session_focuses=["recovery maintenance", "social reconnection", "withdrawal", "low mood", "routine"],
        support_style="hopeful, structured, and grounded",
        stress_base=0.29,
        distress_base=0.32,
        burnout_base=0.18,
        fatigue_base=0.26,
        anxiety_base=0.23,
        depression_base=0.24,
        heart_rate_base=67,
        hrv_base=39,
        stress_index_base=1.3,
        breathing_rate_base=14,
        systolic_base=111,
        diastolic_base=70,
        cardiac_workload_base=31,
        safety_level_base=0.1,
    ),
    DemoClient(
        slug="grace-mitchell",
        first_name="Grace",
        last_name="Mitchell",
        year_of_birth=1987,
        sex="female",
        email="grace.mitchell@ashgrove.co.uk",
        phone_number="+447700910111",
        notification_email="benweekes73@gmail.com",
        notes="History of crisis language during overwhelm but generally responsive to grounding and clear check-ins. Important not to over-react while still taking risk signals seriously.",
        direction="Maintain calm but direct safety check-ins, reinforce support plan, and distinguish fleeting crisis language from active intent.",
        ai_kps="Grace can escalate quickly in language when overwhelmed, but usually responds to grounding, validation, and direct safety clarification. Future sessions should stay alert to mismatch between words and intent while still checking for real risk.",
        human_kps="Human sessions focus on emotional flooding, shame after crises, and building trust in de-escalation strategies. Therapist attention should stay on rapid regulation, explicit safety language, and strengthening her support plan.",
        session_focuses=["overwhelm", "safety language", "de-escalation", "support plan", "shame after crisis"],
        support_style="calm, direct, and safety-aware",
        stress_base=0.58,
        distress_base=0.49,
        burnout_base=0.22,
        fatigue_base=0.24,
        anxiety_base=0.44,
        depression_base=0.19,
        heart_rate_base=79,
        hrv_base=27,
        stress_index_base=2.4,
        breathing_rate_base=17,
        systolic_base=124,
        diastolic_base=80,
        cardiac_workload_base=49,
        safety_level_base=0.8,
    ),
]


def session_biomarkers(client: DemoClient, session_index: int, is_human: bool, focus: str) -> Dict:
    step = session_index * 0.02
    voice = {
        "stress": {"avg": round(client.stress_base + step, 3), "max": round(client.stress_base + step + 0.16, 3), "min": round(max(client.stress_base - 0.12, 0.05), 3), "count": 18},
        "distress": {"avg": round(client.distress_base + (step / 2), 3), "max": round(client.distress_base + (step / 2) + 0.14, 3), "min": round(max(client.distress_base - 0.11, 0.04), 3), "count": 18},
        "burnout": {"avg": round(client.burnout_base + (0.01 if is_human else 0.0), 3), "max": round(client.burnout_base + 0.13, 3), "min": round(max(client.burnout_base - 0.09, 0.03), 3), "count": 18},
        "fatigue": {"avg": round(client.fatigue_base + (0.02 if not is_human else 0.0), 3), "max": round(client.fatigue_base + 0.12, 3), "min": round(max(client.fatigue_base - 0.08, 0.03), 3), "count": 18},
        "depression_probability": {"avg": round(client.depression_base + (0.01 if "mood" in focus or "grief" in focus else 0.0), 3), "max": round(client.depression_base + 0.08, 3), "min": round(max(client.depression_base - 0.05, 0.01), 3), "count": 18},
        "anxiety_probability": {"avg": round(client.anxiety_base + (0.02 if "anxiety" in focus or "stress" in focus else 0.0), 3), "max": round(client.anxiety_base + 0.1, 3), "min": round(max(client.anxiety_base - 0.05, 0.01), 3), "count": 18},
        "neutral": {"avg": 0.54 if is_human else 0.58, "max": 0.69, "min": 0.32, "count": 18},
        "sad": {"avg": 0.16 if "grief" in focus or "mood" in focus else 0.1, "max": 0.29, "min": 0.03, "count": 18},
        "happy": {"avg": 0.08, "max": 0.16, "min": 0.01, "count": 18},
        "fearful": {"avg": 0.09 if "anxiety" in focus or "safety" in focus else 0.05, "max": 0.17, "min": 0.01, "count": 18},
    }
    systolic = client.systolic_base + (2 if session_index % 2 else 0)
    diastolic = client.diastolic_base + (1 if session_index % 2 else 0)
    vitals = {
        "heart_rate_bpm": {"avg": float(client.heart_rate_base + session_index), "max": float(client.heart_rate_base + session_index + 8), "min": float(client.heart_rate_base + session_index - 6), "count": 12},
        "hrv_sdnn_ms": {"avg": float(max(client.hrv_base - session_index, 18)), "max": float(max(client.hrv_base - session_index + 7, 22)), "min": float(max(client.hrv_base - session_index - 6, 14)), "count": 12},
        "stress_index": {"avg": round(client.stress_index_base + (0.15 * session_index), 2), "max": round(client.stress_index_base + (0.15 * session_index) + 0.5, 2), "min": round(max(client.stress_index_base - 0.4, 0.8), 2), "count": 12},
        "breathing_rate_bpm": {"avg": float(client.breathing_rate_base + (1 if session_index % 3 == 0 else 0)), "max": float(client.breathing_rate_base + 3), "min": float(max(client.breathing_rate_base - 2, 10)), "count": 12},
        "systolic_bp": {"avg": float(systolic), "max": float(systolic + 4), "min": float(systolic - 3), "count": 12},
        "diastolic_bp": {"avg": float(diastolic), "max": float(diastolic + 3), "min": float(diastolic - 3), "count": 12},
        "cardiac_workload": {"avg": float(client.cardiac_workload_base + session_index), "max": float(client.cardiac_workload_base + session_index + 6), "min": float(max(client.cardiac_workload_base - 5, 20)), "count": 12},
    }
    max_safety = 3.0 if client.slug == "grace-mitchell" and session_index == 4 else (1.0 if client.safety_level_base > 0 else 0.0)
    avg_safety = 1.4 if max_safety >= 3 else client.safety_level_base
    safety = {
        "level_stats": {"avg": avg_safety, "max": max_safety, "min": 0.0, "count": 12},
        "highest_level": int(max_safety),
        "highest_alert": "crisis" if max_safety >= 3 else ("monitor" if max_safety >= 1 else "ok"),
        "highest_concerns": ["self-harm language"] if max_safety >= 3 else ([focus] if focus else []),
        "highest_recommended_actions": (
            ["Review support plan and confirm immediate safety."]
            if max_safety >= 3
            else (["Continue monitoring and revisit next session."] if max_safety >= 1 else [])
        ),
        "active_policy": "mindfix_safety_v1",
    }
    averages = {
        "stress": voice["stress"]["avg"],
        "distress": voice["distress"]["avg"],
        "burnout": voice["burnout"]["avg"],
        "fatigue": voice["fatigue"]["avg"],
        "depression_probability": voice["depression_probability"]["avg"],
        "anxiety_probability": voice["anxiety_probability"]["avg"],
        "heart_rate_bpm": vitals["heart_rate_bpm"]["avg"],
        "hrv_sdnn_ms": vitals["hrv_sdnn_ms"]["avg"],
        "stress_index": vitals["stress_index"]["avg"],
        "breathing_rate_bpm": vitals["breathing_rate_bpm"]["avg"],
        "cardiac_workload": vitals["cardiac_workload"]["avg"],
        "safety_level": safety["level_stats"]["avg"],
    }
    return {
        "averages": averages,
        "voice": voice,
        "vitals": vitals,
        "safety": safety,
    }


def session_summary(client: DemoClient, session_kind: str, focus: str, session_number: int, biomarkers: Dict) -> Dict:
    is_human = session_kind == "consultant_live_session"
    prefix = "Human session" if is_human else "AI session"
    headline = f"{prefix} focused on {focus}"
    biomarker_line = (
        f"Stress remained {'moderately elevated' if biomarkers['voice']['stress']['avg'] >= 0.45 else 'steady'}, "
        f"HR averaged {round(biomarkers['vitals']['heart_rate_bpm']['avg'])} bpm, "
        f"and leading emotion stayed mostly neutral."
    )
    risk_text = (
        "Highest crisis level reached was 3 during one brief period of overwhelm; the session ended with clear de-escalation and renewed support planning."
        if biomarkers["safety"]["highest_level"] >= 3
        else (
            "Highest crisis level reached was 1, with no active self-harm intent reported."
            if biomarkers["safety"]["highest_level"] >= 1
            else "No acute crisis concerns were identified in this session."
        )
    )
    follow_up = (
        f"Continue using a {client.support_style} style, revisit {focus}, and keep the next step practical."
    )
    body = (
        f"{client.first_name} used this {'human' if is_human else 'AI'} session to work on {focus}. "
        f"The conversation linked current strain back to the longer pattern already seen across care: {client.ai_kps if not is_human else client.human_kps}"
    )
    return {
        "key_point_summary": {"headline": headline, "body": body},
        "brief_overview": headline,
        "overview": headline,
        "full_summary": body,
        "biomarker_summary": biomarker_line,
        "risk_overview": risk_text,
        "follow_up": follow_up,
        "source": "demo-seed",
    }


def transcript_text(client: DemoClient, focus: str, session_number: int) -> Dict:
    lines = [
        {"speaker": "consultant", "text": f"Let's check in on {focus} since our last session."},
        {"speaker": "client", "text": f"I've noticed {focus} shows up most when I am tired and trying to push through anyway."},
        {"speaker": "consultant", "text": "What helped even a little when that happened this week?"},
        {"speaker": "client", "text": f"When I slowed down and used the plan we discussed, I felt less overwhelmed and more able to recover."},
        {"speaker": "consultant", "text": "Let's keep that response simple and repeatable for the next week."},
    ]
    return {"text": "\n".join(line["text"] for line in lines), "lines": lines}


def next_future_slot(days_ahead: int, hour: int, minute: int) -> datetime:
    now = datetime.now(timezone.utc)
    target = now + timedelta(days=days_ahead)
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0)


def ensure_meeting(db, client_id: str, consultant_id: str, title: str, meeting_type: str, repeat_frequency: str, start_at: datetime, duration_minutes: int, invite_message: str) -> None:
    existing = db.execute(
        """
        SELECT id
        FROM scheduled_meetings
        WHERE client_id = ? AND consultant_id = ? AND title = ? AND status IN ('scheduled', 'client_viewed', 'accepted', 'in_progress')
        LIMIT 1
        """,
        (client_id, consultant_id, title),
    ).fetchone()
    if existing:
        return
    token = new_access_token()
    access_link_id = create_client_access_link(
        db,
        client_id=client_id,
        created_by=consultant_id,
        token_hash=hash_access_token(token),
        expires_at=default_expiry(hours=24 * 45),
    )
    end_at = start_at + timedelta(minutes=duration_minutes)
    join_start, join_end = build_join_window(start_at, end_at)
    create_scheduled_meeting(
        db,
        client_id=client_id,
        consultant_id=consultant_id,
        meeting_type=meeting_type,
        repeat_frequency=repeat_frequency,
        transcription_enabled=True,
        audio_biomarkers_enabled=True,
        video_biomarkers_enabled=True,
        transcription_provider="agora_stt",
        transcription_language="en-GB",
        title=title,
        invite_message=invite_message,
        timezone_name="Europe/London",
        scheduled_start_at=iso_utc(start_at),
        scheduled_end_at=iso_utc(end_at),
        join_window_start_at=iso_utc(join_start),
        join_window_end_at=iso_utc(join_end),
        channel_name=get_pair_channel(consultant_id, client_id, meeting_type),
        response_access_link_id=access_link_id,
    )
    db.execute(
        """
        UPDATE scheduled_meetings
        SET invite_delivery_status = 'sent',
            invite_delivery_error = '',
            updated_at = CURRENT_TIMESTAMP
        WHERE client_id = ? AND consultant_id = ? AND title = ?
        """,
        (client_id, consultant_id, title),
    )


def seed_sessions(app, db, client_id: str, consultant_id: str, client: DemoClient) -> None:
    storage = EncryptedStorage(storage_root_for_client(client_id), app.config["MASTER_KEY"])
    base_time = datetime.now(timezone.utc) - timedelta(days=35)
    session_plan = [
        ("avatar_ai_session", client.session_focuses[0], 18),
        ("consultant_live_session", client.session_focuses[1], 50),
        ("avatar_ai_session", client.session_focuses[2], 22),
        ("consultant_live_session", client.session_focuses[3], 48),
        ("avatar_ai_session", client.session_focuses[4], 20),
    ]

    for index, (session_kind, focus, minutes) in enumerate(session_plan, start=1):
        session_id = f"demo-{client.slug}-{index:02d}"
        existing = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if existing:
            continue
        started_at = base_time + timedelta(days=(index - 1) * 8, hours=index)
        ended_at = started_at + timedelta(minutes=minutes)
        channel_name = get_pair_channel(consultant_id, client_id, "human" if session_kind == "consultant_live_session" else "ai")
        biomarkers = session_biomarkers(client, index, session_kind == "consultant_live_session", focus)
        summary = session_summary(client, session_kind, focus, index, biomarkers)
        transcript = transcript_text(client, focus, index) if session_kind == "consultant_live_session" else None

        summary_key = f"clients/{client_id}/sessions/{session_id}/summary.json.enc"
        biomarker_key = f"clients/{client_id}/sessions/{session_id}/biomarkers.json.enc"
        transcript_key = f"clients/{client_id}/sessions/{session_id}/transcript.json.enc" if transcript else None
        storage.put_json(summary_key, client_id, summary)
        storage.put_json(biomarker_key, client_id, biomarkers)
        if transcript and transcript_key:
            storage.put_json(transcript_key, client_id, transcript)

        upsert_session(
            db,
            session_id=session_id,
            client_id=client_id,
            consultant_id=consultant_id,
            session_kind=session_kind,
            meeting_id=None,
            transcription_enabled=1 if transcript else 0,
            audio_biomarkers_enabled=1,
            video_biomarkers_enabled=1,
            profile_name="therapy",
            channel_name=channel_name,
            started_at=iso_utc(started_at),
            ended_at=iso_utc(ended_at),
            duration_seconds=minutes * 60,
            status="completed",
            summary_storage_key=summary_key,
            transcript_storage_key=transcript_key,
            biomarker_storage_key=biomarker_key,
            memory_storage_key=None,
            urgent_escalation=1 if biomarkers["safety"]["highest_level"] >= 3 else 0,
            escalation_reason="Overwhelm and self-harm language" if biomarkers["safety"]["highest_level"] >= 3 else "",
        )


def ensure_client_summaries(storage: EncryptedStorage, db, client_id: str, client: DemoClient) -> None:
    ai_key = f"clients/{client_id}/ai_summary.json.enc"
    human_key = f"clients/{client_id}/human_summary.json.enc"
    ai_payload = {
        "key_point_summary": {"headline": "Client Key Point Summary - AI Sessions", "body": client.ai_kps},
        "brief_overview": "Client Key Point Summary - AI Sessions",
        "full_summary": client.ai_kps,
        "source": "demo-seed",
        "key_facts": [client.session_focuses[0], client.session_focuses[2], client.support_style],
        "open_threads": [client.session_focuses[-1]],
    }
    human_payload = {
        "key_point_summary": {"headline": "Client Key Point Summary - Human Sessions", "body": client.human_kps},
        "brief_overview": "Client Key Point Summary - Human Sessions",
        "full_summary": client.human_kps,
        "source": "demo-seed",
        "key_facts": [client.session_focuses[1], client.session_focuses[3], client.support_style],
        "open_threads": [client.direction],
    }
    storage.put_json(ai_key, client_id, ai_payload)
    storage.put_json(human_key, client_id, human_payload)
    db.execute(
        """
        UPDATE clients
        SET ai_summary_storage_key = ?,
            human_summary_storage_key = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (ai_key, human_key, client_id),
    )


def ensure_messages(db, client_id: str, consultant_id: str, client: DemoClient) -> None:
    count = db.execute("SELECT COUNT(*) AS c FROM client_messages WHERE client_id = ?", (client_id,)).fetchone()["c"]
    if count:
        return
    create_client_message(
        db,
        client_id=client_id,
        consultant_id=consultant_id,
        direction="outbound",
        channel="email",
        subject="Short check-in between sessions",
        body=f"Hi {client.first_name}, just a short check-in. Keep your focus this week on {client.session_focuses[0]} and use the smallest version of the plan if energy is low.",
        delivery_status="sent",
        read_by_consultant_at=iso_utc(datetime.now(timezone.utc) - timedelta(days=2)),
    )
    create_client_message(
        db,
        client_id=client_id,
        consultant_id=consultant_id,
        direction="inbound",
        channel="portal",
        subject="Quick update",
        body=f"Thanks. The main thing I noticed was {client.session_focuses[2]} coming up again, but the structure helped and I felt steadier by the end of the day.",
        delivery_status="received",
        read_by_consultant_at=iso_utc(datetime.now(timezone.utc) - timedelta(days=1)),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed realistic demo clients, sessions, meetings, and summaries.")
    parser.add_argument("--consultant-email", default="benweekes73@gmail.com")
    parser.add_argument("--limit", type=int, default=11)
    args = parser.parse_args()

    load_dotenv()
    app = create_app()
    with app.app_context():
        db = get_db(app.config)
        consultant = db.execute(
            "SELECT id, vendor_id, email FROM consultants WHERE lower(email) = lower(?) LIMIT 1",
            (args.consultant_email,),
        ).fetchone()
        if consultant is None:
            consultant = db.execute("SELECT id, vendor_id, email FROM consultants ORDER BY created_at ASC LIMIT 1").fetchone()
        if consultant is None:
            raise RuntimeError("No consultant found to seed demo practice.")

        seeded_clients = 0
        for offset, demo in enumerate(DEMO_CLIENTS[: args.limit], start=1):
            existing = get_client_by_email(db, demo.email, vendor_id=consultant["vendor_id"])
            if existing:
                client_id = existing["id"]
            else:
                client_id = create_client(
                    db,
                    consultant_id=consultant["id"],
                    vendor_id=consultant["vendor_id"],
                    first_name=demo.first_name,
                    last_name=demo.last_name,
                    email=demo.email,
                    password_hash="",
                    phone_number=demo.phone_number,
                    notification_email=demo.notification_email,
                    escalation_phone_number="",
                    year_of_birth=demo.year_of_birth,
                    sex=demo.sex,
                    notes=demo.notes,
                    direction=demo.direction,
                )
                seeded_clients += 1
            db.execute(
                """
                UPDATE clients
                SET year_of_birth = ?,
                    sex = ?,
                    notes_current = ?,
                    direction_current = ?,
                    notification_email = ?,
                    phone_number = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    demo.year_of_birth,
                    demo.sex,
                    demo.notes,
                    demo.direction,
                    demo.notification_email,
                    demo.phone_number,
                    client_id,
                ),
            )

            storage = EncryptedStorage(storage_root_for_client(client_id), app.config["MASTER_KEY"])
            ensure_client_summaries(storage, db, client_id, demo)
            ensure_messages(db, client_id, consultant["id"], demo)
            seed_sessions(app, db, client_id, consultant["id"], demo)
            ensure_meeting(
                db,
                client_id,
                consultant["id"],
                title="Weekly AI Check-In",
                meeting_type="ai",
                repeat_frequency="weekly",
                start_at=next_future_slot(3 + (offset % 5), 18 + (offset % 3), 0),
                duration_minutes=25,
                invite_message=f"Use this AI session to check in on {demo.session_focuses[0]} and keep momentum between therapist appointments.",
            )
            ensure_meeting(
                db,
                client_id,
                consultant["id"],
                title="Monthly Human Review",
                meeting_type="human",
                repeat_frequency="monthly",
                start_at=next_future_slot(10 + (offset % 7), 12 + (offset % 4), 30),
                duration_minutes=50,
                invite_message=f"We will review progress around {demo.session_focuses[1]} and update next steps together.",
            )
            _refresh_client_derived_state(db, storage, client_id)

        db.commit()
        db.close()
        print(f"Seeded {seeded_clients} new demo clients for consultant {consultant['email']}.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
