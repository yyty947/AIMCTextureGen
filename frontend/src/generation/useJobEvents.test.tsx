import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as generationApi from "./api";
import type { GenerationJobDetail } from "./types";
import { useJobEvents } from "./useJobEvents";

const projectId = "6fda5078-1246-4cac-91e8-541808da14f4";
const jobId = "0f6fb74b-5d0f-46b0-bf03-2fb41aa83694";
const createdAt = "2026-08-03T10:00:00Z";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static failConstruction = false;

  readonly url: string;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  readyState = 0;
  closed = false;

  constructor(url: string) {
    if (FakeWebSocket.failConstruction) {
      throw new Error("WebSocket unavailable");
    }
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  emitOpen() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }

  emitMessage(data: unknown) {
    this.onmessage?.(
      new MessageEvent("message", {
        data: JSON.stringify(data),
      }),
    );
  }

  emitRaw(data: string) {
    this.onmessage?.(new MessageEvent("message", { data }));
  }

  emitClose(code = 1006) {
    this.readyState = 3;
    this.closed = true;
    this.onclose?.(new CloseEvent("close", { code }));
  }

  close() {
    this.closed = true;
    this.readyState = 3;
  }
}

function makeJobDetail(
  revision: number,
  status: GenerationJobDetail["state"]["status"] = "queued",
): GenerationJobDetail {
  return {
    request: {
      schemaVersion: 3,
      jobId,
      projectId,
      parentJobId: null,
      target: {
        semanticId: "minecraft:deepslate",
        displayName: "Deepslate",
        relativePath: "assets/minecraft/textures/block/deepslate.png",
        catalogId: "java-dev-format-34",
      },
      prompt: {
        promptVersion: "java-block-prompt-v1",
        positivePrompt: "cold stone",
        negativePrompt: "none",
        userPrompt: "cold stone",
      },
      resolution: 16,
      parallelism: 2,
      executionBatches: [
        { batchIndex: 0, candidateIndices: [0, 1], seed: 101 },
        { batchIndex: 1, candidateIndices: [2, 3], seed: 202 },
      ],
      references: { style: [], structure: [] },
      advanced: { styleWeight: null, denoise: null, loraWeight: null },
      modelProfile: {
        profileId: "sdxl-mapchip-ipadapter",
        profileVersion: "2",
        profileManifestSha256: "a".repeat(64),
        runtimeId: "comfyui-windows-nvidia",
        runtimeVersion: "0.29.2",
        runtimeManifestSha256: "b".repeat(64),
        workflowVariant: "text2img-no-style",
        workflowSha256: "c".repeat(64),
        outputNodeId: "19",
      },
      createdAt,
    },
    state: {
      schemaVersion: 2,
      jobId,
      projectId,
      revision,
      status,
      cancelRequestedAt: null,
      failure: null,
      batches: [
        {
          batchIndex: 0,
          candidateIndices: [0, 1],
          seed: 101,
          status: "pending",
          promptId: null,
          rawArtifacts: [],
          startedAt: null,
          finishedAt: null,
          failure: null,
        },
        {
          batchIndex: 1,
          candidateIndices: [2, 3],
          seed: 202,
          status: "pending",
          promptId: null,
          rawArtifacts: [],
          startedAt: null,
          finishedAt: null,
          failure: null,
        },
      ],
      candidates: [
        {
          candidateIndex: 0,
          batchIndex: 0,
          positionInBatch: 0,
          batchSeed: 101,
          status: "pending",
          artifacts: { raw: null, final: null, nearest: null, tile: null, report: null },
          lineage: null,
          failure: null,
          startedAt: null,
          finishedAt: null,
        },
        {
          candidateIndex: 1,
          batchIndex: 0,
          positionInBatch: 1,
          batchSeed: 101,
          status: "pending",
          artifacts: { raw: null, final: null, nearest: null, tile: null, report: null },
          lineage: null,
          failure: null,
          startedAt: null,
          finishedAt: null,
        },
        {
          candidateIndex: 2,
          batchIndex: 1,
          positionInBatch: 0,
          batchSeed: 202,
          status: "pending",
          artifacts: { raw: null, final: null, nearest: null, tile: null, report: null },
          lineage: null,
          failure: null,
          startedAt: null,
          finishedAt: null,
        },
        {
          candidateIndex: 3,
          batchIndex: 1,
          positionInBatch: 1,
          batchSeed: 202,
          status: "pending",
          artifacts: { raw: null, final: null, nearest: null, tile: null, report: null },
          lineage: null,
          failure: null,
          startedAt: null,
          finishedAt: null,
        },
      ],
      createdAt,
      updatedAt: createdAt,
      startedAt: null,
      finishedAt: null,
    },
  };
}

describe("useJobEvents", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    FakeWebSocket.instances.length = 0;
    FakeWebSocket.failConstruction = false;
  });

  it("accepts the first snapshot, ignores heartbeats and lower revisions, and refreshes after disconnect", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
    const getJobMock = vi
      .spyOn(generationApi, "getGenerationJob")
      .mockResolvedValue(makeJobDetail(3, "generating"));
    const cancelJobMock = vi.spyOn(generationApi, "cancelGenerationJob");

    const { result, unmount } = renderHook(() => useJobEvents(projectId, jobId));

    expect(FakeWebSocket.instances).toHaveLength(1);
    const socket = FakeWebSocket.instances[0]!;
    act(() => {
      socket.emitOpen();
      socket.emitMessage({
        type: "snapshot",
        revision: 3,
        job: makeJobDetail(3, "queued"),
      });
      socket.emitMessage({ type: "heartbeat" });
      socket.emitMessage({
        type: "snapshot",
        revision: 2,
        job: makeJobDetail(2, "queued"),
      });
    });

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.job?.state.revision).toBe(3);
    act(() => socket.emitClose());
    await waitFor(() => expect(getJobMock).toHaveBeenCalled());
    expect(cancelJobMock).not.toHaveBeenCalled();

    unmount();
    expect(socket.closed).toBe(true);
  });

  it("refreshes after malformed messages and stops timers, fetches, and sockets on unmount", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
    const getJobMock = vi
      .spyOn(generationApi, "getGenerationJob")
      .mockResolvedValue(makeJobDetail(5, "failed"));

    const { unmount } = renderHook(() => useJobEvents(projectId, jobId));

    const socket = FakeWebSocket.instances[0]!;
    act(() => {
      socket.emitOpen();
      socket.emitRaw("{not valid json");
    });
    await waitFor(() => expect(getJobMock).toHaveBeenCalledTimes(1));

    unmount();
    expect(socket.closed).toBe(true);
    expect(getJobMock).toHaveBeenCalledTimes(1);
  });

  it("does not accept a revision gap and reconnects after the durable refresh", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
    const getJobMock = vi
      .spyOn(generationApi, "getGenerationJob")
      .mockResolvedValue(makeJobDetail(4, "generating"));

    const { result, unmount } = renderHook(() => useJobEvents(projectId, jobId));
    const firstSocket = FakeWebSocket.instances[0]!;
    act(() => {
      firstSocket.emitMessage({ type: "snapshot", revision: 3, job: makeJobDetail(3) });
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.job?.state.revision).not.toBe(5);
    act(() => {
      firstSocket.emitMessage({ type: "snapshot", revision: 5, job: makeJobDetail(5) });
    });
    expect(result.current.job?.state.revision).not.toBe(5);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(getJobMock).toHaveBeenCalledOnce();
    expect(result.current.job?.state.revision).toBe(4);

    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(FakeWebSocket.instances).toHaveLength(2);
    unmount();
    vi.useRealTimers();
  });

  it("ignores callbacks from the previous subscription generation", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
    const getJobMock = vi.spyOn(generationApi, "getGenerationJob").mockResolvedValue(
      makeJobDetail(4, "generating"),
    );
    const { rerender, unmount } = renderHook(
      ({ currentProjectId }) => useJobEvents(currentProjectId, jobId),
      { initialProps: { currentProjectId: projectId } },
    );
    const oldSocket = FakeWebSocket.instances[0]!;
    rerender({ currentProjectId: "7fda5078-1246-4cac-91e8-541808da14f5" });
    getJobMock.mockClear();

    act(() => {
      oldSocket.emitMessage({ type: "snapshot", revision: 9, job: makeJobDetail(9) });
      oldSocket.emitClose();
    });

    await act(async () => {
      await Promise.resolve();
    });
    expect(getJobMock).not.toHaveBeenCalled();
    unmount();
  });

  it("uses the durable job detail when the WebSocket path is unavailable", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
    FakeWebSocket.failConstruction = true;
    const getJobMock = vi
      .spyOn(generationApi, "getGenerationJob")
      .mockResolvedValue(makeJobDetail(7, "generating"));

    const { result, unmount } = renderHook(() => useJobEvents(projectId, jobId));

    await waitFor(() => expect(result.current.job?.state.revision).toBe(7));
    expect(getJobMock).toHaveBeenCalledWith(projectId, jobId, expect.any(AbortSignal));
    expect(result.current.job?.state.status).toBe("generating");
    expect(result.current.connected).toBe(false);

    unmount();
  });
});
