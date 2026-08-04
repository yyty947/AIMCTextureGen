import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiRequestError,
  type CoverageReport,
  type ProjectManifest,
} from "../api";
import {
  createGenerationJob,
  getGenerationJob,
  getGenerationOptions,
  listPackReferences,
  listUploadedReferences,
  readCandidateArtifactUrl,
  startGenerationJob,
  type CreateGenerationInput,
} from "./api";

const projectId = "6fda5078-1246-4cac-91e8-541808da14f4";
const jobId = "0f6fb74b-5d0f-46b0-bf03-2fb41aa83694";
const createdAt = "2026-08-03T10:00:00Z";

const manifest: ProjectManifest = {
  schemaVersion: 2,
  projectId,
  projectName: "测试项目",
  edition: "java",
  javaPackFormat: 34,
  supportedFormats: [34, 35],
  catalogId: "java-dev-format-34",
  sourceSha256: "a".repeat(64),
  createdAt,
  updatedAt: createdAt,
  defaultResolution: 16,
  defaultParallelism: 1,
  styleReferences: [],
};

const coverage: CoverageReport = {
  catalogId: "java-dev-format-34",
  catalogStatus: "development_fixture",
  coveredCount: 1,
  missingCount: 2,
  unknownPaths: ["assets/custom/block/custom.png"],
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
  ],
};

const createInput: CreateGenerationInput = {
  targetSemanticId: "minecraft:deepslate",
  userDescription: "cold stone",
  userNegativePrompt: "",
  resolution: 16,
  parallelism: 2,
  styleReferences: [
    {
      source: "pack",
      relativePath: "assets/minecraft/textures/block/stone.png",
    },
    {
      source: "upload",
      referenceId: "11111111-2222-4333-8444-555555555555",
    },
  ],
  structureReference: {
    source: "upload",
    referenceId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
  },
  denoise: 0.45,
  styleWeight: 0.8,
};

const jobDetailResponse = {
  request: {
    schema_version: 3,
    job_id: jobId,
    project_id: projectId,
    parent_job_id: null,
    target: {
      catalog_id: "java-dev-format-34",
      target_semantic_id: "minecraft:deepslate",
      target_display_name: "Deepslate",
      target_relative_path: "assets/minecraft/textures/block/deepslate.png",
    },
    prompt: {
      prompt_version: "java-block-prompt-v1",
      positive_prompt: "16x16 cold stone",
      negative_prompt: "none",
      user_prompt: "cold stone",
    },
    resolution: 16,
    parallelism: 2,
    execution_batches: [
      { batch_index: 0, candidate_indices: [0, 1], seed: 101 },
      { batch_index: 1, candidate_indices: [2, 3], seed: 202 },
    ],
    references: {
      style: [
        {
          kind: "raw",
          relative_path: "inputs/style/00.png",
          byte_size: 96,
          width: 16,
          height: 16,
          media_type: "image/png",
          sha256: "b".repeat(64),
        },
      ],
      structure: [
        {
          kind: "raw",
          relative_path: "inputs/structure.png",
          byte_size: 97,
          width: 16,
          height: 16,
          media_type: "image/png",
          sha256: "c".repeat(64),
        },
      ],
    },
    advanced: {
      style_strength: 0.8,
      denoise_strength: 0.45,
      lora_weight: null,
    },
    model_profile: {
      profile_id: "sdxl-mapchip-ipadapter",
      profile_version: "2",
      profile_manifest_sha256: "d".repeat(64),
      runtime_id: "comfyui-windows-nvidia",
      runtime_version: "0.29.2",
      runtime_manifest_sha256: "e".repeat(64),
      workflow_variant: "img2img-style",
      workflow_sha256: "f".repeat(64),
      output_node_id: "19",
    },
    created_at: createdAt,
  },
  state: {
    schema_version: 2,
    job_id: jobId,
    project_id: projectId,
    revision: 0,
    status: "queued",
    cancel_requested_at: null,
    failure: null,
    batches: [
      {
        batch_index: 0,
        candidate_indices: [0, 1],
        seed: 101,
        status: "pending",
        prompt_id: null,
        raw_artifacts: [],
        started_at: null,
        finished_at: null,
        failure: null,
      },
      {
        batch_index: 1,
        candidate_indices: [2, 3],
        seed: 202,
        status: "pending",
        prompt_id: null,
        raw_artifacts: [],
        started_at: null,
        finished_at: null,
        failure: null,
      },
    ],
    candidates: [
      {
        candidate_index: 0,
        batch_index: 0,
        position_in_batch: 0,
        batch_seed: 101,
        status: "pending",
        artifacts: {
          raw: null,
          final: null,
          nearest: null,
          tile: null,
          report: null,
        },
        failure: null,
        lineage: null,
        started_at: null,
        finished_at: null,
      },
      {
        candidate_index: 1,
        batch_index: 0,
        position_in_batch: 1,
        batch_seed: 101,
        status: "pending",
        artifacts: {
          raw: null,
          final: null,
          nearest: null,
          tile: null,
          report: null,
        },
        failure: null,
        lineage: null,
        started_at: null,
        finished_at: null,
      },
      {
        candidate_index: 2,
        batch_index: 1,
        position_in_batch: 0,
        batch_seed: 202,
        status: "pending",
        artifacts: {
          raw: null,
          final: null,
          nearest: null,
          tile: null,
          report: null,
        },
        failure: null,
        lineage: null,
        started_at: null,
        finished_at: null,
      },
      {
        candidate_index: 3,
        batch_index: 1,
        position_in_batch: 1,
        batch_seed: 202,
        status: "pending",
        artifacts: {
          raw: null,
          final: {
            kind: "final",
            relative_path: "artifacts/final with space.png",
            sha256: "f".repeat(64),
            byte_size: 512,
            media_type: "image/png",
            width: 16,
            height: 16,
          },
          nearest: null,
          tile: null,
          report: null,
        },
        failure: null,
        lineage: null,
        started_at: null,
        finished_at: null,
      },
    ],
    created_at: createdAt,
    updated_at: createdAt,
    started_at: null,
    finished_at: null,
  },
};

const generationOptionsResponse = {
  candidate_count: 4,
  allowed_parallelism: [1, 2, 4],
  defaults: { resolution: 16, parallelism: 1 },
  profile: {
    profile_id: "sdxl-mapchip-ipadapter",
    profile_version: "2",
    support_state: "verified",
  },
  resource_hints: [
    {
      parallelism: 1,
      peak_vram_mib: 4096,
      peak_process_ram_mib: 6144,
      peak_system_ram_mib: 8192,
      elapsed_seconds: 12.5,
    },
    {
      parallelism: 2,
      peak_vram_mib: 6144,
      peak_process_ram_mib: 7168,
      peak_system_ram_mib: 9216,
      elapsed_seconds: 18.25,
    },
    {
      parallelism: 4,
      peak_vram_mib: 8192,
      peak_process_ram_mib: 9216,
      peak_system_ram_mib: 11264,
      elapsed_seconds: 31.75,
    },
  ],
  targets: [
    {
      semantic_id: "minecraft:deepslate",
      display_name: "Deepslate",
      relative_path: "assets/minecraft/textures/block/deepslate.png",
    },
    {
      semantic_id: "minecraft:tuff",
      display_name: "Tuff",
      relative_path: "assets/minecraft/textures/block/tuff.png",
    },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function respondWith(...bodies: readonly unknown[]): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn();
  for (const body of bodies) {
    fetchMock.mockResolvedValueOnce(jsonResponse(body));
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function expectInvalidResponse(operation: () => Promise<unknown>) {
  await expect(operation()).rejects.toMatchObject({
    code: "INVALID_API_RESPONSE",
  } satisfies Partial<ApiRequestError>);
}

describe("strict generation API parsing", () => {
  it("parses schema-3 batches and candidate-to-batch positions", async () => {
    respondWith(jobDetailResponse);

    const detail = await getGenerationJob(projectId, jobId);

    expect(detail.request.executionBatches).toEqual([
      { batchIndex: 0, candidateIndices: [0, 1], seed: 101 },
      { batchIndex: 1, candidateIndices: [2, 3], seed: 202 },
    ]);
    expect(detail.state.candidates.map((candidate) => candidate.positionInBatch)).toEqual(
      [0, 1, 0, 1],
    );
  });

  it.each([
    [
      "missing candidate index from batches",
      {
        ...jobDetailResponse,
        request: {
          ...jobDetailResponse.request,
          execution_batches: [{ batch_index: 0, candidate_indices: [0, 1, 2], seed: 101 }],
        },
      },
    ],
    [
      "duplicate candidate index across batches",
      {
        ...jobDetailResponse,
        request: {
          ...jobDetailResponse.request,
          execution_batches: [
            { batch_index: 0, candidate_indices: [0, 1], seed: 101 },
            { batch_index: 1, candidate_indices: [1, 2], seed: 202 },
          ],
        },
      },
    ],
    [
      "state candidate not aligned to batch assignment",
      {
        ...jobDetailResponse,
        state: {
          ...jobDetailResponse.state,
          candidates: jobDetailResponse.state.candidates.map((candidate, index) =>
            index === 3 ? { ...candidate, batch_index: 0 } : candidate,
          ),
        },
      },
    ],
  ])("rejects schema-3 runtime data with %s", async (_label, invalidResponse) => {
    respondWith(invalidResponse);
    await expectInvalidResponse(() => getGenerationJob(projectId, jobId));
  });

  it("parses generation options, pack references, and upload references", async () => {
    respondWith(
      generationOptionsResponse,
      [
        {
          source: "pack",
          relative_path: "assets/minecraft/textures/block/stone.png",
          display_name: "Stone",
          sha256: "1".repeat(64),
          byte_size: 96,
          width: 16,
          height: 16,
          mode: "RGB",
        },
      ],
      [
        {
          reference_id: "11111111-2222-4333-8444-555555555555",
          kind: "style",
          sha256: "2".repeat(64),
          byte_size: 96,
          width: 16,
          height: 16,
          mode: "RGB",
          created_at: createdAt,
        },
      ],
    );

    const options = await getGenerationOptions(projectId, manifest, coverage);
    const packReferences = await listPackReferences(projectId);
    const uploads = await listUploadedReferences(projectId, "style");

    expect(options.targets.map((target) => target.semanticId)).toEqual([
      "minecraft:deepslate",
      "minecraft:tuff",
    ]);
    expect(packReferences[0]?.source).toBe("pack");
    expect(uploads[0]?.referenceId).toBe(
      "11111111-2222-4333-8444-555555555555",
    );
  });

  it("builds URL-encoded controlled artifact URLs", () => {
    expect(
      readCandidateArtifactUrl(projectId, jobId, 3, "final with space"),
    ).toBe(
      `/api/projects/${projectId}/jobs/${jobId}/candidates/3/artifacts/final%20with%20space`,
    );
  });
});

describe("create and start sequencing", () => {
  it("sends only user-editable create fields and starts after create", async () => {
    const fetchMock = respondWith(jobDetailResponse, jobDetailResponse);

    await createGenerationJob(projectId, createInput);
    await startGenerationJob(projectId, jobId);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      `/api/projects/${projectId}/jobs`,
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target: { semantic_id: "minecraft:deepslate" },
          description: "cold stone",
          negative_prompt: "",
          resolution: 16,
          parallelism: 2,
          references: {
            style: [
              {
                source: "pack",
                relative_path: "assets/minecraft/textures/block/stone.png",
              },
              {
                source: "upload",
                reference_id: "11111111-2222-4333-8444-555555555555",
              },
            ],
            structure: {
              source: "upload",
              reference_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            },
          },
          denoise: 0.45,
          style_weight: 0.8,
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `/api/projects/${projectId}/jobs/${jobId}/start`,
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("omits optional structure and advanced fields when absent", async () => {
    const fetchMock = respondWith(jobDetailResponse);

    await createGenerationJob(projectId, {
      ...createInput,
      styleReferences: [],
      structureReference: null,
      denoise: null,
      styleWeight: null,
    });

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        body: JSON.stringify({
          target: { semantic_id: "minecraft:deepslate" },
          description: "cold stone",
          negative_prompt: "",
          resolution: 16,
          parallelism: 2,
          references: { style: [], structure: null },
          denoise: null,
          style_weight: null,
        }),
      }),
    );
  });

  it("does not start when create fails", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      jsonResponse(
        {
          code: "INVALID_REQUEST",
          stage: "request_validation",
          user_message: "bad request",
          recommended_actions: [],
          technical_details: null,
        },
        422,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(createGenerationJob(projectId, createInput)).rejects.toMatchObject({
      code: "INVALID_REQUEST",
    } satisfies Partial<ApiRequestError>);

    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
