"use client";

import { useCallback, useEffect, useState } from "react";

import type { RTMEventSource } from "@agora/agent-ui-kit/thymia";

export interface MindfixThymiaState {
  biomarkers: Record<string, number | null>;
  wellness: Record<string, number | null>;
  clinical: Record<string, number | null>;
  safety: Record<string, unknown>;
  progress: Record<
    string,
    {
      speech_seconds: number;
      trigger_seconds: number;
      processing: boolean;
    }
  >;
}

function extractNumberRecord(
  val: unknown,
): Record<string, number | null> | null {
  if (typeof val !== "object" || val === null || Array.isArray(val)) return null;
  const result: Record<string, number | null> = {};
  let count = 0;
  for (const [k, v] of Object.entries(val as Record<string, unknown>)) {
    if (typeof v === "number" || v === null) {
      result[k] = v as number | null;
      count++;
    }
  }
  return count > 0 ? result : null;
}

function isPlainObject(val: unknown): val is Record<string, unknown> {
  return typeof val === "object" && val !== null && !Array.isArray(val);
}

function isProgressRecord(val: unknown): val is MindfixThymiaState["progress"] {
  if (!isPlainObject(val)) return false;
  return Object.values(val).every(
    (v) =>
      isPlainObject(v) &&
      typeof v.speech_seconds === "number" &&
      typeof v.trigger_seconds === "number" &&
      typeof v.processing === "boolean",
  );
}

export function useMindfixThymia(
  rtmSource: RTMEventSource | null,
  enabled: boolean = true,
): MindfixThymiaState {
  const [biomarkers, setBiomarkers] = useState<Record<string, number | null>>(
    {},
  );
  const [wellness, setWellness] = useState<Record<string, number | null>>({});
  const [clinical, setClinical] = useState<Record<string, number | null>>({});
  const [safety, setSafety] = useState<Record<string, unknown>>({});
  const [progress, setProgress] = useState<MindfixThymiaState["progress"]>({});

  const handleMessage = useCallback(
    (event: { message: string | Uint8Array }) => {
      try {
        let raw: string;
        if (typeof event.message === "string") {
          raw = event.message;
        } else if (event.message instanceof Uint8Array) {
          raw = new TextDecoder("utf-8").decode(event.message);
        } else {
          return;
        }

        const msg = JSON.parse(raw) as Record<string, unknown>;
        if (msg.object === "thymia.biomarkers") {
          const nextBiomarkers = extractNumberRecord(msg.biomarkers) ?? {};
          const nextWellness = extractNumberRecord(msg.wellness) ?? {};
          const nextClinical = extractNumberRecord(msg.clinical) ?? {};
          const nextSafety = isPlainObject(msg.safety) ? msg.safety : {};

          setBiomarkers(nextBiomarkers);
          setWellness(nextWellness);
          setClinical(nextClinical);
          setSafety(nextSafety);
          return;
        }

        if (msg.object === "thymia.progress" && isProgressRecord(msg.progress)) {
          setProgress(msg.progress);
        }
      } catch {
        // Ignore malformed RTM payloads.
      }
    },
    [],
  );

  useEffect(() => {
    if (!enabled) {
      setBiomarkers({});
      setWellness({});
      setClinical({});
      setSafety({});
      setProgress({});
      return;
    }
    if (!rtmSource) return;

    rtmSource.on("message", handleMessage);
    return () => {
      rtmSource.off("message", handleMessage);
    };
  }, [enabled, handleMessage, rtmSource]);

  return { biomarkers, wellness, clinical, safety, progress };
}
