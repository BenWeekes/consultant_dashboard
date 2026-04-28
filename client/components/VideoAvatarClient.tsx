"use client";

import { useState, useRef, useEffect, useMemo, useCallback } from "react";
import {
  Brain,
  Mic,
  MicOff,
  Video,
  VideoOff,
  Settings,
  Phone,
  PhoneOff,
  SendHorizontal,
} from "lucide-react";
import { useAgoraVideoClient } from "@/hooks/useAgoraVideoClient";
import { useAudioVisualization } from "@/hooks/useAudioVisualization";
import { IconButton } from "@agora/agent-ui-kit";
import { Conversation, ConversationContent } from "@agora/agent-ui-kit";
import { Message, MessageContent } from "@agora/agent-ui-kit";
import { Response } from "@agora/agent-ui-kit";
import { AvatarVideoDisplay, LocalVideoPreview } from "@agora/agent-ui-kit";
import { VideoGrid, MobileTabs } from "@agora/agent-ui-kit";
import { SettingsDialog, SessionPanel } from "@agora/agent-ui-kit";
import { useShenai } from "@/hooks/useShenai";
import { useMindfixThymia } from "@/hooks/useMindfixThymia";
import AgoraRTC from "agora-rtc-sdk-ng";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "./ThemeToggle";
import { CombinedBiomarkersPanel } from "./biomarkers/CombinedBiomarkersPanel";

function getBackendOverride(params: URLSearchParams): string | null {
  return params.get("backend") || params.get("backend_url");
}

function resolveDefaultBackendUrl() {
  if (typeof window !== "undefined") {
    const override = getBackendOverride(
      new URLSearchParams(window.location.search),
    );
    if (override) {
      return override;
    }
    if (process.env.NEXT_PUBLIC_BACKEND_URL !== undefined) {
      return process.env.NEXT_PUBLIC_BACKEND_URL;
    }
    const { hostname, protocol } = window.location;
    if (hostname === "localhost" || hostname === "127.0.0.1") {
      return `${protocol}//${hostname}:8082`;
    }
  }
  return process.env.NEXT_PUBLIC_BACKEND_URL ?? "";
}

const DEFAULT_BACKEND_URL = resolveDefaultBackendUrl();
const DEFAULT_PROFILE = process.env.NEXT_PUBLIC_DEFAULT_PROFILE || "VIDEO";
const SHEN_API_KEY = process.env.NEXT_PUBLIC_SHEN_API_KEY || "";
const DEVICE_COOKIE_MAX_AGE = 60 * 60 * 24 * 180;
const MEETING_BOOTSTRAP_STORAGE_KEY = "mindfix_meeting_join_bootstrap";
const MEETING_ACCESS_TOKEN_STORAGE_KEY = "mindfix_meeting_access_token";

const SENSITIVE_KEYS = [
  "api_key",
  "key",
  "token",
  "adc_credentials_string",
  "subscriber_token",
  "rtm_token",
  "ticket",
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function redactSensitiveFields(obj: any): any {
  if (typeof obj !== "object" || obj === null) return obj;
  if (Array.isArray(obj)) return obj.map(redactSensitiveFields);
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (SENSITIVE_KEYS.includes(k) && typeof v === "string" && v.length > 6) {
      out[k] = v.slice(0, 6) + "***";
    } else {
      out[k] = redactSensitiveFields(v);
    }
  }
  return out;
}

function readCookie(name: string): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${name.replace(/[$()*+./?[\\\]^{|}-]/g, "\\$&")}=([^;]*)`),
  );
  return match ? decodeURIComponent(match[1]) : "";
}

function persistDeviceChoice(name: string, value: string) {
  if (typeof document === "undefined") return;
  document.cookie = `${name}=${encodeURIComponent(value)}; Max-Age=${DEVICE_COOKIE_MAX_AGE}; Path=/; SameSite=Lax`;
}

function clearPersistedDeviceChoice(name: string) {
  if (typeof document === "undefined") return;
  document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax`;
}

function getStoredDeviceChoice(name: string, legacyLocalStorageKey: string): string {
  if (typeof window === "undefined") return "";
  return readCookie(name) || window.localStorage.getItem(legacyLocalStorageKey) || "";
}

function getSessionValue(key: string): string {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(key) || "";
}

function setSessionValue(key: string, value: string) {
  if (typeof window === "undefined") return;
  if (value) {
    window.sessionStorage.setItem(key, value);
  } else {
    window.sessionStorage.removeItem(key);
  }
}

function decodeJoinBootstrapRole(token: string): string {
  if (!token || !token.includes(".")) return "";
  try {
    const [encoded] = token.split(".", 1);
    const padded = encoded.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(encoded.length / 4) * 4, "=");
    const payload = JSON.parse(window.atob(padded));
    return String(payload?.participant_role || "").trim().toLowerCase();
  } catch {
    return "";
  }
}

function fallbackConsultantDashboardUrl() {
  if (typeof window === "undefined") return "/consultant/dashboard";
  return `${window.location.origin}/consultant/dashboard`;
}

export function VideoAvatarClient() {
  const [backendUrl, setBackendUrl] = useState(DEFAULT_BACKEND_URL);
  const [agentId, setAgentId] = useState<string | undefined>(undefined);
  const [isLoading, setIsLoading] = useState(false);
  const [chatMessage, setChatMessage] = useState("");
  const [enableLocalVideo, setEnableLocalVideo] = useState(true);
  const [enableAvatar, setEnableAvatar] = useState(true);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [enableAivad, setEnableAivad] = useState(true);
  const [language, setLanguage] = useState("en-US");
  const [profile, setProfile] = useState("");
  const [prompt, setPrompt] = useState("");
  const [greeting, setGreeting] = useState("");
  const [activeTab, setActiveTab] = useState("video");
  const _conversationRef = useRef<HTMLDivElement>(null);
  const [autoConnect, setAutoConnect] = useState(false);
  const [returnUrl, setReturnUrl] = useState<string | null>(null);
  const channelRef = useRef<string | null>(null);
  const [selectedMic, setSelectedMic] = useState(() =>
    typeof window !== "undefined"
      ? getStoredDeviceChoice("mindfix_selected_mic", "selectedMicId")
      : "",
  );
  const [sessionAgentId, setSessionAgentId] = useState<string | null>(null);
  const [sessionPayload, setSessionPayload] = useState<object | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [authUser, setAuthUser] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [meetingMode, setMeetingMode] = useState(false);
  const [meetingParticipantRole, setMeetingParticipantRole] = useState<
    "host" | "guest" | null
  >(null);
  const [meetingTranscriptionEnabled, setMeetingTranscriptionEnabled] = useState(false);
  const [meetingAudioBiomarkersEnabled, setMeetingAudioBiomarkersEnabled] = useState(true);
  const [meetingVideoBiomarkersEnabled, setMeetingVideoBiomarkersEnabled] = useState(true);
  const [meetingInitError, setMeetingInitError] = useState<string | null>(null);
  const [meetingJoinReady, setMeetingJoinReady] = useState(true);
  const [availableMics, setAvailableMics] = useState<MediaDeviceInfo[]>([]);
  const [availableCameras, setAvailableCameras] = useState<MediaDeviceInfo[]>([]);
  const [meetingPreviewStream, setMeetingPreviewStream] = useState<MediaStream | null>(null);
  const [meetingMicLevel, setMeetingMicLevel] = useState(0);
  const [selectedCamera, setSelectedCamera] = useState(() =>
    typeof window !== "undefined"
      ? getStoredDeviceChoice("mindfix_selected_camera", "selectedCameraId")
      : "",
  );
  const meetingAccessTokenRef = useRef<string | null>(null);
  const meetingJoinBootstrapRef = useRef<string | null>(null);
  const previewVideoRef = useRef<HTMLVideoElement | null>(null);
  const previewStreamRef = useRef<MediaStream | null>(null);
  const previewAudioContextRef = useRef<AudioContext | null>(null);
  const previewAnalyserRef = useRef<AnalyserNode | null>(null);
  const previewAnimationFrameRef = useRef<number | null>(null);
  const lastBiomarkerLogRef = useRef(0);
  // Legacy bearer fallback held in memory only. Normal auth now rides on the
  // 1-hour backend cookie so hosted meeting/invite flows do not leak auth state
  // through URLs.
  const authTokenRef = useRef<string | null>(null);

  useEffect(() => {
    console.log(
      "[MindFixClient][build]",
      "2026-04-28-biomarker-rtm-debug-2",
    );
  }, []);

  // Helper — sends backend cookies and, for legacy compatibility, an
  // Authorization header if a transient in-memory token exists.
  const fetchWithAuth = (url: string, options?: RequestInit) => {
    const headers: Record<string, string> = {
      ...(options?.headers as Record<string, string> || {}),
    };
    if (authTokenRef.current) {
      headers["Authorization"] = `Bearer ${authTokenRef.current}`;
    }
    return fetch(url, { ...options, headers, credentials: "include" });
  };

  // Read URL parameters on mount + auth check
  useEffect(() => {
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      const backendOverride = getBackendOverride(params);
      const urlProfile = params.get("profile");
      if (backendOverride) {
        setBackendUrl(backendOverride);
      }
      if (urlProfile) {
        setProfile(urlProfile);
      }
      if (params.get("autoconnect") === "true") {
        setAutoConnect(true);
      }
      if (params.get("meeting_mode") === "true") {
        setMeetingMode(true);
      }
      const meetingAccessToken = params.get("access_token");
      if (meetingAccessToken) {
        meetingAccessTokenRef.current = meetingAccessToken;
        setSessionValue(MEETING_ACCESS_TOKEN_STORAGE_KEY, meetingAccessToken);
        params.delete("access_token");
      } else if (params.get("meeting_mode") === "true") {
        const storedAccessToken = getSessionValue(MEETING_ACCESS_TOKEN_STORAGE_KEY);
        if (storedAccessToken) {
          meetingAccessTokenRef.current = storedAccessToken;
        }
      }
      const joinBootstrap = params.get("join_bootstrap");
      if (joinBootstrap) {
        meetingJoinBootstrapRef.current = joinBootstrap;
        setSessionValue(MEETING_BOOTSTRAP_STORAGE_KEY, joinBootstrap);
        params.delete("join_bootstrap");
      } else if (params.get("meeting_mode") === "true") {
        const storedJoinBootstrap = getSessionValue(MEETING_BOOTSTRAP_STORAGE_KEY);
        if (storedJoinBootstrap) {
          meetingJoinBootstrapRef.current = storedJoinBootstrap;
        }
      }
      const ru = params.get("returnurl");
      if (ru) {
        setReturnUrl(ru);
      }

      let cleanedUrl = false;

      // Legacy compatibility: accept an auth token in the URL if an older
      // auth flow returns one, but prefer the shared auth cookie.
      const authToken = params.get("auth_token");
      if (authToken) {
        // Store in memory only — not sessionStorage
        authTokenRef.current = authToken;
        params.delete("auth_token");
        cleanedUrl = true;
      }

      if (cleanedUrl || meetingAccessToken || joinBootstrap) {
        const cleanUrl = `${window.location.pathname}${params.toString() ? "?" + params.toString() : ""}`;
        window.history.replaceState({}, "", cleanUrl);
      }

      const effectiveProfile = urlProfile || DEFAULT_PROFILE;
      const effectiveBackend = backendOverride || DEFAULT_BACKEND_URL;
      const token = authTokenRef.current;
      const currentUrl = window.location.href;
      const authHeaders: Record<string, string> = {};
      if (token) authHeaders["Authorization"] = `Bearer ${token}`;

      if (params.get("meeting_mode") === "true") {
        const hasMeetingCredential = Boolean(
          meetingAccessTokenRef.current || meetingJoinBootstrapRef.current,
        );
        setMeetingJoinReady(hasMeetingCredential);
        if (!hasMeetingCredential) {
          setMeetingInitError("This meeting link is missing or expired.");
          setAuthChecked(true);
          return;
        }

        const participantRole = meetingAccessTokenRef.current
          ? "guest"
          : decodeJoinBootstrapRole(meetingJoinBootstrapRef.current || "");
        if (participantRole === "host") {
          setAuthChecked(true);
          return;
        }
      }

      // Auth check — determine if this profile requires authentication

      fetch(
        `${effectiveBackend}/auth-check?profile=${encodeURIComponent(effectiveProfile)}&return_url=${encodeURIComponent(currentUrl)}`,
        { headers: authHeaders, credentials: "include" },
      )
        .then((res) => res.json())
        .then((data) => {
          if (data.auth_required && !data.authenticated) {
            // Not authenticated — redirect to auth flow
            if (data.auth_url) {
              window.location.href = data.auth_url.startsWith("http")
                ? data.auth_url
                : `${effectiveBackend}${data.auth_url}`;
              return;
            }
            if (data.error) {
              setAuthError(data.error);
            }
          }
          if (data.authenticated) {
            setAuthUser(data.user_name || "User");
            setAuthError(null);
          }
          setAuthChecked(true);
        })
        .catch(() => {
          // Backend unreachable — proceed without auth (graceful degradation)
          setAuthChecked(true);
        });
    }
  }, []);

  const {
    isConnected,
    isMuted,
    micState,
    messageList,
    currentInProgressMessage,
    isAgentSpeaking: _isAgentSpeaking,
    localAudioTrack,
    remoteVideoTrack: avatarVideoTrack,
    joinChannel,
    leaveChannel,
    toggleMute,
    sendMessage,
    agentUid,
    rtcClientRef,
    rtmClientRef,
    rtmSource,
    getMeetingTranscriptArtifact,
  } = useAgoraVideoClient();

  const stopMeetingPreview = useCallback(() => {
    if (previewAnimationFrameRef.current !== null) {
      window.cancelAnimationFrame(previewAnimationFrameRef.current);
      previewAnimationFrameRef.current = null;
    }
    if (previewAudioContextRef.current) {
      void previewAudioContextRef.current.close();
      previewAudioContextRef.current = null;
    }
    previewAnalyserRef.current = null;
    setMeetingMicLevel(0);
    if (previewVideoRef.current) {
      previewVideoRef.current.srcObject = null;
    }
    if (previewStreamRef.current) {
      previewStreamRef.current.getTracks().forEach((track) => track.stop());
      previewStreamRef.current = null;
    }
    setMeetingPreviewStream(null);
  }, []);

  // Handle mic selection change: persist to cookie and live-switch if connected
  const handleMicChange = async (deviceId: string) => {
    setSelectedMic(deviceId);
    if (deviceId) {
      persistDeviceChoice("mindfix_selected_mic", deviceId);
      localStorage.setItem("selectedMicId", deviceId);
    } else {
      clearPersistedDeviceChoice("mindfix_selected_mic");
      localStorage.removeItem("selectedMicId");
    }
    if (isConnected && localAudioTrack && deviceId) {
      try {
        await localAudioTrack.setDevice(deviceId);
      } catch (err) {
        console.error("Failed to switch microphone:", err);
      }
    }
  };

  const handleCameraChange = async (deviceId: string) => {
    setSelectedCamera(deviceId);
    if (deviceId) {
      persistDeviceChoice("mindfix_selected_camera", deviceId);
      localStorage.setItem("selectedCameraId", deviceId);
    } else {
      clearPersistedDeviceChoice("mindfix_selected_camera");
      localStorage.removeItem("selectedCameraId");
    }
  };

  useEffect(() => {
    if (
      typeof navigator === "undefined" ||
      !meetingMode ||
      isConnected ||
      !navigator.mediaDevices?.enumerateDevices
    ) {
      stopMeetingPreview();
      return;
    }

    let cancelled = false;

    const selectAvailableDevice = (
      preferredId: string,
      devices: MediaDeviceInfo[],
    ): string => {
      if (preferredId && devices.some((device) => device.deviceId === preferredId)) {
        return preferredId;
      }
      return devices[0]?.deviceId || "";
    };

    const loadDevicesAndPreview = async () => {
      try {
        const permissionStream = await navigator.mediaDevices.getUserMedia({
          audio: true,
          video: enableLocalVideo,
        });

        if (cancelled) {
          permissionStream.getTracks().forEach((track) => track.stop());
          return;
        }

        const devices = await navigator.mediaDevices.enumerateDevices();
        if (cancelled) {
          permissionStream.getTracks().forEach((track) => track.stop());
          return;
        }

        const microphones = devices.filter((device) => device.kind === "audioinput");
        const cameras = devices.filter((device) => device.kind === "videoinput");
        setAvailableMics(microphones);
        setAvailableCameras(cameras);

        const nextMic = selectAvailableDevice(selectedMic, microphones);
        const nextCamera = selectAvailableDevice(selectedCamera, cameras);

        if (nextMic !== selectedMic) {
          setSelectedMic(nextMic);
        }
        if (nextMic) {
          persistDeviceChoice("mindfix_selected_mic", nextMic);
          localStorage.setItem("selectedMicId", nextMic);
        } else {
          clearPersistedDeviceChoice("mindfix_selected_mic");
        }

        if (nextCamera !== selectedCamera) {
          setSelectedCamera(nextCamera);
        }
        if (nextCamera) {
          persistDeviceChoice("mindfix_selected_camera", nextCamera);
          localStorage.setItem("selectedCameraId", nextCamera);
        } else {
          clearPersistedDeviceChoice("mindfix_selected_camera");
        }

        permissionStream.getTracks().forEach((track) => track.stop());

        stopMeetingPreview();
        const previewStream = await navigator.mediaDevices.getUserMedia({
          audio: nextMic ? { deviceId: { exact: nextMic } } : true,
          video: enableLocalVideo
            ? (nextCamera ? { deviceId: { exact: nextCamera } } : true)
            : false,
        });
        if (cancelled) {
          previewStream.getTracks().forEach((track) => track.stop());
          return;
        }
        previewStreamRef.current = previewStream;
        setMeetingPreviewStream(previewStream);
        if (previewVideoRef.current && enableLocalVideo) {
          previewVideoRef.current.srcObject = previewStream;
        }
      } catch (error) {
        console.error("Meeting preview setup failed:", error);
        stopMeetingPreview();
        try {
          const devices = await navigator.mediaDevices.enumerateDevices();
          if (!cancelled) {
            setAvailableMics(devices.filter((device) => device.kind === "audioinput"));
            setAvailableCameras(devices.filter((device) => device.kind === "videoinput"));
          }
        } catch {
          // Ignore follow-up device enumeration errors.
        }
      }
    };

    loadDevicesAndPreview();

    return () => {
      cancelled = true;
      stopMeetingPreview();
    };
  }, [
    enableLocalVideo,
    isConnected,
    meetingMode,
    selectedCamera,
    selectedMic,
    stopMeetingPreview,
  ]);

  useEffect(() => {
    if (!meetingPreviewStream) {
      return;
    }

    const audioTracks = meetingPreviewStream.getAudioTracks();
    if (audioTracks.length === 0) {
      setMeetingMicLevel(0);
      return;
    }

    const AudioContextImpl =
      window.AudioContext ||
      (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextImpl) {
      return;
    }

    const audioContext = new AudioContextImpl();
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 256;
    const source = audioContext.createMediaStreamSource(meetingPreviewStream);
    source.connect(analyser);
    previewAudioContextRef.current = audioContext;
    previewAnalyserRef.current = analyser;
    const buffer = new Uint8Array(analyser.frequencyBinCount);

    const updateMeter = () => {
      analyser.getByteFrequencyData(buffer);
      const average =
        buffer.reduce((sum, value) => sum + value, 0) / Math.max(buffer.length, 1);
      setMeetingMicLevel(Math.min(1, average / 96));
      previewAnimationFrameRef.current = window.requestAnimationFrame(updateMeter);
    };

    updateMeter();

    return () => {
      if (previewAnimationFrameRef.current !== null) {
        window.cancelAnimationFrame(previewAnimationFrameRef.current);
        previewAnimationFrameRef.current = null;
      }
      source.disconnect();
      if (previewAudioContextRef.current === audioContext) {
        void audioContext.close();
        previewAudioContextRef.current = null;
      }
      previewAnalyserRef.current = null;
      setMeetingMicLevel(0);
    };
  }, [meetingPreviewStream]);

  // Get audio visualization data (restart on mute/unmute to fix Web Audio API connection)
  const frequencyData = useAudioVisualization(
    localAudioTrack,
    isConnected && !isMuted,
  );

  // Thymia voice biomarker data (opt-in via NEXT_PUBLIC_ENABLE_THYMIA)
  const {
    biomarkers,
    wellness,
    clinical,
    progress: thymiaProgress,
    safety: thymiaSafety,
  } = useMindfixThymia(
    rtmSource,
    isConnected && meetingAudioBiomarkersEnabled,
  );

  useEffect(() => {
    if (!isConnected || !meetingAudioBiomarkersEnabled) return;
    const now = Date.now();
    if (now - lastBiomarkerLogRef.current < 3000) return;

    const biomarkerEntries = Object.entries(biomarkers).filter(
      ([, value]) => typeof value === "number",
    );
    const progressEntries = Object.entries(thymiaProgress);
    const safetyLevel =
      typeof thymiaSafety.level === "number"
        ? thymiaSafety.level
        : typeof thymiaSafety.highest_level === "number"
          ? thymiaSafety.highest_level
          : null;

    if (
      biomarkerEntries.length === 0 &&
      progressEntries.length === 0 &&
      safetyLevel === null
    ) {
      return;
    }

    lastBiomarkerLogRef.current = now;
    const topScores = biomarkerEntries
      .sort((a, b) => (Number(b[1]) || 0) - (Number(a[1]) || 0))
      .slice(0, 4)
      .map(([key, value]) => `${key}=${Number(value).toFixed(2)}`);
    const progressSummary = progressEntries
      .map(
        ([key, value]) =>
          `${key}:${value.speech_seconds.toFixed(1)}/${value.trigger_seconds}s${value.processing ? "*" : ""}`,
      )
      .join(" ");

    console.log(
      "[Biomarkers][Client]",
      JSON.stringify({
        topScores,
        progress: progressSummary,
        safetyLevel,
      }),
    );
  }, [
    biomarkers,
    isConnected,
    meetingAudioBiomarkersEnabled,
    thymiaProgress,
    thymiaSafety,
  ]);

  // Shen.AI camera vitals (opt-in via NEXT_PUBLIC_ENABLE_SHEN)
  // RTM publish function for Shen to push vitals to server
  const shenRtmPublish = useMemo(() => {
    if (!SHEN_API_KEY || !meetingVideoBiomarkersEnabled) return null;
    const rtm = rtmClientRef.current;
    if (!rtm) return null;
    return async (message: string): Promise<boolean> => {
      try {
        const ch = channelRef.current;
        if (!ch) return false;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        await (rtm as any).publish?.(ch, message);
        return true;
      } catch {
        return false;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rtmClientRef.current]);

  const shenState = useShenai(
    Boolean(SHEN_API_KEY) && isConnected && meetingVideoBiomarkersEnabled,
    SHEN_API_KEY,
    shenRtmPublish,
    "shen-canvas",
  );

  // Move the shen canvas between desktop/mobile containers based on screen size
  useEffect(() => {
    if (
      !SHEN_API_KEY ||
      !isConnected ||
      !meetingVideoBiomarkersEnabled
    ) return;

    // Create the canvas once
    let canvas = document.getElementById("shen-canvas") as HTMLCanvasElement;
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.id = "shen-canvas";
      canvas.className = "absolute top-1/2 left-1/2 h-full";
      canvas.style.transform = "translate(-50%, -50%) scale(1.8)";
    }

    const moveCanvas = () => {
      const isMobile = window.matchMedia("(max-width: 767px)").matches;
      const containerId = isMobile
        ? "shen-container-mobile"
        : "shen-container-desktop";
      const container = document.getElementById(containerId);
      if (container && canvas.parentElement !== container) {
        container.appendChild(canvas);
      }
    };

    moveCanvas();
    const mql = window.matchMedia("(max-width: 767px)");
    mql.addEventListener("change", moveCanvas);
    return () => mql.removeEventListener("change", moveCanvas);
  }, [isConnected]);

  // Local video state - managed directly via AgoraRTC
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [localVideoTrack, setLocalVideoTrack] = useState<any>(null);
  const [isLocalVideoActive, setIsLocalVideoActive] = useState(false);

  const handleStart = async () => {
    if (authError) {
      alert(authError);
      return;
    }
    setMeetingInitError(null);
    setIsLoading(true);
    try {
      if (meetingMode) {
        if (!meetingJoinBootstrapRef.current && !meetingAccessTokenRef.current) {
          throw new Error("This meeting link is missing or expired.");
        }
        stopMeetingPreview();
        const joinResponse = await fetchWithAuth(`${backendUrl}/join-meeting`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            profile: profile.trim() || DEFAULT_PROFILE,
            access_token: meetingAccessTokenRef.current || undefined,
            join_bootstrap: meetingJoinBootstrapRef.current || undefined,
          }),
        });
        if (!joinResponse.ok) {
          const payload = await joinResponse.json().catch(() => ({}));
          throw new Error(payload.error || `Meeting join failed: ${joinResponse.statusText}`);
        }
        const meetingData = await joinResponse.json();
        channelRef.current = meetingData.channel;
        setMeetingTranscriptionEnabled(Boolean(meetingData.transcription_enabled));
        setMeetingAudioBiomarkersEnabled(Boolean(meetingData.audio_biomarkers_enabled ?? true));
        setMeetingVideoBiomarkersEnabled(Boolean(meetingData.video_biomarkers_enabled ?? true));
        await joinChannel({
          appId: meetingData.appid,
          channel: meetingData.channel,
          token: meetingData.token || null,
          uid: parseInt(String(meetingData.uid), 10),
          participantRole: meetingData.participant_role || "guest",
          rtmUid: meetingData.user_rtm_uid || meetingData.rtm_uid,
          mode: "meeting",
          transcriptionEnabled: Boolean(meetingData.transcription_enabled),
          ...(selectedMic ? { microphoneId: selectedMic } : {}),
        });
        setMeetingParticipantRole(meetingData.participant_role || "guest");
        if (enableLocalVideo && rtcClientRef.current) {
          const videoTrack = await AgoraRTC.createCameraVideoTrack({
            encoderConfig: "720p_2",
            ...(selectedCamera ? { cameraId: selectedCamera } : {}),
          });
          await rtcClientRef.current.publish(videoTrack);
          setLocalVideoTrack(videoTrack);
          setIsLocalVideoActive(true);
        }
        await fetchWithAuth(`${backendUrl}/meeting-participant-event`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            profile: profile.trim() || DEFAULT_PROFILE,
            event: "joined",
            access_token: meetingAccessTokenRef.current || undefined,
            join_bootstrap: meetingJoinBootstrapRef.current || undefined,
          }),
        }).catch((e) => {
          console.error("Meeting join notify failed:", e);
        });
        return;
      }

      // Build query params for backend
      const params = new URLSearchParams();

      // Add profile override if provided, otherwise use default "VIDEO" profile
      if (profile.trim()) {
        params.append("profile", profile.trim());
      } else {
        params.append("profile", DEFAULT_PROFILE);
      }

      // Add agent settings
      params.append("enable_aivad", enableAivad.toString());
      params.append("asr_language", language);

      // Add prompt and greeting if provided
      if (prompt.trim()) {
        params.append("prompt", prompt.trim());
      }
      if (greeting.trim()) {
        params.append("greeting", greeting.trim());
      }

      // Phase 1: Get tokens only (don't start agent yet)
      params.append("connect", "false");
      const tokenUrl = `${backendUrl}/start-agent?${params.toString()}`;
      const tokenResponse = await fetchWithAuth(tokenUrl);

      if (!tokenResponse.ok) {
        throw new Error(`Backend error: ${tokenResponse.statusText}`);
      }

      const data = await tokenResponse.json();
      setMeetingTranscriptionEnabled(Boolean(data.transcription_enabled));
      setMeetingAudioBiomarkersEnabled(Boolean(data.audio_biomarkers_enabled ?? true));
      setMeetingVideoBiomarkersEnabled(Boolean(data.video_biomarkers_enabled ?? true));

      // Phase 2: Join channel first so RTM is ready for greeting
      channelRef.current = data.channel;
      await joinChannel({
        appId: data.appid,
        channel: data.channel,
        token: data.token || null,
        uid: parseInt(data.uid),
        participantRole: "guest",
        rtmUid: data.user_rtm_uid, // Channel-scoped RTM UID for multi-session support
        agentUid: data.agent?.uid ? String(data.agent.uid) : undefined,
        agentRtmUid: data.agent_rtm_uid,
        mode: "avatar",
        ...(selectedMic ? { microphoneId: selectedMic } : {}),
      });

      // Auto-enable local video if checkbox was checked
      if (enableLocalVideo && rtcClientRef.current) {
        const videoTrack = await AgoraRTC.createCameraVideoTrack({
          encoderConfig: "720p_2",
        });
        await rtcClientRef.current.publish(videoTrack);
        setLocalVideoTrack(videoTrack);
        setIsLocalVideoActive(true);
      }

      // Phase 3: Now start the agent (client is listening for greeting)
      params.delete("connect");
      params.append("channel", data.channel);
      params.append("debug", "true");
      const agentUrl = `${backendUrl}/start-agent?${params.toString()}`;
      const agentResponse = await fetchWithAuth(agentUrl);

      if (!agentResponse.ok) {
        throw new Error(`Agent start error: ${agentResponse.statusText}`);
      }

      const agentData = await agentResponse.json();

      // Store agent_id from the actual agent response
      if (agentData.agent_response?.response) {
        try {
          const resp =
            typeof agentData.agent_response.response === "string"
              ? JSON.parse(agentData.agent_response.response)
              : agentData.agent_response.response;
          if (resp.agent_id) {
            setAgentId(resp.agent_id);
            setSessionAgentId(resp.agent_id);
          }
        } catch {
          // ignore parse errors
        }
      }

      // Store redacted payload for session panel
      if (agentData.debug?.agent_payload) {
        setSessionPayload(redactSensitiveFields(agentData.debug.agent_payload));
      }
    } catch (error) {
      console.error("Failed to start:", error);
      const message = error instanceof Error ? error.message : "Unknown error";
      if (meetingMode) {
        setMeetingInitError(message);
      } else {
        alert(`Failed to start: ${message}`);
      }
    } finally {
      setIsLoading(false);
    }
  };

  // Auto-connect after state is committed AND auth check is complete
  useEffect(() => {
    if (autoConnect && authChecked && !authError) {
      setAutoConnect(false);
      handleStart();
    }
  }, [autoConnect, authChecked, authError]);

  const handleStop = async () => {
    const isHostMeetingParticipant =
      meetingMode && meetingParticipantRole === "host";

    // Stop and close local video track to release camera hardware
    if (localVideoTrack) {
      localVideoTrack.stop();
      localVideoTrack.close();
      setLocalVideoTrack(null);
      setIsLocalVideoActive(false);
    }

    if (meetingMode) {
      await fetchWithAuth(`${backendUrl}/meeting-participant-event`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          profile: profile.trim() || DEFAULT_PROFILE,
          event: "left",
          access_token: meetingAccessTokenRef.current || undefined,
          join_bootstrap: meetingJoinBootstrapRef.current || undefined,
        }),
      }).catch((e) => {
        console.error("Meeting leave notify failed:", e);
      });
      if (meetingJoinBootstrapRef.current && channelRef.current) {
        try {
          const transcript = getMeetingTranscriptArtifact();
          await fetchWithAuth(`${backendUrl}/end-meeting`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              profile: profile.trim() || DEFAULT_PROFILE,
              join_bootstrap: meetingJoinBootstrapRef.current,
              channel: channelRef.current,
              transcript: transcript || undefined,
            }),
          });
        } catch (e) {
          console.error("End meeting failed:", e);
        }
      }
    } else if (sessionAgentId) {
      const params = new URLSearchParams({ agent_id: sessionAgentId });
      if (channelRef.current) params.append("channel", channelRef.current);
      if (profile) params.append("profile", profile);
      try {
        await fetchWithAuth(`${backendUrl}/hangup-agent?${params.toString()}`);
      } catch (e) {
        console.error("Hangup failed:", e);
      }
    }

    await leaveChannel();
    setSessionAgentId(null);
    setSessionPayload(null);
    if (meetingMode) {
      if (isHostMeetingParticipant) {
        setSessionValue(MEETING_BOOTSTRAP_STORAGE_KEY, "");
        setSessionValue(MEETING_ACCESS_TOKEN_STORAGE_KEY, "");
        setMeetingParticipantRole(null);
      }
      setMeetingTranscriptionEnabled(false);
      setMeetingInitError(null);
      setMeetingJoinReady(
        Boolean(
          meetingAccessTokenRef.current || meetingJoinBootstrapRef.current,
        ),
      );
    }
    if (isHostMeetingParticipant) {
      window.location.href = returnUrl || fallbackConsultantDashboardUrl();
      return;
    }
    if (returnUrl) {
      window.location.href = returnUrl;
      return;
    }
  };

  const handleSendMessage = async () => {
    if (!chatMessage.trim() || !isConnected) return;

    const success = await sendMessage(chatMessage);

    if (success) {
      setChatMessage("");
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const toggleVideo = async () => {
    if (isLocalVideoActive && localVideoTrack) {
      // Turn off: unpublish, stop, and close to release camera hardware
      if (rtcClientRef.current) {
        await rtcClientRef.current.unpublish(localVideoTrack);
      }
      localVideoTrack.stop();
      localVideoTrack.close();
      setLocalVideoTrack(null);
      setIsLocalVideoActive(false);
    } else if (!isLocalVideoActive && rtcClientRef.current) {
      // Turn on: create new track and publish
      const videoTrack = await AgoraRTC.createCameraVideoTrack({
        encoderConfig: "720p_2",
        ...(selectedCamera ? { cameraId: selectedCamera } : {}),
      });
      await rtcClientRef.current.publish(videoTrack);
      setLocalVideoTrack(videoTrack);
      setIsLocalVideoActive(true);
    }
  };

  // Helper to determine if message is from agent
  // Agent messages have uid matching the agent's RTC UID (provided by backend)
  const isAgentMessage = (uid: string) => {
    return agentUid ? uid === agentUid : false;
  };

  const getMeetingMessageLabel = (msg: (typeof messageList)[number] | typeof currentInProgressMessage) => {
    if (!msg) return "Message";
    const role = (msg as { role?: "host" | "guest" }).role;
    const baseLabel =
      role === "host"
        ? "Consultant"
        : role === "guest"
          ? "Client"
          : meetingParticipantRole === "host"
            ? "Client"
            : "Consultant";
    if ((msg as { transcriptSource?: boolean }).transcriptSource) {
      return `${baseLabel} transcript`;
    }
    return baseLabel;
  };

  const isOwnMeetingMessage = (
    msg: (typeof messageList)[number] | typeof currentInProgressMessage,
  ) => {
    if (!meetingMode || !msg) return false;
    const role = (msg as { role?: "host" | "guest" }).role;
    return Boolean(role && meetingParticipantRole && role === meetingParticipantRole);
  };

  const formatTime = (ts?: number) => {
    if (!ts) return "";
    const d = new Date(ts);
    return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
  };

  const showThymiaPanel = meetingAudioBiomarkersEnabled;
  const showShenPanel =
    Boolean(SHEN_API_KEY) && meetingVideoBiomarkersEnabled;
  const showBiomarkersPanel =
    meetingAudioBiomarkersEnabled || meetingVideoBiomarkersEnabled;

  // Don't render UI until auth check completes
  if (!authChecked) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <p className="text-lg text-muted-foreground animate-pulse">Loading...</p>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col bg-background overflow-hidden">
      {/* Header */}
      <header className="flex-shrink-0 border-b border-white/8 bg-[#1b2838]/82 px-4 py-3 shadow-[0_14px_40px_rgba(0,0,0,0.18)] backdrop-blur md:px-6 md:py-4">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[#2bb58e]/18 text-[#2bb58e] ring-1 ring-[#2bb58e]/28">
                <Brain className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <p className="text-[0.7rem] font-semibold uppercase tracking-[0.26em] text-[#8fb2c9]">
                  MindFix
                </p>
                <h1 className="truncate font-[family-name:var(--font-geist-sans)] text-lg font-semibold tracking-[-0.03em] text-white md:text-2xl">
                  {meetingMode ? "Secure Meeting Room" : "MindFix Session"}
                </h1>
              </div>
            </div>
            <p className="mt-2 max-w-2xl text-xs text-[#b5c7d4] md:pl-14 md:text-sm">
              AI mental wellness, guided by a human therapist.
              {meetingMode
                ? ` ${meetingTranscriptionEnabled ? "Transcription on." : "Transcription off."}`
                : " Private, browser-based support with live biomarker insight."}
              {authUser && <span className="ml-2 text-[#d7e6ee]">Signed in as {authUser}.</span>}
            </p>
            {authError && (
              <p className="mt-1 text-xs text-red-300 md:pl-14 md:text-sm">
                {authError}
              </p>
            )}
          </div>
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <button
              onClick={() => setIsSettingsOpen(!isSettingsOpen)}
              className="cursor-pointer rounded-full p-2 hover:bg-accent transition-colors"
              aria-label="Toggle settings"
            >
              <Settings className="h-5 w-5" />
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex flex-1 px-4 py-1 md:py-6 min-h-0 overflow-hidden min-w-0">
        {!isConnected ? (
          /* Connection Form / Meeting Prejoin */
          <div className="flex flex-1 items-center justify-center">
            {autoConnect || isLoading ? (
              <p className="text-lg text-muted-foreground animate-pulse">
                Connecting...
              </p>
            ) : (
              meetingMode ? (
                <div className="grid w-full max-w-5xl gap-6 lg:grid-cols-[1.15fr_0.85fr]">
                  <section className="overflow-hidden rounded-[1.75rem] border border-white/10 bg-[rgba(20,34,47,0.82)] shadow-[0_24px_80px_rgba(0,0,0,0.28)] backdrop-blur">
                    <div className="border-b border-white/8 px-6 py-5">
                      <p className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-[#8fb2c9]">
                        MindFix
                      </p>
                      <h2 className="mt-2 font-[family-name:var(--font-geist-sans)] text-2xl font-semibold tracking-[-0.03em] text-white">
                        Enter your meeting room
                      </h2>
                      <p className="mt-2 text-sm text-[#b5c7d4]">
                        Check your camera and microphone before you join.
                      </p>
                    </div>
                    <div className="p-6">
                      <div className="relative overflow-hidden rounded-[1.5rem] border border-white/10 bg-[#08131d]">
                        {enableLocalVideo ? (
                          <video
                            ref={previewVideoRef}
                            autoPlay
                            muted
                            playsInline
                            className="aspect-video w-full object-cover"
                          />
                        ) : (
                          <div className="flex aspect-video items-center justify-center text-sm text-[#8fb2c9]">
                            Camera preview is off
                          </div>
                        )}
                      </div>
                    </div>
                  </section>

                  <section className="rounded-[1.75rem] border border-white/10 bg-[rgba(20,34,47,0.82)] p-6 shadow-[0_24px_80px_rgba(0,0,0,0.28)] backdrop-blur">
                    <div className="space-y-5">
                      {meetingInitError && (
                        <div className="rounded-2xl border border-red-400/25 bg-red-500/10 px-4 py-3 text-sm text-red-200">
                          Failed to start: {meetingInitError}
                        </div>
                      )}

                      <div>
                        <label
                          htmlFor="meeting-camera"
                          className="mb-2 block text-sm font-medium text-slate-100"
                        >
                          Camera
                        </label>
                        <select
                          id="meeting-camera"
                          value={selectedCamera}
                          onChange={(e) => void handleCameraChange(e.target.value)}
                          disabled={!enableLocalVideo || availableCameras.length === 0}
                          className="w-full rounded-xl border border-white/12 bg-white/6 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-60"
                        >
                          {availableCameras.length === 0 ? (
                            <option value="">No camera detected</option>
                          ) : (
                            availableCameras.map((device, index) => (
                              <option key={device.deviceId || index} value={device.deviceId}>
                                {device.label || `Camera ${index + 1}`}
                              </option>
                            ))
                          )}
                        </select>
                      </div>

                      <div>
                        <label
                          htmlFor="meeting-mic"
                          className="mb-2 block text-sm font-medium text-slate-100"
                        >
                          Microphone
                        </label>
                        <select
                          id="meeting-mic"
                          value={selectedMic}
                          onChange={(e) => void handleMicChange(e.target.value)}
                          disabled={availableMics.length === 0}
                          className="w-full rounded-xl border border-white/12 bg-white/6 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-60"
                        >
                          {availableMics.length === 0 ? (
                            <option value="">No microphone detected</option>
                          ) : (
                            availableMics.map((device, index) => (
                              <option key={device.deviceId || index} value={device.deviceId}>
                                {device.label || `Microphone ${index + 1}`}
                              </option>
                            ))
                          )}
                        </select>
                      </div>

                      <div>
                        <p className="mb-2 block text-sm font-medium text-slate-100">
                          Microphone level
                        </p>
                        <div className="rounded-2xl border border-white/8 bg-white/4 px-4 py-3">
                          <div className="flex items-center gap-2">
                            <Mic className="h-4 w-4 text-[#8fb2c9]" />
                            <div className="flex flex-1 gap-1">
                              {Array.from({ length: 12 }).map((_, index) => {
                                const threshold = (index + 1) / 12;
                                const active = meetingMicLevel >= threshold;
                                return (
                                  <span
                                    key={index}
                                    className={cn(
                                      "h-7 flex-1 rounded-full transition-colors",
                                      active ? "bg-[#2bb58e]" : "bg-white/10",
                                    )}
                                  />
                                );
                              })}
                            </div>
                          </div>
                        </div>
                      </div>

                      <label className="flex items-center gap-3 cursor-pointer rounded-2xl border border-white/8 bg-white/4 px-4 py-3">
                        <input
                          type="checkbox"
                          checked={enableLocalVideo}
                          onChange={(e) => setEnableLocalVideo(e.target.checked)}
                          className="h-4 w-4 rounded border-gray-300"
                        />
                        <span className="text-sm font-medium text-white">
                          Start with camera on
                        </span>
                      </label>

                      <button
                        onClick={handleStart}
                        disabled={isLoading || !meetingJoinReady}
                        className="cursor-pointer w-full rounded-xl bg-primary px-4 py-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                      >
                        {isLoading ? "Connecting..." : "Join Meeting"}
                      </button>
                    </div>
                  </section>
                </div>
              ) : (
                <div className="w-full max-w-md rounded-[1.75rem] border border-white/10 bg-[rgba(20,34,47,0.82)] p-6 shadow-[0_24px_80px_rgba(0,0,0,0.28)] backdrop-blur">
                  <p className="mb-2 text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-[#8fb2c9]">
                    MindFix
                  </p>
                  <h2 className="mb-4 font-[family-name:var(--font-geist-sans)] text-xl font-semibold tracking-[-0.03em] text-white">
                    Start your session
                  </h2>
                  <div className="space-y-4">
                    <div>
                      <label
                        htmlFor="backend"
                        className="mb-2 block text-sm font-medium text-slate-100"
                      >
                        Backend URL
                      </label>
                      <input
                        id="backend"
                        type="text"
                        value={backendUrl}
                        onChange={(e) => setBackendUrl(e.target.value)}
                        placeholder={DEFAULT_BACKEND_URL}
                        className="w-full rounded-xl border border-white/12 bg-white/6 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-ring"
                      />
                    </div>

                    <div>
                      <label
                        htmlFor="profile"
                        className="mb-2 block text-sm font-medium text-slate-100"
                      >
                        Server Profile
                      </label>
                      <input
                        id="profile"
                        type="text"
                        value={profile}
                        onChange={(e) => setProfile(e.target.value)}
                        placeholder={DEFAULT_PROFILE}
                        className="w-full rounded-xl border border-white/12 bg-white/6 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-ring"
                      />
                      <p className="mt-1 text-xs text-muted-foreground">
                        Leave empty for default &ldquo;{DEFAULT_PROFILE}&rdquo;
                        profile
                      </p>
                    </div>

                    <div className="space-y-2">
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={enableLocalVideo}
                          onChange={(e) => setEnableLocalVideo(e.target.checked)}
                          className="h-4 w-4 rounded border-gray-300"
                        />
                        <span className="text-sm font-medium">
                          Enable Local Video
                        </span>
                      </label>

                      <label className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={enableAvatar}
                          onChange={(e) => setEnableAvatar(e.target.checked)}
                          className="h-4 w-4 rounded border-gray-300"
                        />
                        <span className="text-sm font-medium">Enable Avatar</span>
                      </label>
                    </div>

                    <button
                      onClick={handleStart}
                      disabled={isLoading}
                      className="cursor-pointer w-full rounded-lg bg-primary px-4 py-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                    >
                      {isLoading ? "Connecting..." : "Start Call"}
                    </button>
                  </div>
                </div>
              )
            )}
          </div>
        ) : (
          /* Connected: Responsive Layout */
          <>
            {/* Desktop Layout - Hidden on mobile */}
            <VideoGrid
              className="hidden md:grid flex-1 min-w-0"
              style={{
                gridTemplateColumns: "2fr 3fr",
                gridTemplateRows: "1fr 1fr",
                gap: "1rem",
              }}
              chat={
                <div className="flex flex-col h-full">
                  {/* Conversation Header */}
                  <div className="border-b p-4 flex-shrink-0 flex items-center justify-between">
                    <h2 className="font-semibold">Conversation</h2>
                    <p className="text-sm text-muted-foreground">
                      {messageList.length} message
                      {messageList.length !== 1 ? "s" : ""}
                    </p>
                  </div>

                  {/* Messages */}
                  <Conversation
                    height=""
                    className="flex-1 min-h-0"
                    style={{ overflow: "auto" }}
                  >
                    <ConversationContent className="gap-3">
                      {messageList.map((msg, idx) => {
                        const isAgent = !meetingMode && isAgentMessage(msg.uid);
                        const ownMeetingMessage = isOwnMeetingMessage(msg);
                        const label = meetingMode
                          ? getMeetingMessageLabel(msg)
                          : isAgent
                            ? "Agent"
                            : "You";
                        const time = formatTime(msg.timestamp);
                        return (
                            <Message
                              key={`${msg.turn_id}-${msg.uid}-${idx}`}
                              from={meetingMode ? (ownMeetingMessage ? "user" : "assistant") : isAgent ? "assistant" : "user"}
                              name={time ? `${label}  ${time}` : label}
                            >
                              <MessageContent
                                className={
                                  meetingMode
                                    ? ownMeetingMessage
                                      ? "px-3 py-2 bg-foreground text-background"
                                      : "px-3 py-2"
                                    : isAgent
                                  ? "px-3 py-2"
                                  : "px-3 py-2 bg-foreground text-background"
                                }
                            >
                              <Response>{msg.text}</Response>
                            </MessageContent>
                          </Message>
                        );
                      })}

                      {/* In-progress message */}
                      {currentInProgressMessage &&
                        (() => {
                          const isAgent = !meetingMode && isAgentMessage(
                            currentInProgressMessage.uid,
                          );
                          const ownMeetingMessage =
                            isOwnMeetingMessage(currentInProgressMessage);
                          const label = meetingMode
                            ? getMeetingMessageLabel(currentInProgressMessage)
                            : isAgent
                              ? "Agent"
                              : "You";
                          const time = formatTime(
                            currentInProgressMessage.timestamp,
                          );
                          return (
                            <Message
                              from={meetingMode ? (ownMeetingMessage ? "user" : "assistant") : isAgent ? "assistant" : "user"}
                              name={time ? `${label}  ${time}` : label}
                            >
                              <MessageContent
                                className={`animate-pulse px-3 py-2 ${meetingMode ? (ownMeetingMessage ? "bg-foreground text-background" : "") : isAgent ? "" : "bg-foreground text-background"}`}
                              >
                                <Response>
                                  {currentInProgressMessage.text}
                                </Response>
                              </MessageContent>
                            </Message>
                          );
                        })()}
                    </ConversationContent>
                  </Conversation>

                  {/* Input Box */}
                  <div className="border-t p-4 flex-shrink-0">
                    <div className="flex gap-2">
                      <input
                        type="text"
                        value={chatMessage}
                        onChange={(e) => setChatMessage(e.target.value)}
                        onKeyPress={handleKeyPress}
                        placeholder="Type a message"
                        disabled={!isConnected}
                        className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                      />
                      <button
                        onClick={handleSendMessage}
                        disabled={!isConnected || !chatMessage.trim()}
                        className="cursor-pointer h-10 w-10 flex items-center justify-center rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                      >
                        <SendHorizontal className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </div>
              }
              avatar={
                <div className="flex flex-col h-full">
                  {/* Avatar Video + combined biomarkers tab */}
                  {showBiomarkersPanel ? (
                    <MobileTabs
                      tabs={[
                        {
                          id: "avatar",
                          label: meetingMode ? "Remote Video" : "Avatar",
                          content: (
                            <div className="flex-1 flex items-center justify-center bg-muted/20 p-2 h-full">
                              <AvatarVideoDisplay
                                videoTrack={avatarVideoTrack}
                                state={
                                  avatarVideoTrack
                                    ? "connected"
                                    : "disconnected"
                                }
                                className="h-full w-full"
                                useMediaStream={true}
                              />
                            </div>
                          ),
                        },
                        {
                          id: "biomarkers",
                          label: "Biomarkers",
                          content: (
                            <CombinedBiomarkersPanel
                              biomarkers={biomarkers}
                              wellness={wellness}
                              clinical={clinical}
                              progress={thymiaProgress}
                              safety={thymiaSafety}
                              shenState={shenState}
                              isConnected={isConnected}
                              voiceEnabled={meetingAudioBiomarkersEnabled}
                              videoEnabled={meetingVideoBiomarkersEnabled}
                            />
                          ),
                        },
                      ]}
                    />
                  ) : (
                    <div className="flex-1 flex items-center justify-center bg-muted/20 p-2">
                      <AvatarVideoDisplay
                        videoTrack={avatarVideoTrack}
                        state={avatarVideoTrack ? "connected" : "disconnected"}
                        className="h-full w-full"
                        useMediaStream={true}
                      />
                    </div>
                  )}

                  {/* Controls below avatar */}
                  <div className="border-t p-4 flex-shrink-0">
                    <div className="flex gap-3 justify-center">
                      <IconButton
                        shape="square"
                        variant={isMuted ? "standard" : "filled"}
                        size="md"
                        onClick={toggleMute}
                        className={
                          isMuted
                            ? "rounded-lg bg-muted text-destructive hover:bg-muted/80"
                            : "rounded-lg"
                        }
                      >
                        {isMuted ? (
                          <MicOff className="size-4" />
                        ) : (
                          <Mic className="size-4" />
                        )}
                      </IconButton>
                      <IconButton
                        shape="square"
                        variant={isLocalVideoActive ? "filled" : "standard"}
                        size="md"
                        onClick={toggleVideo}
                        className={
                          !isLocalVideoActive
                            ? "rounded-lg bg-muted text-destructive hover:bg-muted/80"
                            : "rounded-lg"
                        }
                      >
                        {isLocalVideoActive ? (
                          <Video className="size-4" />
                        ) : (
                          <VideoOff className="size-4" />
                        )}
                      </IconButton>
                      <button
                        onClick={handleStop}
                        className="cursor-pointer flex items-center gap-2 rounded-lg bg-destructive px-5 py-2.5 text-sm font-medium text-destructive-foreground hover:bg-destructive/90"
                      >
                        <PhoneOff className="h-4 w-4" />
                        End Call
                      </button>
                    </div>
                  </div>
                </div>
              }
              localVideo={
                <div className="h-full flex items-center justify-center p-2">
                  {showShenPanel ? (
                    <div
                      id="shen-container-desktop"
                      className="relative h-full w-full rounded-lg overflow-hidden bg-black"
                    />
                  ) : (
                    <LocalVideoPreview
                      videoTrack={isLocalVideoActive ? localVideoTrack : null}
                      className="h-full w-full"
                      useMediaStream={true}
                    />
                  )}
                </div>
              }
            />

            {/* Mobile Layout - Hidden on desktop */}
            <div className="flex md:hidden flex-1 flex-col min-h-0 overflow-hidden">
              <MobileTabs
                activeTab={activeTab}
                onTabChange={setActiveTab}
                tabs={[
                  {
                    id: "video",
                    label: "Video",
                    content: (
                      <div className="flex flex-col h-full gap-2 p-2">
                        {/* Avatar - 50% */}
                        <div className="flex-1 rounded-lg border bg-card shadow-lg overflow-hidden">
                          <AvatarVideoDisplay
                            videoTrack={avatarVideoTrack}
                            state={
                              avatarVideoTrack ? "connected" : "disconnected"
                            }
                            className="h-full w-full"
                            useMediaStream={true}
                          />
                        </div>

                        {/* Local Video - 50% */}
                        {showShenPanel ? (
                          <div
                            id="shen-container-mobile"
                            className="relative flex-1 rounded-lg border bg-black shadow-lg overflow-hidden"
                          />
                        ) : (
                          <div className="flex-1 rounded-lg border bg-card shadow-lg overflow-hidden">
                            <LocalVideoPreview
                              videoTrack={
                                isLocalVideoActive ? localVideoTrack : null
                              }
                              className="h-full w-full"
                              useMediaStream={true}
                            />
                          </div>
                        )}
                      </div>
                    ),
                  },
                  {
                    id: "chat",
                    label: "Chat",
                    content: (
                      <div className="flex flex-col h-full gap-2 p-2">
                        {/* Avatar - 50% (matches Video tab) */}
                        <div className="flex-[50] rounded-lg border bg-card shadow-lg overflow-hidden">
                          <AvatarVideoDisplay
                            videoTrack={avatarVideoTrack}
                            state={
                              avatarVideoTrack ? "connected" : "disconnected"
                            }
                            className="h-full w-full"
                            useMediaStream={true}
                          />
                        </div>

                        {/* Chat - 50% */}
                        <div className="flex-[50] rounded-lg border bg-card shadow-lg overflow-hidden flex flex-col">
                          {/* Messages */}
                          <Conversation
                            height=""
                            className="flex-1 min-h-0"
                            style={{ overflow: "auto" }}
                          >
                            <ConversationContent className="gap-3">
                              {messageList.map((msg, idx) => {
                        const isAgent = !meetingMode && isAgentMessage(msg.uid);
                        const ownMeetingMessage = isOwnMeetingMessage(msg);
                        const label = meetingMode
                          ? getMeetingMessageLabel(msg)
                          : isAgent
                            ? "Agent"
                            : "You";
                                const time = formatTime(msg.timestamp);
                                return (
                                  <Message
                                    key={`${msg.turn_id}-${msg.uid}-${idx}`}
                                    from={meetingMode ? (ownMeetingMessage ? "user" : "assistant") : isAgent ? "assistant" : "user"}
                                    name={time ? `${label}  ${time}` : label}
                                  >
                                    <MessageContent
                                      className={
                                        meetingMode
                                          ? ownMeetingMessage
                                            ? "px-3 py-2 bg-foreground text-background"
                                            : "px-3 py-2"
                                          : isAgent
                                          ? "px-3 py-2"
                                          : "px-3 py-2 bg-foreground text-background"
                                      }
                                    >
                                      <Response>{msg.text}</Response>
                                    </MessageContent>
                                  </Message>
                                );
                              })}

                              {/* In-progress message */}
                              {currentInProgressMessage &&
                                (() => {
                                  const isAgent = !meetingMode && isAgentMessage(
                                    currentInProgressMessage.uid,
                                  );
                                  const ownMeetingMessage =
                                    isOwnMeetingMessage(currentInProgressMessage);
                                  const label = meetingMode
                                    ? getMeetingMessageLabel(currentInProgressMessage)
                                    : isAgent
                                      ? "Agent"
                                      : "You";
                                  const time = formatTime(
                                    currentInProgressMessage.timestamp,
                                  );
                                  return (
                                    <Message
                                      from={meetingMode ? (ownMeetingMessage ? "user" : "assistant") : isAgent ? "assistant" : "user"}
                                      name={time ? `${label}  ${time}` : label}
                                    >
                                      <MessageContent
                                        className={`animate-pulse px-3 py-2 ${meetingMode ? (ownMeetingMessage ? "bg-foreground text-background" : "") : isAgent ? "" : "bg-foreground text-background"}`}
                                      >
                                        <Response>
                                          {currentInProgressMessage.text}
                                        </Response>
                                      </MessageContent>
                                    </Message>
                                  );
                                })()}
                            </ConversationContent>
                          </Conversation>

                          {/* Input Box */}
                          <div className="border-t p-2 flex-shrink-0">
                            <div className="flex gap-2">
                              <input
                                type="text"
                                value={chatMessage}
                                onChange={(e) => setChatMessage(e.target.value)}
                                onKeyPress={handleKeyPress}
                                placeholder="Type a message"
                                disabled={!isConnected}
                                className="flex-1 rounded-md border border-input bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                              />
                              <button
                                onClick={handleSendMessage}
                                disabled={!isConnected || !chatMessage.trim()}
                                className="cursor-pointer h-10 w-10 flex items-center justify-center rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                              >
                                <SendHorizontal className="h-4 w-4" />
                              </button>
                            </div>
                          </div>
                        </div>
                      </div>
                    ),
                  },
                  ...(showBiomarkersPanel
                    ? [
                        {
                          id: "biomarkers",
                          label: "Biomarkers",
                          content: (
                            <CombinedBiomarkersPanel
                              biomarkers={biomarkers}
                              wellness={wellness}
                              clinical={clinical}
                              progress={thymiaProgress}
                              safety={thymiaSafety}
                              shenState={shenState}
                              isConnected={isConnected}
                              voiceEnabled={meetingAudioBiomarkersEnabled}
                              videoEnabled={meetingVideoBiomarkersEnabled}
                            />
                          ),
                        },
                      ]
                    : []),
                ]}
              />

              {/* Mobile: Fixed Bottom Controls */}
              <div className="flex gap-3 p-2 border-t bg-card flex-shrink-0 justify-center">
                <IconButton
                  shape="square"
                  variant={isMuted ? "standard" : "filled"}
                  size="md"
                  onClick={toggleMute}
                  className={
                    isMuted
                      ? "rounded-lg bg-muted text-destructive hover:bg-muted/80"
                      : "rounded-lg"
                  }
                >
                  {isMuted ? (
                    <MicOff className="size-4" />
                  ) : (
                    <Mic className="size-4" />
                  )}
                </IconButton>
                <IconButton
                  shape="square"
                  variant={isLocalVideoActive ? "filled" : "standard"}
                  size="md"
                  onClick={toggleVideo}
                  className={
                    !isLocalVideoActive
                      ? "rounded-lg bg-muted text-destructive hover:bg-muted/80"
                      : "rounded-lg"
                  }
                >
                  {isLocalVideoActive ? (
                    <Video className="size-4" />
                  ) : (
                    <VideoOff className="size-4" />
                  )}
                </IconButton>
                <button
                  onClick={handleStop}
                  className="cursor-pointer flex items-center gap-2 rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground hover:bg-destructive/90 min-h-[44px]"
                >
                  <PhoneOff className="h-4 w-4" />
                  End Call
                </button>
              </div>
            </div>
          </>
        )}
      </main>

      {/* Settings Dialog */}
      <SettingsDialog
        open={isSettingsOpen}
        onOpenChange={setIsSettingsOpen}
        enableAivad={enableAivad}
        onEnableAivadChange={setEnableAivad}
        language={language}
        onLanguageChange={setLanguage}
        prompt={prompt}
        onPromptChange={setPrompt}
        greeting={greeting}
        onGreetingChange={setGreeting}
        disabled={isConnected}
        selectedMicId={selectedMic}
        onMicChange={handleMicChange}
      >
        {!meetingMode && <SessionPanel agentId={sessionAgentId} payload={sessionPayload} />}
      </SettingsDialog>
    </div>
  );
}

export default VideoAvatarClient;
