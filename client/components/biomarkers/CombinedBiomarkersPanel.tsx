"use client";

import type { ReactNode } from "react";
import {
  Activity,
  Heart,
  SmilePlus,
  Shield,
} from "lucide-react";

import { cn } from "@/lib/utils";

type ProgressState = Record<
  string,
  {
    speech_seconds: number;
    trigger_seconds: number;
    processing: boolean;
  }
>;

type ShenLikeState = {
  heartRate: number | null;
  hrvSdnn: number | null;
  stressIndex: number | null;
  breathingRate: number | null;
  systolicBP: number | null;
  diastolicBP: number | null;
  cardiacWorkload: number | null;
  signalQuality: number | null;
};

type Props = {
  biomarkers: Record<string, number | null>;
  wellness: Record<string, number | null>;
  clinical: Record<string, number | null>;
  safety: Record<string, unknown>;
  progress: ProgressState;
  shenState: ShenLikeState;
  isConnected: boolean;
  voiceEnabled: boolean;
  videoEnabled: boolean;
};

const EMOTIONS = [
  { key: "happy", label: "Happiness", emoji: "😄" },
  { key: "sad", label: "Sadness", emoji: "😢" },
  { key: "angry", label: "Anger", emoji: "😠" },
  { key: "fearful", label: "Fear", emoji: "😨" },
  { key: "surprised", label: "Surprise", emoji: "😲" },
  { key: "disgusted", label: "Disgust", emoji: "🤢" },
  { key: "neutral", label: "Neutral", emoji: "🙂" },
  { key: "other", label: "Other", emoji: "🙂" },
] as const;

function percentageValue(...values: Array<number | null | undefined>): number | null {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return Math.max(0, Math.min(100, Math.round(value * 100)));
    }
  }
  return null;
}

function numericValue(...values: Array<number | null | undefined>): number | null {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }
  return null;
}

function gaugeColor(value: number | null): string {
  if (value === null) return "#7b8b99";
  if (value < 35) return "#2bb58e";
  if (value < 65) return "#f0a43b";
  return "#ef6b64";
}

function safetyTone(level: number | null): string {
  if (level === null) return "text-[#9db0bc]";
  if (level <= 0) return "text-[#2bb58e]";
  if (level === 1) return "text-[#f0a43b]";
  return "text-[#ef6b64]";
}

function formatPolicyName(policyName: unknown): string | null {
  if (typeof policyName !== "string" || !policyName.trim()) return null;
  if (policyName === "mindfix_safety_v1") return "Custom policy";
  if (policyName === "agora_safety_analysis" || policyName === "safety_analysis") {
    return "Default policy";
  }
  return policyName.replace(/_/g, " ");
}

function formatProgressName(name: string): string {
  return name
    .replace(/^symptom_/, "")
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatMetricValue(
  key: "bloodPressure" | "signalQuality" | "heartRate" | "hrvSdnn" | "stressIndex" | "breathingRate" | "cardiacWorkload",
  shenState: ShenLikeState,
): string | null {
  switch (key) {
    case "bloodPressure": {
      const systolic = numericValue(shenState.systolicBP);
      const diastolic = numericValue(shenState.diastolicBP);
      if (systolic === null && diastolic === null) return null;
      return `${systolic !== null ? Math.round(systolic) : "--"}/${diastolic !== null ? Math.round(diastolic) : "--"}`;
    }
    case "signalQuality": {
      const quality = numericValue(shenState.signalQuality);
      return quality === null ? null : String(Math.round(quality * 100));
    }
    case "heartRate": {
      const hr = numericValue(shenState.heartRate);
      return hr === null ? null : String(Math.round(hr));
    }
    case "hrvSdnn": {
      const value = numericValue(shenState.hrvSdnn);
      return value === null ? null : String(Math.round(value));
    }
    case "stressIndex": {
      const value = numericValue(shenState.stressIndex);
      return value === null ? null : value.toFixed(2);
    }
    case "breathingRate": {
      const value = numericValue(shenState.breathingRate);
      return value === null ? null : String(Math.round(value));
    }
    case "cardiacWorkload": {
      const value = numericValue(shenState.cardiacWorkload);
      return value === null ? null : String(Math.round(value));
    }
    default:
      return null;
  }
}

function SemiGauge({
  label,
  value,
}: {
  label: string;
  value: number | null;
}) {
  const radius = 46;
  const circumference = Math.PI * radius;
  const progress = value === null ? circumference * 0.45 : circumference * (1 - value / 100);
  return (
    <div className="min-h-[5.6rem] rounded-[1.15rem] border border-white/8 bg-white/[0.025] px-3.5 py-3 shadow-[0_12px_28px_rgba(0,0,0,0.16)]">
      <div className="flex items-start justify-between gap-2.5">
        <div>
          <p className="text-[0.62rem] font-semibold uppercase tracking-[0.2em] text-[#89a8ba]">
            {label}
          </p>
          <p
            className={cn(
              "mt-1 text-[1.45rem] font-semibold tracking-[-0.04em] text-white",
              value === null && "animate-pulse text-[#8094a3]",
            )}
          >
            {value === null ? "--" : `${value}%`}
          </p>
        </div>
        <svg viewBox="0 0 124 76" className="mt-0.5 h-14 w-20 shrink-0">
          <path
            d="M 16 62 A 46 46 0 0 1 108 62"
            fill="none"
            stroke="rgba(255,255,255,0.08)"
            strokeWidth="10"
            strokeLinecap="round"
          />
          <path
            d="M 16 62 A 46 46 0 0 1 108 62"
            fill="none"
            stroke={gaugeColor(value)}
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={progress}
            className={cn("transition-all duration-500", value === null && "animate-pulse")}
          />
        </svg>
      </div>
    </div>
  );
}

function SummaryCard({
  icon,
  title,
  value,
  detail,
  pulse = false,
  valueClassName,
}: {
  icon: ReactNode;
  title: string;
  value: string;
  detail?: string;
  pulse?: boolean;
  valueClassName?: string;
}) {
  return (
    <div className="min-h-[5.6rem] rounded-[1.15rem] border border-white/8 bg-white/[0.025] px-3.5 py-3 shadow-[0_12px_28px_rgba(0,0,0,0.16)]">
      <div className="flex items-start justify-between gap-2.5">
        <div className="min-w-0">
          <p className="text-[0.62rem] font-semibold uppercase tracking-[0.2em] text-[#89a8ba]">
            {title}
          </p>
          <p
            className={cn(
              "mt-1 text-[1.45rem] font-semibold tracking-[-0.04em] text-white",
              pulse && "animate-pulse text-[#8094a3]",
              valueClassName,
            )}
          >
            {value}
          </p>
          {detail ? <p className="mt-0.5 line-clamp-2 text-[0.72rem] text-[#9db0bc]">{detail}</p> : null}
        </div>
        <div className="flex h-8 w-8 items-center justify-center rounded-[0.9rem] bg-white/6 text-[#2bb58e] ring-1 ring-white/10">
          {icon}
        </div>
      </div>
    </div>
  );
}

function MetricTile({
  label,
  value,
  unit,
  pulse,
}: {
  label: string;
  value: string | null;
  unit?: string;
  pulse?: boolean;
}) {
  return (
    <div className="rounded-[1.05rem] border border-white/8 bg-white/[0.03] px-3 py-2.5">
      <p className="text-[0.65rem] font-medium uppercase tracking-[0.16em] text-[#7f99ab]">
        {label}
      </p>
      <p
        className={cn(
          "mt-1.5 text-lg font-semibold tracking-[-0.03em] text-white",
          (pulse || value === null) && "animate-pulse text-[#8195a4]",
        )}
      >
        {value ?? "--"}
        {value !== null && unit ? <span className="ml-1 text-xs text-[#9db0bc]">{unit}</span> : null}
      </p>
    </div>
  );
}

function SectionCard({
  title,
  eyebrow,
  children,
}: {
  title: string;
  eyebrow?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-[1.25rem] border border-white/8 bg-white/[0.018] px-3.5 py-3.5 shadow-[0_12px_28px_rgba(0,0,0,0.14)]">
      {eyebrow ? (
        <p className="text-[0.62rem] font-semibold uppercase tracking-[0.2em] text-[#89a8ba]">
          {eyebrow}
        </p>
      ) : null}
      <h3 className="mt-1 text-[0.98rem] font-semibold tracking-[-0.03em] text-white">
        {title}
      </h3>
      <div className="mt-2.5">{children}</div>
    </section>
  );
}

function DisabledSource({
  title,
  message,
}: {
  title: string;
  message: string;
}) {
  return (
    <SectionCard title={title}>
      <div className="rounded-[1.05rem] border border-dashed border-white/10 bg-white/[0.03] px-4 py-4 text-sm text-[#95aab7]">
        {message}
      </div>
    </SectionCard>
  );
}

export function CombinedBiomarkersPanel({
  biomarkers,
  wellness,
  clinical,
  safety,
  progress,
  shenState,
  isConnected,
  voiceEnabled,
  videoEnabled,
}: Props) {
  const stress = percentageValue(wellness.stress, biomarkers.stress);
  const distress = percentageValue(wellness.distress, biomarkers.distress);
  const burnout = percentageValue(wellness.burnout, biomarkers.burnout);
  const fatigue = percentageValue(wellness.fatigue, biomarkers.fatigue);
  const lowSelfEsteem = percentageValue(wellness.low_self_esteem, biomarkers.low_self_esteem);
  const depression = percentageValue(
    clinical.depression_probability,
    biomarkers.depression_probability,
  );
  const anxiety = percentageValue(
    clinical.anxiety_probability,
    biomarkers.anxiety_probability,
  );

  const dominantEmotion = EMOTIONS.map((emotion) => ({
    ...emotion,
    score: percentageValue(biomarkers[emotion.key]),
  }))
    .filter((emotion) => emotion.score !== null)
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))[0];

  const safetyLevel =
    typeof safety.level === "number"
      ? safety.level
      : typeof safety.highest_level === "number"
        ? safety.highest_level
        : null;
  const activePolicy = formatPolicyName(safety.active_policy);
  const alertDetail =
    typeof safety.alert === "string" ? safety.alert.replace(/_/g, " ") : null;
  const safetyDetail = [alertDetail, activePolicy].filter(Boolean).join(" · ") || "Details below";
  const recommendedActions =
    safety.recommended_actions && typeof safety.recommended_actions === "object"
      ? (safety.recommended_actions as Record<string, unknown>)
      : null;

  const heartRate = formatMetricValue("heartRate", shenState);
  const hrv = formatMetricValue("hrvSdnn", shenState);
  const breathingRate = formatMetricValue("breathingRate", shenState);
  const cardiacStress = formatMetricValue("stressIndex", shenState);
  const bloodPressure = formatMetricValue("bloodPressure", shenState);
  const cardiacWorkload = formatMetricValue("cardiacWorkload", shenState);
  const signalQuality = formatMetricValue("signalQuality", shenState);

  const progressEntries = Object.entries(progress);
  const showPanel = voiceEnabled || videoEnabled;

  if (!isConnected) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="text-center">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-white/6 text-[#2bb58e]">
            <Activity className="h-6 w-6" />
          </div>
          <p className="mt-4 text-sm text-[#93a8b5]">Connect to view live biomarkers.</p>
        </div>
      </div>
    );
  }

  if (!showPanel) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="max-w-sm rounded-[1.5rem] border border-white/10 bg-[rgba(13,24,34,0.88)] px-5 py-6 text-center text-sm text-[#95aab7]">
          Biomarkers were not enabled for this session.
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-2.5 overflow-auto p-2 md:p-2.5">
      <div className="grid grid-cols-2 gap-2.5 xl:grid-cols-4">
        {voiceEnabled ? (
          <SemiGauge label="Stress" value={stress} />
        ) : null}

        {videoEnabled ? (
          <SummaryCard
            icon={<Heart className="h-5 w-5" />}
            title="Heart Rate"
            value={heartRate ? `${heartRate} bpm` : "--"}
            pulse={!heartRate}
          />
        ) : null}

        {voiceEnabled ? (
          <SummaryCard
            icon={<SmilePlus className="h-4 w-4" />}
            title="Leading Emotion"
            value={dominantEmotion?.emoji ?? "--"}
            detail={dominantEmotion?.label ?? "Waiting for signal"}
            pulse={!dominantEmotion}
          />
        ) : null}

        {voiceEnabled ? (
          <SummaryCard
            icon={<Shield className="h-5 w-5" />}
            title="Safety"
            value={safetyLevel === null ? "--" : `Level ${safetyLevel}`}
            detail={safetyDetail}
            pulse={safetyLevel === null}
            valueClassName={safetyTone(safetyLevel)}
          />
        ) : null}
      </div>

      {voiceEnabled ? (
        <SectionCard title="Voice Biomarkers">
          <div className="space-y-2.5">
            <div className="flex flex-wrap gap-2">
              {progressEntries.length > 0 ? (
                progressEntries.map(([name, info]) => (
                  <div
                    key={name}
                    className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[0.7rem] text-[#9db0bc]"
                  >
                    <span className="font-medium text-white">{formatProgressName(name)}</span>{" "}
                    {info.speech_seconds.toFixed(1)}/{info.trigger_seconds}s
                    {info.processing ? (
                      <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-[#2bb58e] align-middle animate-pulse" />
                    ) : null}
                  </div>
                ))
              ) : (
                <div className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[0.7rem] text-[#9db0bc] animate-pulse">
                  Listening for enough speech to analyse
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-2 xl:grid-cols-3">
              <MetricTile label="Stress" value={stress !== null ? `${stress}%` : null} pulse={stress === null} />
              <MetricTile label="Distress" value={distress !== null ? `${distress}%` : null} pulse={distress === null} />
              <MetricTile label="Burnout" value={burnout !== null ? `${burnout}%` : null} pulse={burnout === null} />
              <MetricTile label="Fatigue" value={fatigue !== null ? `${fatigue}%` : null} pulse={fatigue === null} />
              <MetricTile label="Depression" value={depression !== null ? `${depression}%` : null} pulse={depression === null} />
              <MetricTile label="Anxiety" value={anxiety !== null ? `${anxiety}%` : null} pulse={anxiety === null} />
            </div>

            <details className="group rounded-[1rem] border border-white/8 bg-white/[0.028] px-3 py-2.5">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-4">
                <div>
                  <p className="text-[0.65rem] font-medium uppercase tracking-[0.16em] text-[#7f99ab]">
                    Safety Details
                  </p>
                  <p className="mt-0.5 text-[0.72rem] text-[#9db0bc]">
                    {safetyLevel === null ? "Waiting for assessment" : `Level ${safetyLevel}`}
                  </p>
                </div>
                <span className="text-[#9db0bc] transition group-open:rotate-180">⌄</span>
              </summary>
              <div className="mt-2.5 grid grid-cols-2 gap-2 text-sm text-[#c8d6df]">
                <MetricTile
                  label="Current Alert"
                  value={alertDetail}
                  pulse={!alertDetail}
                />
                <MetricTile
                  label="Highest Level"
                  value={typeof safety.highest_level === "number" ? `Level ${safety.highest_level}` : null}
                  pulse={typeof safety.highest_level !== "number"}
                />
                <MetricTile
                  label="Active Policy"
                  value={activePolicy}
                  pulse={!activePolicy}
                />
              </div>
              <div className="mt-2.5 space-y-2 text-sm text-[#b5c7d4]">
                {typeof safety.rationale === "string" ? (
                  <p><span className="font-medium text-white">Rationale:</span> {safety.rationale}</p>
                ) : null}
                {Array.isArray(safety.concerns) && safety.concerns.length > 0 ? (
                  <p><span className="font-medium text-white">Concerns:</span> {safety.concerns.join(", ")}</p>
                ) : null}
                {recommendedActions ? (
                  <div className="space-y-2">
                    {typeof recommendedActions.for_agent === "string" ? (
                      <p><span className="font-medium text-white">Guidance:</span> {recommendedActions.for_agent}</p>
                    ) : null}
                    {typeof recommendedActions.for_human_reviewer === "string" ? (
                      <p><span className="font-medium text-white">Reviewer:</span> {recommendedActions.for_human_reviewer}</p>
                    ) : null}
                  </div>
                ) : null}
              </div>
              <div className="mt-3">
                <p className="text-[0.65rem] font-medium uppercase tracking-[0.16em] text-[#7f99ab]">
                  Full Voice Breakdown
                </p>
                <div className="mt-2 grid grid-cols-2 gap-2 xl:grid-cols-3">
                  <MetricTile label="Stress" value={stress !== null ? `${stress}%` : null} pulse={stress === null} />
                  <MetricTile label="Distress" value={distress !== null ? `${distress}%` : null} pulse={distress === null} />
                  <MetricTile label="Burnout" value={burnout !== null ? `${burnout}%` : null} pulse={burnout === null} />
                  <MetricTile label="Fatigue" value={fatigue !== null ? `${fatigue}%` : null} pulse={fatigue === null} />
                  <MetricTile label="Low Self-Esteem" value={lowSelfEsteem !== null ? `${lowSelfEsteem}%` : null} pulse={lowSelfEsteem === null} />
                  <MetricTile label="Depression" value={depression !== null ? `${depression}%` : null} pulse={depression === null} />
                  <MetricTile label="Anxiety" value={anxiety !== null ? `${anxiety}%` : null} pulse={anxiety === null} />
                </div>
              </div>
            </details>
          </div>
        </SectionCard>
      ) : (
        <DisabledSource
          title="Voice Biomarkers"
          message="Voice biomarkers were not enabled for this session."
        />
      )}

      {videoEnabled ? (
        <SectionCard title="Video Biomarkers">
          <div className="grid grid-cols-2 gap-2 xl:grid-cols-3">
            <MetricTile label="HRV" value={hrv} unit="ms" pulse={!hrv} />
            <MetricTile label="Cardiac Stress" value={cardiacStress} pulse={!cardiacStress} />
            <MetricTile label="Breathing Rate" value={breathingRate} unit="bpm" pulse={!breathingRate} />
            <MetricTile label="Blood Pressure" value={bloodPressure} unit="mmHg" pulse={!bloodPressure} />
            <MetricTile label="Cardiac Workload" value={cardiacWorkload} pulse={!cardiacWorkload} />
            <MetricTile label="Signal Quality" value={signalQuality} unit="%" pulse={!signalQuality} />
          </div>
        </SectionCard>
      ) : null}
    </div>
  );
}
