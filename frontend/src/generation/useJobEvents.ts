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
  const reconnectAttempt = useRef(0);
  const activeSocket = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<number | null>(null);
  const refreshAbort = useRef<AbortController | null>(null);
  const latestRevision = useRef<number | null>(null);
  const disposed = useRef(false);

  const closeSocket = useCallback(() => {
    if (activeSocket.current !== null) {
      activeSocket.current.close();
      activeSocket.current = null;
    }
  }, []);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimer.current !== null) {
      window.clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
  }, []);

  const refresh = useCallback(async () => {
    if (jobId === null) {
      setJob(null);
      setError(null);
      latestRevision.current = null;
      return;
    }
    refreshAbort.current?.abort();
    const controller = new AbortController();
    refreshAbort.current = controller;
    try {
      const loaded = await getGenerationJob(projectId, jobId, controller.signal);
      if (disposed.current || controller.signal.aborted) {
        return;
      }
      setJob((current) => {
        if (current !== null && loaded.state.revision < current.state.revision) {
          return current;
        }
        latestRevision.current = loaded.state.revision;
        return loaded;
      });
      setError(null);
    } catch (cause) {
      if (disposed.current || controller.signal.aborted) {
        return;
      }
      setError(toApiError(cause));
    }
  }, [jobId, projectId]);

  useEffect(() => {
    disposed.current = false;
    if (jobId === null) {
      closeSocket();
      clearReconnectTimer();
      refreshAbort.current?.abort();
      refreshAbort.current = null;
      latestRevision.current = null;
      setConnected(false);
      setJob(null);
      setError(null);
      return () => {
        disposed.current = true;
      };
    }

    const scheduleReconnect = (withRefresh: boolean) => {
      if (disposed.current) {
        return;
      }
      setConnected(false);
      closeSocket();
      clearReconnectTimer();
      if (withRefresh) {
        void refresh();
      }
      const delay = Math.min(
        INITIAL_RECONNECT_DELAY_MS * 2 ** reconnectAttempt.current,
        MAX_RECONNECT_DELAY_MS,
      );
      reconnectAttempt.current += 1;
      reconnectTimer.current = window.setTimeout(() => {
        reconnectTimer.current = null;
        connect();
      }, delay);
    };

    const handleSnapshot = (message: Extract<GenerationEventMessage, { type: "snapshot" }>) => {
      setJob((current) => {
        if (current !== null && message.revision <= current.state.revision) {
          latestRevision.current = current.state.revision;
          return current;
        }
        latestRevision.current = message.revision;
        return message.job;
      });
      setError(null);
      if (
        latestRevision.current !== null &&
        message.revision > latestRevision.current + 1
      ) {
        void refresh();
      }
      reconnectAttempt.current = 0;
    };

    const connect = () => {
      if (disposed.current || jobId === null) {
        return;
      }
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(
        `${protocol}//${window.location.host}/api/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(jobId)}/events`,
      );
      activeSocket.current = socket;
      socket.onopen = () => {
        if (!disposed.current) {
          setConnected(true);
        }
      };
      socket.onmessage = (event) => {
        if (disposed.current) {
          return;
        }
        try {
          const parsed = parseGenerationEventMessage(
            JSON.parse(String(event.data)),
            projectId,
            jobId,
          );
          if (parsed.type === "snapshot") {
            handleSnapshot(parsed);
          }
        } catch (cause) {
          setError(toApiError(cause));
          scheduleReconnect(true);
        }
      };
      socket.onclose = () => {
        if (!disposed.current) {
          scheduleReconnect(true);
        }
      };
      socket.onerror = () => {
        if (!disposed.current) {
          setConnected(false);
        }
      };
    };

    connect();
    return () => {
      disposed.current = true;
      refreshAbort.current?.abort();
      refreshAbort.current = null;
      clearReconnectTimer();
      closeSocket();
      setConnected(false);
    };
  }, [clearReconnectTimer, closeSocket, jobId, projectId, refresh]);

  return { job, connected, error, refresh };
}

export default useJobEvents;
