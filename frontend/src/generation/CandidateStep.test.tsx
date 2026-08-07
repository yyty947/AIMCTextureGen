import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiError } from "../api";
import CandidateStep from "./CandidateStep";
import type { GenerationJobDetail } from "./types";

const projectId = "6fda5078-1246-4cac-91e8-541808da14f4";
const jobId = "0f6fb74b-5d0f-46b0-bf03-2fb41aa83694";
const parentJobId = "3ec8b782-ec9b-48a6-b949-9ed175122414";
const createdAt = "2026-08-03T10:00:00Z";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function makeJobDetail(
  overrides?: Partial<GenerationJobDetail["state"]>,
): GenerationJobDetail {
  return {
    request: {
      schemaVersion: 3,
      jobId,
      projectId,
      parentJobId,
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
      revision: 4,
      status: "generating",
      cancelRequestedAt: null,
      failure: null,
      batches: [
        {
          batchIndex: 0,
          candidateIndices: [0, 1],
          seed: 101,
          status: "completed",
          promptId: "prompt-1",
          rawArtifacts: [],
          startedAt: createdAt,
          finishedAt: createdAt,
          failure: null,
        },
        {
          batchIndex: 1,
          candidateIndices: [2, 3],
          seed: 202,
          status: "generating",
          promptId: "prompt-2",
          rawArtifacts: [],
          startedAt: createdAt,
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
          status: "completed",
          artifacts: {
            raw: {
              kind: "raw",
              relativePath: "jobs/raw/0.png",
              sha256: "1".repeat(64),
              byteSize: 512,
              mediaType: "image/png",
              width: 1024,
              height: 1024,
            },
            final: {
              kind: "final",
              relativePath: "jobs/final/0.png",
              sha256: "2".repeat(64),
              byteSize: 128,
              mediaType: "image/png",
              width: 16,
              height: 16,
            },
            nearest: {
              kind: "nearest",
              relativePath: "jobs/nearest/0.png",
              sha256: "3".repeat(64),
              byteSize: 256,
              mediaType: "image/png",
              width: 128,
              height: 128,
            },
            tile: {
              kind: "tile",
              relativePath: "jobs/tile/0.png",
              sha256: "4".repeat(64),
              byteSize: 384,
              mediaType: "image/png",
              width: 48,
              height: 48,
            },
            report: {
              kind: "report",
              relativePath: "jobs/report/0.json",
              sha256: "5".repeat(64),
              byteSize: 64,
              mediaType: "application/json",
              width: null,
              height: null,
            },
          },
          lineage: null,
          failure: null,
          startedAt: createdAt,
          finishedAt: createdAt,
        },
        {
          candidateIndex: 1,
          batchIndex: 0,
          positionInBatch: 1,
          batchSeed: 101,
          status: "inherited",
          artifacts: {
            raw: null,
            final: {
              kind: "final",
              relativePath: "jobs/final/1.png",
              sha256: "6".repeat(64),
              byteSize: 128,
              mediaType: "image/png",
              width: 16,
              height: 16,
            },
            nearest: null,
            tile: null,
            report: null,
          },
          lineage: {
            parentJobId,
            parentCandidateIndex: 1,
          },
          failure: null,
          startedAt: createdAt,
          finishedAt: createdAt,
        },
        {
          candidateIndex: 2,
          batchIndex: 1,
          positionInBatch: 0,
          batchSeed: 202,
          status: "generating",
          artifacts: {
            raw: null,
            final: null,
            nearest: null,
            tile: null,
            report: null,
          },
          lineage: null,
          failure: null,
          startedAt: createdAt,
          finishedAt: null,
        },
        {
          candidateIndex: 3,
          batchIndex: 1,
          positionInBatch: 1,
          batchSeed: 202,
          status: "pending",
          artifacts: {
            raw: null,
            final: null,
            nearest: null,
            tile: null,
            report: null,
          },
          lineage: null,
          failure: null,
          startedAt: null,
          finishedAt: null,
        },
      ],
      createdAt,
      updatedAt: createdAt,
      startedAt: createdAt,
      finishedAt: null,
      ...overrides,
    },
  };
}

function renderCandidateStep(
  job = makeJobDetail(),
  error: ApiError | null = null,
) {
  const onContinue = vi.fn();
  const onCancel = vi.fn();
  const onRetry = vi.fn();
  render(
    <CandidateStep
      connected
      error={error}
      job={job}
      projectId={projectId}
      onCancel={onCancel}
      onContinue={onContinue}
      onRefresh={vi.fn()}
      onRetry={onRetry}
    />,
  );
  return { onContinue, onCancel, onRetry };
}

describe("CandidateStep", () => {
  it("renders four stable candidate cards, artifact tabs, seam report, and read-only batch identity", async () => {
    const report = await import("./api");
    vi.spyOn(report, "getCandidateReport").mockResolvedValue({
      schemaVersion: 1,
      resolution: 16,
      seamScore: {
        horizontal: 0.1,
        vertical: 0.15,
        average: 0.125,
      },
    } as never);

    renderCandidateStep();
    const user = userEvent.setup();

    expect(screen.getAllByRole("article")).toHaveLength(4);
    expect(screen.getByText("候选 1")).toBeVisible();
    expect(screen.getByText("候选 2")).toBeVisible();
    expect(screen.getByText("候选 3")).toBeVisible();
    expect(screen.getByText("候选 4")).toBeVisible();
    expect(screen.getAllByText("批次 seed 101").length).toBeGreaterThan(0);
    expect(screen.getAllByText("批次 0 / 位置 0").length).toBeGreaterThan(0);
    expect(screen.queryByRole("spinbutton", { name: /seed/i })).not.toBeInTheDocument();

    const firstCard = screen.getByRole("article", { name: "候选 1" });
    await user.click(within(firstCard).getByRole("tab", { name: "放大预览" }));
    await user.click(within(firstCard).getByRole("tab", { name: "3×3 平铺" }));
    await user.click(within(firstCard).getByRole("button", { name: "读取质量报告" }));

    expect(await screen.findByText(/seam score 0.125/i)).toBeVisible();
    expect(screen.queryByRole("button", { name: /采用|导出/ })).not.toBeInTheDocument();
  });

  it("shows continue, cancel state, retry, and preserves completed cards through cancellation or failure", async () => {
    const queued = makeJobDetail({ status: "queued" });
    const { onContinue, onCancel, onRetry } = renderCandidateStep(queued);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "继续任务" }));
    expect(onContinue).toHaveBeenCalledOnce();

    cleanup();
    renderCandidateStep(
      makeJobDetail({
        status: "generating",
        cancelRequestedAt: "2026-08-03T10:02:00Z",
      }),
    );
    expect(screen.getByText(/正在等待取消确认/)).toBeVisible();
    expect(screen.getByText("候选 1")).toBeVisible();

    cleanup();
    renderCandidateStep(
      makeJobDetail({
        status: "failed",
        failure: {
          code: "GPU_OUT_OF_MEMORY",
          stage: "execution",
          userMessage: "显存不足，当前原生批次未能完成",
          recommendedActions: ["用更低并行度重新创建一个新任务", "关闭其他占用显存的应用程序"],
          technicalDetails: null,
          retryable: true,
          occurredAt: createdAt,
        },
      }),
    );
    expect(screen.getByText("显存不足，当前原生批次未能完成")).toBeVisible();
    expect(screen.getByText("用更低并行度重新创建一个新任务")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "重试任务" }));
    expect(screen.getByRole("button", { name: "重试任务" })).toBeVisible();

    cleanup();
    const active = renderCandidateStep(makeJobDetail());
    await user.click(screen.getByRole("button", { name: "取消任务" }));
    expect(active.onCancel).toHaveBeenCalledOnce();
  });

  it("shows conflict and interrupted guidance without pretending legacy jobs are executable", () => {
    renderCandidateStep(
      makeJobDetail({
        status: "failed",
        failure: {
          code: "JOB_INTERRUPTED",
          stage: "recovery",
          userMessage: "应用重启时任务仍在运行",
          recommendedActions: ["检查已完成候选后重试任务"],
          technicalDetails: null,
          retryable: true,
          occurredAt: createdAt,
        },
      }),
      {
        code: "GENERATION_JOB_CONFLICT",
        stage: "creating_generation_job",
        userMessage: "当前已有一个未完成任务",
        recommendedActions: ["查看当前任务或取消后再创建新任务"],
        technicalDetails: null,
      },
    );

    expect(screen.getByText("当前已有一个未完成任务")).toBeVisible();
    expect(screen.getByText("查看当前任务或取消后再创建新任务")).toBeVisible();
    expect(screen.getByText("检查已完成候选后重试任务")).toBeVisible();
  });

  it("shows a stable report error when report loading rejects", async () => {
    const report = await import("./api");
    vi.spyOn(report, "getCandidateReport").mockRejectedValue(new Error("report unavailable"));
    renderCandidateStep();

    await userEvent.setup().click(
      within(screen.getByRole("article", { name: "候选 1" })).getByRole("button", {
        name: "读取质量报告",
      }),
    );

    expect(await screen.findByText("质量报告读取失败，请稍后重试。")).toBeVisible();
  });

  it("does not publish a stale report after the job changes", async () => {
    const report = await import("./api");
    const pending = deferred<Awaited<ReturnType<typeof report.getCandidateReport>>>();
    vi.spyOn(report, "getCandidateReport").mockReturnValue(pending.promise);
    const view = render(
      <CandidateStep
        connected
        error={null}
        job={makeJobDetail()}
        projectId={projectId}
        onCancel={vi.fn()}
        onContinue={vi.fn()}
        onRefresh={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    await userEvent.setup().click(
      within(screen.getByRole("article", { name: "候选 1" })).getByRole("button", {
        name: "读取质量报告",
      }),
    );
    const nextJob = makeJobDetail();
    const nextJobId = "12345678-1234-4abc-8def-123456789abc";
    view.rerender(
      <CandidateStep
        connected
        error={null}
        job={{
          ...nextJob,
          request: { ...nextJob.request, jobId: nextJobId },
          state: { ...nextJob.state, jobId: nextJobId },
        }}
        projectId={projectId}
        onCancel={vi.fn()}
        onContinue={vi.fn()}
        onRefresh={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    pending.resolve({
      schemaVersion: 1,
      resolution: 16,
      seamScore: { horizontal: 0.1, vertical: 0.15, average: 0.125 },
    });
    await act(async () => {
      await pending.promise;
    });

    expect(screen.queryByText(/seam score/)).not.toBeInTheDocument();
  });

  it("does not offer retry when failure metadata is missing", () => {
    renderCandidateStep(makeJobDetail({ status: "failed", failure: null }));
    expect(screen.queryByRole("button", { name: "重试任务" })).not.toBeInTheDocument();
  });
});
