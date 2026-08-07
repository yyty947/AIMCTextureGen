import { useCallback, useEffect, useRef, useState } from "react";

import type { ApiError } from "../api";
import {
  getGenerationJob,
  parseGenerationEventMessage,
  toApiError,
  type GenerationEventMessage,
} from "./api";
import type { GenerationJobDetail } from "./types";

const INITIAL_RECONNECT_DELAY_MS = 250;
const MAX_RECONNECT_DELAY_MS = 2_000;

export function useJobEvents(
  projectId: string,
  jobId: string | null,
): {
  readonly job: GenerationJobDetail | null;
  readonly connected: boolean;
  readonly error: ApiError | null;
  readonly refresh: () => Promise<void>;
} {
  const [job, setJob] = useState<GenerationJobDetail | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const generation = useRef(0);
  const refreshCurrent = useRef<() => Promise<void>>(async () => undefined);

  const refresh = useCallback(() => refreshCurrent.current(), []);

  useEffect(() => {
    const currentGeneration = ++generation.current;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let refreshController: AbortController | null = null;
    let reconnectAttempt = 0;
    let latestRevision: number | null = null;
    let reconnectScheduled = false;
    const isCurrent = () => generation.current === currentGeneration;

    const clearTimer = () => {
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };
    const closeSocket = () => {
      const current = socket;
      socket = null;
      current?.close();
    };
    const refreshDurable = async () => {
      if (!isCurrent() || jobId === null) return;
      refreshController?.abort();
      const controller = new AbortController();
      refreshController = controller;
      try {
        const loaded = await getGenerationJob(projectId, jobId, controller.signal);
        if (!isCurrent() || controller.signal.aborted) return;
        if (latestRevision !== null && loaded.state.revision < latestRevision) return;
        latestRevision = loaded.state.revision;
        setJob(loaded);
        setError(null);
      } catch (cause) {
        if (isCurrent() && !controller.signal.aborted) setError(toApiError(cause));
      }
    };
    refreshCurrent.current = refreshDurable;

    const scheduleReconnect = (refreshFirst: boolean) => {
      if (!isCurrent() || reconnectScheduled) return;
      reconnectScheduled = true;
      setConnected(false);
      closeSocket();
      clearTimer();
      const continueReconnect = () => {
        if (!isCurrent()) return;
        const delay = Math.min(
          INITIAL_RECONNECT_DELAY_MS * 2 ** reconnectAttempt,
          MAX_RECONNECT_DELAY_MS,
        );
        reconnectAttempt += 1;
        reconnectTimer = window.setTimeout(() => {
          reconnectTimer = null;
          reconnectScheduled = false;
          connect();
        }, delay);
      };
      if (refreshFirst) void refreshDurable().finally(continueReconnect);
      else continueReconnect();
    };

    const handleSnapshot = (
      message: Extract<GenerationEventMessage, { type: "snapshot" }>,
    ) => {
      if (!isCurrent()) return;
      const priorRevision = latestRevision;
      if (priorRevision !== null && message.revision <= priorRevision) return;
      if (priorRevision !== null && message.revision > priorRevision + 1) {
        scheduleReconnect(true);
        return;
      }
      latestRevision = message.revision;
      setJob(message.job);
      setError(null);
      reconnectAttempt = 0;
    };

    const connect = () => {
      if (!isCurrent() || jobId === null) return;
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      let currentSocket: WebSocket;
      try {
        currentSocket = new WebSocket(
          `${protocol}//${window.location.host}/api/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(jobId)}/events`,
        );
      } catch (cause) {
        if (isCurrent()) {
          setError(toApiError(cause));
          scheduleReconnect(true);
        }
        return;
      }
      socket = currentSocket;
      currentSocket.onopen = () => {
        if (isCurrent() && socket === currentSocket) setConnected(true);
      };
      currentSocket.onmessage = (event) => {
        if (!isCurrent() || socket !== currentSocket) return;
        try {
          const parsed = parseGenerationEventMessage(
            JSON.parse(String(event.data)),
            projectId,
            jobId,
          );
          if (parsed.type === "snapshot") handleSnapshot(parsed);
        } catch (cause) {
          if (!isCurrent()) return;
          setError(toApiError(cause));
          scheduleReconnect(true);
        }
      };
      currentSocket.onclose = () => {
        if (isCurrent() && socket === currentSocket) scheduleReconnect(true);
      };
      currentSocket.onerror = () => {
        if (isCurrent() && socket === currentSocket) scheduleReconnect(true);
      };
    };

    if (jobId === null) {
      setJob(null);
      setConnected(false);
      setError(null);
    } else {
      connect();
    }

    return () => {
      if (refreshCurrent.current === refreshDurable) {
        refreshCurrent.current = async () => undefined;
      }
      refreshController?.abort();
      clearTimer();
      closeSocket();
      setConnected(false);
    };
  }, [jobId, projectId]);

  return { job, connected, error, refresh };
}

export default useJobEvents;
