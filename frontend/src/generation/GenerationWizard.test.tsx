import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { CoverageReport, ProjectManifest } from "../api";
import { ApiRequestError } from "../api";
import GenerationWizard from "./GenerationWizard";
import type { PackReferenceRecord, UploadedReferenceRecord } from "./types";

const projectId = "6fda5078-1246-4cac-91e8-541808da14f4";
const generationJobId = "12345678-1234-4abc-8def-123456789abc";

class WizardWebSocket {
  static instances: WizardWebSocket[] = [];

  readonly url: string;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  readyState = 0;

  constructor(url: string) {
    this.url = url;
    WizardWebSocket.instances.push(this);
  }

  emitOpen() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }

  emitClose() {
    this.readyState = 3;
    this.onclose?.(new CloseEvent("close", { code: 1006 }));
  }

  emitSnapshot(payload: unknown) {
    this.onmessage?.(
      new MessageEvent("message", {
        data: JSON.stringify(payload),
      }),
    );
  }

  close() {
    this.readyState = 3;
  }
}

const manifest: ProjectManifest = {
  schemaVersion: 2,
  projectId,
  projectName: "测试项目",
  edition: "java",
  javaPackFormat: 34,
  supportedFormats: [34, 35],
  catalogId: "java-dev-format-34",
  sourceSha256: "a".repeat(64),
  createdAt: "2026-08-03T10:00:00Z",
  updatedAt: "2026-08-03T10:00:00Z",
  defaultResolution: 16,
  defaultParallelism: 1,
  styleReferences: [],
};

const coverage: CoverageReport = {
  catalogId: "java-dev-format-34",
  catalogStatus: "development_fixture",
  coveredCount: 1,
  missingCount: 3,
  unknownPaths: ["assets/custom/textures/block/custom.png"],
  items: [
    {
      semanticId: "minecraft:stone",
      displayName: "Stone",
      relativePath: "assets/minecraft/textures/block/stone.png",
      mvpEligible: true,
      status: "covered",
    },
    {
      semanticId: "minecraft:deepslate",
      displayName: "Deepslate",
      relativePath: "assets/minecraft/textures/block/deepslate.png",
      mvpEligible: true,
      status: "missing",
    },
    {
      semanticId: "minecraft:tuff",
      displayName: "Tuff",
      relativePath: "assets/minecraft/textures/block/tuff.png",
      mvpEligible: true,
      status: "missing",
    },
    {
      semanticId: "minecraft:diamond_block",
      displayName: "Diamond Block",
      relativePath: "assets/minecraft/textures/block/diamond_block.png",
      mvpEligible: true,
      status: "missing",
    },
  ],
};

const generationOptions = {
  candidateCount: 4 as const,
  allowedParallelism: [1, 2, 4] as const,
  defaults: { resolution: 16 as const, parallelism: 1 as const },
  profile: {
    profileId: "sdxl-mapchip-ipadapter",
    profileVersion: "2",
    supportState: "verified" as const,
  },
  resourceHints: [
    {
      parallelism: 1 as const,
      peakVramMiB: 4096,
      peakProcessRamMiB: 6144,
      peakSystemRamMiB: 8192,
      elapsedSeconds: 12.5,
    },
    {
      parallelism: 2 as const,
      peakVramMiB: 6144,
      peakProcessRamMiB: 7168,
      peakSystemRamMiB: 9216,
      elapsedSeconds: 18.25,
    },
    {
      parallelism: 4 as const,
      peakVramMiB: 8192,
      peakProcessRamMiB: 9216,
      peakSystemRamMiB: 11264,
      elapsedSeconds: 31.75,
    },
  ],
  targets: [
    {
      semanticId: "minecraft:deepslate",
      displayName: "Deepslate",
      relativePath: "assets/minecraft/textures/block/deepslate.png",
    },
    {
      semanticId: "minecraft:tuff",
      displayName: "Tuff",
      relativePath: "assets/minecraft/textures/block/tuff.png",
    },
    {
      semanticId: "minecraft:diamond_block",
      displayName: "Diamond Block",
      relativePath: "assets/minecraft/textures/block/diamond_block.png",
    },
  ],
};

const packReferences = [
  {
    source: "pack" as const,
    relativePath: "assets/minecraft/textures/block/stone.png",
    displayName: "Stone",
    sha256: "1".repeat(64),
    byteSize: 96,
    width: 16,
    height: 16,
    mode: "RGB",
  },
  {
    source: "pack" as const,
    relativePath: "assets/custom/textures/block/custom.png",
    displayName: "Custom Stone",
    sha256: "2".repeat(64),
    byteSize: 96,
    width: 16,
    height: 16,
    mode: "RGB",
  },
] satisfies readonly PackReferenceRecord[];

const uploadedStyleReferences = [
  {
    referenceId: "11111111-2222-4333-8444-555555555555",
    kind: "style" as const,
    sha256: "3".repeat(64),
    byteSize: 96,
    width: 16,
    height: 16,
    mode: "RGB",
    createdAt: "2026-08-03T10:01:00Z",
  },
] satisfies readonly UploadedReferenceRecord[];

const uploadedStructureReferences = [
  {
    referenceId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    kind: "structure" as const,
    sha256: "4".repeat(64),
    byteSize: 96,
    width: 16,
    height: 16,
    mode: "RGB",
    createdAt: "2026-08-03T10:02:00Z",
  },
] satisfies readonly UploadedReferenceRecord[];

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
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
  vi.unstubAllGlobals();
  WizardWebSocket.instances.length = 0;
});

beforeEach(() => {
  vi.stubGlobal("WebSocket", WizardWebSocket as unknown as typeof WebSocket);
});

function makeLiveJob(
  status: "queued" | "generating" | "postprocessing" | "completed",
  revision: number,
) {
  return {
    request: { jobId: "12345678-1234-4abc-8def-123456789abc" },
    state: {
      revision,
      status,
      cancelRequestedAt: null,
      failure: null,
      candidates: [0, 1, 2, 3].map((candidateIndex) => ({
        candidateIndex,
        batchIndex: 0,
        positionInBatch: candidateIndex,
        batchSeed: 101,
        status: status === "completed" ? "completed" : "pending",
        artifacts: { raw: null, final: null, nearest: null, tile: null, report: null },
        lineage: null,
        failure: null,
      })),
    },
  } as never;
}

function makeGenerationSnapshot(
  status: "queued" | "generating" | "postprocessing" | "completed",
  revision: number,
) {
  return {
    type: "snapshot",
    revision,
    job: {
      request: {
        schema_version: 3,
        job_id: generationJobId,
        project_id: projectId,
        parent_job_id: null,
        target: {
          target_semantic_id: "minecraft:deepslate",
          target_display_name: "Deepslate",
          target_relative_path: "assets/minecraft/textures/block/deepslate.png",
          catalog_id: "java-dev-format-34",
        },
        prompt: {
          prompt_version: "java-block-prompt-v1",
          positive_prompt: "cold stone",
          negative_prompt: "none",
          user_prompt: "cold stone",
        },
        resolution: 16,
        parallelism: 1,
        execution_batches: [0, 1, 2, 3].map((batchIndex) => ({
          batch_index: batchIndex,
          candidate_indices: [batchIndex],
          seed: 101 + batchIndex,
        })),
        references: { style: [], structure: [] },
        advanced: {
          style_strength: null,
          denoise_strength: null,
          lora_weight: null,
        },
        model_profile: {
          profile_id: "sdxl-mapchip-ipadapter",
          profile_version: "2",
          profile_manifest_sha256: "a".repeat(64),
          runtime_id: "comfyui-windows-nvidia",
          runtime_version: "0.29.2",
          runtime_manifest_sha256: "b".repeat(64),
          workflow_variant: "text2img-no-style",
          workflow_sha256: "c".repeat(64),
          output_node_id: "19",
        },
        created_at: "2026-08-03T10:00:00Z",
      },
      state: {
        schema_version: 2,
        job_id: generationJobId,
        project_id: projectId,
        revision,
        status,
        cancel_requested_at: null,
        failure: null,
        batches: [0, 1, 2, 3].map((batchIndex) => ({
          batch_index: batchIndex,
          candidate_indices: [batchIndex],
          seed: 101 + batchIndex,
          status: "pending",
          prompt_id: null,
          raw_artifacts: [],
          started_at: null,
          finished_at: null,
          failure: null,
        })),
        candidates: [0, 1, 2, 3].map((candidateIndex) => ({
          candidate_index: candidateIndex,
          batch_index: candidateIndex,
          position_in_batch: 0,
          batch_seed: 101 + candidateIndex,
          status: status === "completed" ? "completed" : "pending",
          artifacts: {
            raw: null,
            final: null,
            nearest: null,
            tile: null,
            report: null,
          },
          lineage: null,
          failure: null,
          started_at: null,
          finished_at: null,
        })),
        created_at: "2026-08-03T10:00:00Z",
        updated_at: "2026-08-03T10:00:00Z",
        started_at: null,
        finished_at: null,
      },
    },
  };
}

function renderWizard() {
  const refreshJobs = vi.fn().mockResolvedValue(undefined);
  const onCurrentJobChange = vi.fn();
  render(
    <GenerationWizard
      projectId={projectId}
      manifest={manifest}
      coverage={coverage}
      onJobsChanged={refreshJobs}
      onCurrentJobChange={onCurrentJobChange}
    />,
  );
  return { onCurrentJobChange, refreshJobs };
}

describe("guided generation wizard", () => {
  it("filters missing targets by search and allows back navigation before creation", async () => {
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockResolvedValue(generationOptions);
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue(packReferences);
    vi.spyOn(generationApi, "listUploadedReferences")
      .mockResolvedValueOnce(uploadedStyleReferences)
      .mockResolvedValueOnce(uploadedStructureReferences);

    renderWizard();
    const user = userEvent.setup();

    expect(await screen.findByRole("heading", { name: "选择目标" })).toBeVisible();
    await user.type(screen.getByRole("searchbox", { name: "搜索未覆盖目标" }), "tuff");
    expect(screen.getByRole("radio", { name: /Tuff/ })).toBeVisible();
    expect(screen.queryByRole("radio", { name: /Deepslate/ })).not.toBeInTheDocument();

    await user.clear(screen.getByRole("searchbox", { name: "搜索未覆盖目标" }));
    await user.click(screen.getByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));

    expect(await screen.findByRole("heading", { name: "参考图与描述" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "上一步：选择目标" }));

    expect(await screen.findByRole("heading", { name: "选择目标" })).toBeVisible();
    expect(screen.getByRole("radio", { name: /Deepslate/ })).toBeChecked();
  });

  it("supports 0–8 style refs, optional structure, and conditional advanced controls", async () => {
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockResolvedValue(generationOptions);
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue(packReferences);
    vi.spyOn(generationApi, "listUploadedReferences")
      .mockResolvedValueOnce(uploadedStyleReferences)
      .mockResolvedValueOnce(uploadedStructureReferences);
    vi.spyOn(generationApi, "uploadReference").mockResolvedValue(uploadedStyleReferences[0]);
    vi.spyOn(generationApi, "deleteUploadedReference").mockResolvedValue(undefined);

    renderWizard();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));

    expect(await screen.findByText("风格参考（0–8 张）")).toBeVisible();
    expect(screen.getByText("可选结构参考")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "补充描述" })).toBeVisible();
    expect(screen.queryByLabelText("风格强度")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("结构保持强度")).not.toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: /^Stone$/ }));
    await user.click(screen.getByRole("button", { name: "下一步：生成配置" }));

    expect(await screen.findByRole("heading", { name: "生成配置" })).toBeVisible();
    expect(screen.getByText("固定生成 4 个候选")).toBeVisible();
    expect(screen.getByRole("option", { name: "并行 1" })).toBeVisible();
    expect(screen.getByRole("option", { name: "并行 2" })).toBeVisible();
    expect(screen.getByRole("option", { name: "并行 4" })).toBeVisible();
    const resourceHints = screen.getByRole("list", { name: "资源提示" });
    expect(within(resourceHints).getByText(/并行 1：显存约 4096 MiB/)).toBeVisible();
    expect(within(resourceHints).getByText(/并行 2：显存约 6144 MiB/)).toBeVisible();
    expect(within(resourceHints).getByText(/并行 4：显存约 8192 MiB/)).toBeVisible();
    expect(screen.getByText(/已验证模型配置/)).toBeVisible();
    expect(screen.queryByLabelText(/seed/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /workflow|model|模型/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "上一步：参考图与描述" }));
    await user.click(await screen.findByRole("combobox", { name: "结构参考" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "结构参考" }), [
      "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    ]);
    await user.click(screen.getByRole("button", { name: "下一步：生成配置" }));

    await user.click(screen.getByText("高级设置"));
    expect(await screen.findByLabelText("风格强度")).toBeVisible();
    expect(screen.getByLabelText("结构保持强度")).toBeVisible();
  });

  it("uploads and deletes style references while keeping structure optional", async () => {
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockResolvedValue(generationOptions);
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue(packReferences);
    vi.spyOn(generationApi, "listUploadedReferences")
      .mockResolvedValueOnce(uploadedStyleReferences)
      .mockResolvedValueOnce(uploadedStructureReferences);
    const addedStyle = {
      referenceId: "22222222-3333-4444-8555-666666666666",
      kind: "style" as const,
      sha256: "5".repeat(64),
      byteSize: 96,
      width: 16,
      height: 16,
      mode: "RGB" as const,
      createdAt: "2026-08-03T10:03:00Z",
    };
    const addedStructure = {
      referenceId: "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
      kind: "structure" as const,
      sha256: "6".repeat(64),
      byteSize: 96,
      width: 16,
      height: 16,
      mode: "RGB" as const,
      createdAt: "2026-08-03T10:04:00Z",
    };
    const uploadReference = vi
      .spyOn(generationApi, "uploadReference")
      .mockImplementation(async (_projectId, kind) =>
        kind === "style" ? addedStyle : addedStructure,
      );
    const deleteReference = vi
      .spyOn(generationApi, "deleteUploadedReference")
      .mockResolvedValue(undefined);

    renderWizard();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));

    const styleFile = new File(["style"], "style.png", { type: "image/png" });
    await user.upload(screen.getByLabelText("上传风格参考"), styleFile);
    expect(uploadReference).toHaveBeenCalledWith(projectId, "style", styleFile);
    expect(
      screen.getByRole("button", { name: `删除风格参考 ${addedStyle.referenceId}` }),
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: `删除风格参考 ${addedStyle.referenceId}` }),
    );
    expect(deleteReference).toHaveBeenCalledWith(
      projectId,
      "style",
      addedStyle.referenceId,
    );

    const structureFile = new File(["structure"], "structure.png", {
      type: "image/png",
    });
    await user.upload(screen.getByLabelText("上传结构参考"), structureFile);
    expect(uploadReference).toHaveBeenCalledWith(
      projectId,
      "structure",
      structureFile,
    );
    expect(screen.getByRole("option", { name: addedStructure.referenceId })).toBeInTheDocument();
  });

  it("ignores a stale upload completion after switching projects", async () => {
    const nextProjectId = "7fda5078-1246-4cac-91e8-541808da14f5";
    const nextManifest = {
      ...manifest,
      projectId: nextProjectId,
      projectName: "新项目",
    } satisfies ProjectManifest;
    const nextCoverage = {
      ...coverage,
      catalogId: "java-dev-format-35",
    } satisfies CoverageReport;
    const nextGenerationOptions = {
      ...generationOptions,
      targets: [
        {
          semanticId: "minecraft:granite",
          displayName: "Granite",
          relativePath: "assets/minecraft/textures/block/granite.png",
        },
      ],
    };
    const nextStyleReference = {
      ...uploadedStyleReferences[0],
      referenceId: "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
    } satisfies UploadedReferenceRecord;
    const staleStyleReference = {
      ...uploadedStyleReferences[0],
      referenceId: "cccccccc-dddd-4eee-8fff-000000000000",
    } satisfies UploadedReferenceRecord;
    const pendingUpload = deferred<UploadedReferenceRecord>();
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockImplementation(
      async (requestedProjectId) =>
        requestedProjectId === nextProjectId
          ? nextGenerationOptions
          : generationOptions,
    );
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue([]);
    vi.spyOn(generationApi, "listUploadedReferences").mockImplementation(
      async (requestedProjectId, kind) => {
        if (requestedProjectId === nextProjectId) {
          return kind === "style" ? [nextStyleReference] : [];
        }
        return kind === "style" ? uploadedStyleReferences : uploadedStructureReferences;
      },
    );
    const uploadReference = vi
      .spyOn(generationApi, "uploadReference")
      .mockReturnValue(pendingUpload.promise);

    const refreshJobs = vi.fn().mockResolvedValue(undefined);
    const onCurrentJobChange = vi.fn();
    const view = render(
      <GenerationWizard
        projectId={projectId}
        manifest={manifest}
        coverage={coverage}
        onJobsChanged={refreshJobs}
        onCurrentJobChange={onCurrentJobChange}
      />,
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    const file = new File(["old project"], "old.png", { type: "image/png" });
    await user.upload(screen.getByLabelText("上传风格参考"), file);
    expect(uploadReference).toHaveBeenCalledWith(projectId, "style", file);

    view.rerender(
      <GenerationWizard
        projectId={nextProjectId}
        manifest={nextManifest}
        coverage={nextCoverage}
        onJobsChanged={refreshJobs}
        onCurrentJobChange={onCurrentJobChange}
      />,
    );
    await screen.findByRole("radio", { name: /Granite/ });
    pendingUpload.resolve(staleStyleReference);
    await act(async () => {
      await pendingUpload.promise;
    });

    await user.click(screen.getByRole("radio", { name: /Granite/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    expect(
      screen.getByRole("button", { name: `删除风格参考 ${nextStyleReference.referenceId}` }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: `删除风格参考 ${staleStyleReference.referenceId}` }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ignores a stale delete success after switching projects", async () => {
    const nextProjectId = "7fda5078-1246-4cac-91e8-541808da14f5";
    const nextManifest = {
      ...manifest,
      projectId: nextProjectId,
      projectName: "新项目",
    } satisfies ProjectManifest;
    const nextCoverage = {
      ...coverage,
      catalogId: "java-dev-format-35",
    } satisfies CoverageReport;
    const nextGenerationOptions = {
      ...generationOptions,
      targets: [
        {
          semanticId: "minecraft:granite",
          displayName: "Granite",
          relativePath: "assets/minecraft/textures/block/granite.png",
        },
      ],
    };
    const sharedReference = uploadedStyleReferences[0];
    const pendingDelete = deferred<void>();
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockImplementation(
      async (requestedProjectId) =>
        requestedProjectId === nextProjectId
          ? nextGenerationOptions
          : generationOptions,
    );
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue([]);
    vi.spyOn(generationApi, "listUploadedReferences").mockImplementation(
      async (requestedProjectId, kind) => {
        if (requestedProjectId === nextProjectId) {
          return kind === "style" ? [sharedReference] : [];
        }
        return kind === "style" ? uploadedStyleReferences : uploadedStructureReferences;
      },
    );
    const deleteReference = vi
      .spyOn(generationApi, "deleteUploadedReference")
      .mockReturnValue(pendingDelete.promise);

    const refreshJobs = vi.fn().mockResolvedValue(undefined);
    const onCurrentJobChange = vi.fn();
    const view = render(
      <GenerationWizard
        projectId={projectId}
        manifest={manifest}
        coverage={coverage}
        onJobsChanged={refreshJobs}
        onCurrentJobChange={onCurrentJobChange}
      />,
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    await user.click(screen.getByRole("button", { name: `删除风格参考 ${sharedReference.referenceId}` }));
    expect(deleteReference).toHaveBeenCalledWith(
      projectId,
      "style",
      sharedReference.referenceId,
    );

    view.rerender(
      <GenerationWizard
        projectId={nextProjectId}
        manifest={nextManifest}
        coverage={nextCoverage}
        onJobsChanged={refreshJobs}
        onCurrentJobChange={onCurrentJobChange}
      />,
    );
    await screen.findByRole("radio", { name: /Granite/ });
    await user.click(screen.getByRole("radio", { name: /Granite/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    await user.click(screen.getByRole("checkbox", { name: sharedReference.referenceId }));

    pendingDelete.resolve();
    await act(async () => {
      await pendingDelete.promise;
    });

    expect(
      screen.getByRole("button", { name: `删除风格参考 ${sharedReference.referenceId}` }),
    ).toBeVisible();
    expect(screen.getByRole("checkbox", { name: sharedReference.referenceId })).toBeChecked();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ignores a stale delete failure after switching projects", async () => {
    const nextProjectId = "7fda5078-1246-4cac-91e8-541808da14f5";
    const nextManifest = {
      ...manifest,
      projectId: nextProjectId,
      projectName: "新项目",
    } satisfies ProjectManifest;
    const nextCoverage = {
      ...coverage,
      catalogId: "java-dev-format-35",
    } satisfies CoverageReport;
    const nextGenerationOptions = {
      ...generationOptions,
      targets: [
        {
          semanticId: "minecraft:granite",
          displayName: "Granite",
          relativePath: "assets/minecraft/textures/block/granite.png",
        },
      ],
    };
    const sharedReference = uploadedStyleReferences[0];
    const pendingDelete = deferred<void>();
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockImplementation(
      async (requestedProjectId) =>
        requestedProjectId === nextProjectId
          ? nextGenerationOptions
          : generationOptions,
    );
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue([]);
    vi.spyOn(generationApi, "listUploadedReferences").mockImplementation(
      async (requestedProjectId, kind) => {
        if (requestedProjectId === nextProjectId) {
          return kind === "style" ? [sharedReference] : [];
        }
        return kind === "style" ? uploadedStyleReferences : uploadedStructureReferences;
      },
    );
    vi.spyOn(generationApi, "deleteUploadedReference").mockReturnValue(
      pendingDelete.promise,
    );

    const refreshJobs = vi.fn().mockResolvedValue(undefined);
    const onCurrentJobChange = vi.fn();
    const view = render(
      <GenerationWizard
        projectId={projectId}
        manifest={manifest}
        coverage={coverage}
        onJobsChanged={refreshJobs}
        onCurrentJobChange={onCurrentJobChange}
      />,
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    await user.click(screen.getByRole("button", { name: `删除风格参考 ${sharedReference.referenceId}` }));

    view.rerender(
      <GenerationWizard
        projectId={nextProjectId}
        manifest={nextManifest}
        coverage={nextCoverage}
        onJobsChanged={refreshJobs}
        onCurrentJobChange={onCurrentJobChange}
      />,
    );
    await screen.findByRole("radio", { name: /Granite/ });
    await user.click(screen.getByRole("radio", { name: /Granite/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));

    const rejection = pendingDelete.promise.catch(() => undefined);
    pendingDelete.reject(new Error("old project delete failed"));
    await act(async () => {
      await rejection;
    });

    expect(
      screen.getByRole("button", { name: `删除风格参考 ${sharedReference.referenceId}` }),
    ).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("resets project-scoped wizard state before submitting the newly loaded project", async () => {
    const nextProjectId = "7fda5078-1246-4cac-91e8-541808da14f5";
    const nextManifest = {
      ...manifest,
      projectId: nextProjectId,
      projectName: "新项目",
    } satisfies ProjectManifest;
    const nextCoverage = {
      ...coverage,
      catalogId: "java-dev-format-35",
    } satisfies CoverageReport;
    const nextGenerationOptions = {
      ...generationOptions,
      defaults: { resolution: 32 as const, parallelism: 2 as const },
      targets: [
        {
          semanticId: "minecraft:granite",
          displayName: "Granite",
          relativePath: "assets/minecraft/textures/block/granite.png",
        },
      ],
    };
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockImplementation(
      async (requestedProjectId) =>
        requestedProjectId === nextProjectId
          ? nextGenerationOptions
          : generationOptions,
    );
    vi.spyOn(generationApi, "listPackReferences").mockImplementation(
      async (requestedProjectId) =>
        requestedProjectId === nextProjectId ? [] : packReferences,
    );
    vi.spyOn(generationApi, "listUploadedReferences").mockImplementation(
      async (requestedProjectId, kind) => {
        if (requestedProjectId === nextProjectId) {
          return [];
        }
        return kind === "style" ? uploadedStyleReferences : uploadedStructureReferences;
      },
    );
    const createdJob = {
      request: { jobId: "12345678-1234-4abc-8def-123456789abc" },
      state: { status: "queued" },
    } as never;
    vi.spyOn(generationApi, "createGenerationJob").mockResolvedValue(createdJob);
    vi.spyOn(generationApi, "startGenerationJob").mockResolvedValue(createdJob);

    const refreshJobs = vi.fn().mockResolvedValue(undefined);
    const onCurrentJobChange = vi.fn();
    const view = render(
      <GenerationWizard
        projectId={projectId}
        manifest={manifest}
        coverage={coverage}
        onJobsChanged={refreshJobs}
        onCurrentJobChange={onCurrentJobChange}
      />,
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    await user.click(screen.getByRole("checkbox", { name: /^Stone$/ }));
    await user.selectOptions(screen.getByRole("combobox", { name: "结构参考" }), [
      "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    ]);
    await user.type(screen.getByRole("textbox", { name: "补充描述" }), "旧描述");
    await user.click(screen.getByRole("button", { name: "下一步：生成配置" }));
    await user.click(screen.getByText("高级设置"));
    await user.selectOptions(screen.getByLabelText("分辨率"), "64");
    await user.selectOptions(screen.getByLabelText("并行方式"), "4");
    await user.type(screen.getByLabelText("负面提示词"), "旧负面提示");
    await user.type(screen.getByLabelText("结构保持强度"), "0.4");
    await user.type(screen.getByLabelText("风格强度"), "0.8");

    view.rerender(
      <GenerationWizard
        projectId={nextProjectId}
        manifest={nextManifest}
        coverage={nextCoverage}
        onJobsChanged={refreshJobs}
        onCurrentJobChange={onCurrentJobChange}
      />,
    );

    expect(await screen.findByRole("radio", { name: /Granite/ })).not.toBeChecked();
    await user.click(screen.getByRole("radio", { name: /Granite/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    expect(screen.getByRole("combobox", { name: "结构参考" })).toHaveValue("");
    expect(screen.getByRole("textbox", { name: "补充描述" })).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "下一步：生成配置" }));

    expect(screen.getByLabelText("分辨率")).toHaveValue("32");
    expect(screen.getByLabelText("并行方式")).toHaveValue("2");
    await user.click(screen.getByText("高级设置"));
    expect(screen.getByLabelText("负面提示词")).toHaveValue("");
    expect(screen.queryByLabelText("结构保持强度")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("风格强度")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "创建并开始生成" }));

    const createJob = generationApi.createGenerationJob as unknown as {
      mock: { calls: readonly [string, Record<string, unknown>][] };
    };
    await waitFor(() => expect(createJob.mock.calls).toHaveLength(1));
    expect(createJob.mock.calls[0]?.[0]).toBe(nextProjectId);
    expect(createJob.mock.calls[0]?.[1]).toMatchObject({
      targetSemanticId: "minecraft:granite",
      styleReferences: [],
      structureReference: null,
      userDescription: "",
      userNegativePrompt: "",
      resolution: 32,
      parallelism: 2,
      denoise: null,
      styleWeight: null,
    });
  });

  it("keeps the completed candidate view after same-project job history refresh", async () => {
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockResolvedValue(generationOptions);
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue(packReferences);
    vi.spyOn(generationApi, "listUploadedReferences").mockImplementation(
      async (_requestedProjectId, kind) =>
        kind === "style" ? uploadedStyleReferences : uploadedStructureReferences,
    );
    const completedJob = {
      request: { jobId: "12345678-1234-4abc-8def-123456789abc" },
      state: {
        status: "completed",
        candidates: [0, 1, 2, 3].map((candidateIndex) => ({
          candidateIndex,
          batchIndex: 0,
          batchSeed: 101,
          positionInBatch: candidateIndex,
          status: "completed",
          artifacts: {
            raw: null,
            final: {
              relativePath: `candidates/${candidateIndex}.png`,
              sha256: "a".repeat(64),
              byteSize: 96,
              mediaType: "image/png",
              width: 16,
              height: 16,
            },
            nearest: null,
            tile: null,
            report: null,
          },
          lineage: null,
          failure: null,
        })),
      },
    } as never;
    vi.spyOn(generationApi, "createGenerationJob").mockResolvedValue(completedJob);
    vi.spyOn(generationApi, "startGenerationJob").mockResolvedValue(completedJob);

    let view!: ReturnType<typeof render>;
    const refreshJobs = vi.fn(async () => {
      const refreshedManifest = Object.fromEntries(
        Object.entries(manifest).reverse(),
      ) as ProjectManifest;
      const refreshedCoverage = Object.fromEntries(
        Object.entries({ ...coverage, items: [...coverage.items] }).reverse(),
      ) as unknown as CoverageReport;
      view.rerender(
        <GenerationWizard
          projectId={projectId}
          manifest={refreshedManifest}
          coverage={refreshedCoverage}
          onJobsChanged={refreshJobs}
          onCurrentJobChange={vi.fn()}
        />,
      );
    });
    view = render(
      <GenerationWizard
        projectId={projectId}
        manifest={manifest}
        coverage={coverage}
        onJobsChanged={refreshJobs}
        onCurrentJobChange={vi.fn()}
      />,
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    await user.click(screen.getByRole("button", { name: "下一步：生成配置" }));
    await user.click(await screen.findByRole("button", { name: "创建并开始生成" }));

    await waitFor(() => expect(refreshJobs).toHaveBeenCalledOnce());
    expect(await screen.findByRole("heading", { name: "候选结果" })).toBeVisible();
    expect(screen.getByRole("article", { name: "候选 1" })).toBeVisible();
    expect(screen.getByRole("article", { name: "候选 4" })).toBeVisible();
  });

  it("refreshes history on durable status transitions and hides Continue once active", async () => {
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockResolvedValue(generationOptions);
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue(packReferences);
    vi.spyOn(generationApi, "listUploadedReferences").mockResolvedValue([]);
    const queuedJob = makeLiveJob("queued", 1);
    const getJob = vi
      .spyOn(generationApi, "getGenerationJob")
      .mockResolvedValue(queuedJob);
    vi.spyOn(generationApi, "createGenerationJob").mockResolvedValue(queuedJob);
    vi.spyOn(generationApi, "startGenerationJob").mockResolvedValue(queuedJob);
    const refreshJobs = vi.fn().mockResolvedValue(undefined);
    const view = render(
      <GenerationWizard
        projectId={projectId}
        manifest={manifest}
        coverage={coverage}
        onJobsChanged={refreshJobs}
        onCurrentJobChange={vi.fn()}
      />,
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    await user.click(screen.getByRole("button", { name: "下一步：生成配置" }));
    await user.click(await screen.findByRole("button", { name: "创建并开始生成" }));

    expect(await screen.findByRole("heading", { name: "候选结果" })).toBeVisible();
    expect(screen.getByRole("button", { name: "继续任务" })).toBeVisible();
    await waitFor(() => expect(WizardWebSocket.instances).toHaveLength(1));
    refreshJobs.mockClear();

    getJob.mockResolvedValue(makeLiveJob("generating", 2));
    act(() => WizardWebSocket.instances[0]!.emitClose());
    await waitFor(() => expect(refreshJobs).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "继续任务" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消任务" })).toBeVisible();
    await waitFor(() => expect(WizardWebSocket.instances).toHaveLength(2));

    getJob.mockResolvedValue(makeLiveJob("postprocessing", 3));
    act(() => WizardWebSocket.instances[1]!.emitClose());
    await waitFor(() => expect(refreshJobs).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(WizardWebSocket.instances).toHaveLength(3));

    getJob.mockResolvedValue(makeLiveJob("completed", 4));
    act(() => WizardWebSocket.instances[2]!.emitClose());
    await waitFor(() => expect(refreshJobs).toHaveBeenCalledTimes(3));
    expect(screen.queryByRole("button", { name: "继续任务" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "取消任务" })).not.toBeInTheDocument();
    view.unmount();
  });

  it("keeps a newer HTTP command response when an older live snapshot arrives", async () => {
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockResolvedValue(generationOptions);
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue(packReferences);
    vi.spyOn(generationApi, "listUploadedReferences").mockResolvedValue([]);
    const queuedJob = makeLiveJob("queued", 1);
    const startJob = vi
      .spyOn(generationApi, "startGenerationJob")
      .mockResolvedValue(makeLiveJob("generating", 2));
    vi.spyOn(generationApi, "createGenerationJob").mockResolvedValue(queuedJob);
    const refreshJobs = vi.fn().mockResolvedValue(undefined);
    render(
      <GenerationWizard
        projectId={projectId}
        manifest={manifest}
        coverage={coverage}
        onJobsChanged={refreshJobs}
        onCurrentJobChange={vi.fn()}
      />,
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    await user.click(screen.getByRole("button", { name: "下一步：生成配置" }));
    await user.click(await screen.findByRole("button", { name: "创建并开始生成" }));

    await waitFor(() => expect(startJob).toHaveBeenCalledWith(projectId, generationJobId));
    await waitFor(() => expect(WizardWebSocket.instances).toHaveLength(1));
    act(() =>
      WizardWebSocket.instances[0]!.emitSnapshot(
        makeGenerationSnapshot("queued", 1),
      ),
    );
    expect(screen.queryByRole("button", { name: "继续任务" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消任务" })).toBeVisible();
  });

  it("does not rerun the live projection effect for callback-only parent rerenders", async () => {
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockResolvedValue(generationOptions);
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue(packReferences);
    vi.spyOn(generationApi, "listUploadedReferences").mockResolvedValue([]);
    const queuedJob = makeLiveJob("queued", 1);
    const activeJob = makeLiveJob("generating", 2);
    const getJob = vi
      .spyOn(generationApi, "getGenerationJob")
      .mockResolvedValue(activeJob);
    vi.spyOn(generationApi, "createGenerationJob").mockResolvedValue(queuedJob);
    vi.spyOn(generationApi, "startGenerationJob").mockResolvedValue(activeJob);
    const firstCurrentJobChange = vi.fn();
    const view = render(
      <GenerationWizard
        projectId={projectId}
        manifest={manifest}
        coverage={coverage}
        onJobsChanged={vi.fn().mockResolvedValue(undefined)}
        onCurrentJobChange={firstCurrentJobChange}
      />,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    await user.click(screen.getByRole("button", { name: "下一步：生成配置" }));
    await user.click(await screen.findByRole("button", { name: "创建并开始生成" }));
    await screen.findByRole("heading", { name: "候选结果" });
    await waitFor(() => expect(WizardWebSocket.instances).toHaveLength(1));
    act(() => WizardWebSocket.instances[0]!.emitClose());
    await waitFor(() => expect(getJob).toHaveBeenCalled());
    expect(firstCurrentJobChange).toHaveBeenCalled();

    const replacementCurrentJobChange = vi.fn();
    view.rerender(
      <GenerationWizard
        projectId={projectId}
        manifest={manifest}
        coverage={coverage}
        onJobsChanged={vi.fn().mockResolvedValue(undefined)}
        onCurrentJobChange={replacementCurrentJobChange}
      />,
    );

    expect(replacementCurrentJobChange).not.toHaveBeenCalled();
    view.unmount();
  });

  it("creates then starts one schema-3 job and skips start when create fails", async () => {
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockResolvedValue(generationOptions);
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue(packReferences);
    vi.spyOn(generationApi, "listUploadedReferences")
      .mockResolvedValueOnce(uploadedStyleReferences)
      .mockResolvedValueOnce(uploadedStructureReferences);
    const createJob = vi.spyOn(generationApi, "createGenerationJob").mockResolvedValue({
      request: { jobId: "12345678-1234-4abc-8def-123456789abc" },
      state: { status: "queued" },
    } as never);
    const startJob = vi.spyOn(generationApi, "startGenerationJob").mockResolvedValue({
      request: { jobId: "12345678-1234-4abc-8def-123456789abc" },
      state: { status: "queued" },
    } as never);

    const { onCurrentJobChange, refreshJobs } = renderWizard();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    await user.click(screen.getByRole("button", { name: "下一步：生成配置" }));
    await user.click(await screen.findByRole("button", { name: "创建并开始生成" }));

    await waitFor(() => expect(createJob).toHaveBeenCalledOnce());
    await waitFor(() => expect(startJob).toHaveBeenCalledOnce());
    expect(createJob.mock.invocationCallOrder[0]).toBeLessThan(
      startJob.mock.invocationCallOrder[0]!,
    );
    expect(refreshJobs).toHaveBeenCalled();
    expect(onCurrentJobChange).toHaveBeenCalled();

    createJob.mockReset();
    startJob.mockReset();
    createJob.mockRejectedValue(
      new ApiRequestError({
        code: "INVALID_REQUEST",
        stage: "request_validation",
        userMessage: "bad request",
        recommendedActions: [],
        technicalDetails: null,
      }),
    );

    await user.click(screen.getByRole("button", { name: "创建并开始生成" }));

    await waitFor(() => expect(createJob).toHaveBeenCalledOnce());
    expect(startJob).not.toHaveBeenCalled();
  });

  it("keeps a queued job visible and reports a stable error when start fails", async () => {
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockResolvedValue(generationOptions);
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue(packReferences);
    vi.spyOn(generationApi, "listUploadedReferences")
      .mockResolvedValueOnce(uploadedStyleReferences)
      .mockResolvedValueOnce(uploadedStructureReferences);
    const queuedJob = {
      request: { jobId: "12345678-1234-4abc-8def-123456789abc" },
      state: { status: "queued" },
    } as never;
    const createJob = vi
      .spyOn(generationApi, "createGenerationJob")
      .mockResolvedValue(queuedJob);
    const startJob = vi.spyOn(generationApi, "startGenerationJob").mockRejectedValue(
      new ApiRequestError({
        code: "GPU_UNAVAILABLE",
        stage: "starting",
        userMessage: "backend detail",
        recommendedActions: [],
        technicalDetails: null,
      }),
    );

    const { onCurrentJobChange, refreshJobs } = renderWizard();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    await user.click(screen.getByRole("button", { name: "下一步：生成配置" }));
    await user.click(await screen.findByRole("button", { name: "创建并开始生成" }));

    await waitFor(() => expect(createJob).toHaveBeenCalledOnce());
    await waitFor(() => expect(startJob).toHaveBeenCalledOnce());
    expect(onCurrentJobChange).toHaveBeenCalledWith(queuedJob);
    await waitFor(() => expect(refreshJobs).toHaveBeenCalledOnce());
    expect(screen.getByRole("alert")).toHaveTextContent(
      "生成任务已创建，但启动失败，请稍后重试。",
    );
  });

  it("keeps conflict visible and does not call start when create returns GENERATION_JOB_CONFLICT", async () => {
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockResolvedValue(generationOptions);
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue(packReferences);
    vi.spyOn(generationApi, "listUploadedReferences")
      .mockResolvedValueOnce(uploadedStyleReferences)
      .mockResolvedValueOnce(uploadedStructureReferences);
    vi.spyOn(generationApi, "createGenerationJob").mockRejectedValue(
      new ApiRequestError({
        code: "GENERATION_JOB_CONFLICT",
        stage: "creating_generation_job",
        userMessage: "当前已有一个未完成任务",
        recommendedActions: ["查看当前任务或取消后再创建新任务"],
        technicalDetails: null,
      }),
    );
    const startJob = vi.spyOn(generationApi, "startGenerationJob");

    renderWizard();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    await user.click(screen.getByRole("button", { name: "下一步：生成配置" }));
    await user.click(await screen.findByRole("button", { name: "创建并开始生成" }));

    expect(await screen.findByText("当前已有一个未完成任务")).toBeVisible();
    expect(startJob).not.toHaveBeenCalled();
  });

  it("keeps the failed job visible and shows an error when retry rejects", async () => {
    const generationApi = await import("./api");
    vi.spyOn(generationApi, "getGenerationOptions").mockResolvedValue(generationOptions);
    vi.spyOn(generationApi, "listPackReferences").mockResolvedValue(packReferences);
    vi.spyOn(generationApi, "listUploadedReferences")
      .mockResolvedValueOnce(uploadedStyleReferences)
      .mockResolvedValueOnce(uploadedStructureReferences);
    const failedJob = {
      request: { jobId: "12345678-1234-4abc-8def-123456789abc" },
      state: {
        status: "failed",
        failure: {
          code: "GPU_OUT_OF_MEMORY",
          stage: "execution",
          userMessage: "显存不足",
          recommendedActions: ["降低并行度后重试"],
          technicalDetails: null,
          retryable: true,
          occurredAt: "2026-08-03T10:00:00Z",
        },
        candidates: [0, 1, 2, 3].map((candidateIndex) => ({
          candidateIndex,
          batchSeed: 101,
          positionInBatch: candidateIndex,
          status: "failed",
          artifacts: { raw: null, final: null, nearest: null, tile: null, report: null },
          lineage: null,
          failure: null,
        })),
      },
    } as never;
    vi.spyOn(generationApi, "createGenerationJob").mockResolvedValue(failedJob);
    vi.spyOn(generationApi, "startGenerationJob").mockResolvedValue(failedJob);
    vi.spyOn(generationApi, "retryGenerationJob").mockRejectedValue(
      new ApiRequestError({
        code: "GENERATION_JOB_CONFLICT",
        stage: "retrying_generation_job",
        userMessage: "当前已有一个未完成任务",
        recommendedActions: ["查看当前任务"],
        technicalDetails: null,
      }),
    );

    renderWizard();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: /Deepslate/ }));
    await user.click(screen.getByRole("button", { name: "下一步：参考图与描述" }));
    await user.click(screen.getByRole("button", { name: "下一步：生成配置" }));
    await user.click(screen.getByRole("button", { name: "创建并开始生成" }));
    const retryButton = await screen.findByRole("button", { name: "重试任务" });
    await user.click(retryButton);

    expect(await screen.findByText("当前已有一个未完成任务")).toBeVisible();
    expect(screen.getByRole("button", { name: "重试任务" })).toBeVisible();
  });
});
