import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiRequestError,
  getInferenceStatus,
  getInstallPlan,
  getInstallation,
  getJob,
  getProject,
  getRecoveryReport,
  listJobs,
  listProjects,
} from "./api";

const projectId = "6fda5078-1246-4cac-91e8-541808da14f4";
const jobId = "0f6fb74b-5d0f-46b0-bf03-2fb41aa83694";
const retryJobId = "3ec8b782-ec9b-48a6-b949-9ed175122414";
const createdAt = "2026-07-29T10:00:00+08:00";

const projectSummary = {
  project_id: projectId,
  project_name: "恢复项目",
  edition: "java",
  java_pack_format: 34,
  catalog_id: "java-dev-format-34",
  created_at: createdAt,
  updated_at: "2026-07-29T11:00:00+08:00",
};

const projectManifest = {
  schema_version: 2,
  ...projectSummary,
  supported_formats: [34, 35],
  source_sha256: "a".repeat(64),
  default_resolution: 16,
  default_parallelism: 1,
  style_references: [],
};

const jobSummary = {
  job_id: jobId,
  project_id: projectId,
  retry_of_job_id: retryJobId,
  target_semantic_id: "minecraft:deepslate",
  target_display_name: "Deepslate",
  resolution: 32,
  parallelism: 2,
  status: "queued",
  revision: 0,
  candidate_statuses: ["pending", "pending", "pending", "pending"],
  created_at: createdAt,
  updated_at: createdAt,
};

const seeds = [11, 22, 33, 44];
const candidates = seeds.map((seed, candidateIndex) => ({
  candidate_index: candidateIndex,
  seed,
  status: "pending",
  failure: null,
  started_at: null,
  finished_at: null,
}));

const jobDetail = {
  request: {
    schema_version: 1,
    job_id: jobId,
    project_id: projectId,
    retry_of_job_id: retryJobId,
    catalog_id: "java-dev-format-34",
    target_semantic_id: "minecraft:deepslate",
    target_display_name: "Deepslate",
    target_relative_path: "assets/minecraft/textures/block/deepslate.png",
    prompt: "cold blue-gray stone",
    resolution: 32,
    parallelism: 2,
    style_references: ["assets/minecraft/textures/block/stone.png"],
    structure_reference: null,
    seeds,
    created_at: createdAt,
  },
  state: {
    schema_version: 1,
    job_id: jobId,
    project_id: projectId,
    revision: 0,
    status: "queued",
    candidates,
    failure: null,
    created_at: createdAt,
    updated_at: createdAt,
    started_at: null,
    finished_at: null,
  },
};

const recoveryReport = {
  project_count: 1,
  job_count: 1,
  recovered_job_count: 0,
  issues: [],
  completed_at: "2026-07-29T12:00:00Z",
};

const failure = {
  code: "JOB_INTERRUPTED",
  stage: "recovery",
  user_message: "unexpected failure",
  recommended_actions: [],
  technical_details: null,
  log_reference: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function respondWith(body: unknown): void {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(body)));
}

async function expectInvalidResponse(operation: () => Promise<unknown>) {
  await expect(operation()).rejects.toMatchObject({
    code: "INVALID_API_RESPONSE",
  } satisfies Partial<ApiRequestError>);
}

describe("strict project and durable-job API parsing", () => {
  it("parses project summaries without filesystem or source fields", async () => {
    respondWith([projectSummary]);

    await expect(listProjects()).resolves.toEqual([
      {
        projectId,
        projectName: "恢复项目",
        edition: "java",
        javaPackFormat: 34,
        catalogId: "java-dev-format-34",
        createdAt,
        updatedAt: "2026-07-29T11:00:00+08:00",
      },
    ]);
  });

  it.each([
    ["noncanonical UUID", { ...projectSummary, project_id: projectId.toUpperCase() }],
    [
      "unsafe integer",
      { ...projectSummary, java_pack_format: Number.MAX_SAFE_INTEGER + 1 },
    ],
    ["naive timestamp", { ...projectSummary, updated_at: "2026-07-29T11:00:00" }],
    [
      "nonexistent Gregorian date",
      { ...projectSummary, updated_at: "2026-02-30T11:00:00Z" },
    ],
    [
      "24-hour rollover",
      { ...projectSummary, updated_at: "2026-07-29T24:00:00Z" },
    ],
    [
      "invalid numeric offset hour",
      { ...projectSummary, updated_at: "2026-07-29T11:00:00+24:00" },
    ],
    [
      "invalid numeric offset minute",
      { ...projectSummary, updated_at: "2026-07-29T11:00:00+08:60" },
    ],
    ["empty project name", { ...projectSummary, project_name: "" }],
    ["overlong project name", { ...projectSummary, project_name: "x".repeat(129) }],
  ])("rejects a project summary with %s", async (_label, invalidSummary) => {
    respondWith([invalidSummary]);
    await expectInvalidResponse(() => listProjects());
  });

  it("accepts a real Gregorian leap day", async () => {
    const leapDay = "2024-02-29T23:59:59.123456+08:00";
    respondWith([
      {
        ...projectSummary,
        created_at: leapDay,
        updated_at: leapDay,
      },
    ]);

    await expect(listProjects()).resolves.toMatchObject([
      {
        createdAt: leapDay,
        updatedAt: leapDay,
      },
    ]);
  });

  it.each([
    [
      "unsafe manifest style reference",
      { ...projectManifest, style_references: ["../outside.png"] },
    ],
    [
      "overlong manifest project name",
      { ...projectManifest, project_name: "😀".repeat(129) },
    ],
  ])("rejects a project manifest with %s", async (_label, invalidManifest) => {
    respondWith(invalidManifest);
    await expectInvalidResponse(() => getProject(projectId));
  });

  it("rejects a project detail whose ID differs from the requested project", async () => {
    respondWith({
      ...projectManifest,
      project_id: "cd2b33a6-222f-4cd4-b52e-cf6251864d1c",
    });

    await expectInvalidResponse(() => getProject(projectId));
  });

  it("parses deterministic job summaries and retry lineage", async () => {
    respondWith([jobSummary]);

    await expect(listJobs(projectId)).resolves.toEqual([
      {
        jobId,
        projectId,
        retryOfJobId: retryJobId,
        targetSemanticId: "minecraft:deepslate",
        targetDisplayName: "Deepslate",
        resolution: 32,
        parallelism: 2,
        status: "queued",
        revision: 0,
        candidateStatuses: ["pending", "pending", "pending", "pending"],
        createdAt,
        updatedAt: createdAt,
      },
    ]);
  });

  it.each([
    ["unknown state", { ...jobSummary, status: "paused" }],
    ["negative revision", { ...jobSummary, revision: -1 }],
    [
      "wrong candidate count",
      { ...jobSummary, candidate_statuses: ["pending", "pending", "pending"] },
    ],
    ["malformed retry UUID", { ...jobSummary, retry_of_job_id: "not-a-uuid" }],
    ["self retry lineage", { ...jobSummary, retry_of_job_id: jobId }],
  ])("rejects a job summary with %s", async (_label, invalidSummary) => {
    respondWith([invalidSummary]);
    await expectInvalidResponse(() => listJobs(projectId));
  });

  it("parses all four persisted candidates in job detail", async () => {
    respondWith(jobDetail);

    const detail = await getJob(projectId, jobId);

    expect(detail.request.seeds).toEqual([11, 22, 33, 44]);
    expect(detail.state.candidates.map((candidate) => candidate.candidateIndex)).toEqual([
      0, 1, 2, 3,
    ]);
  });

  it.each([
    [
      "wrong seed count",
      {
        ...jobDetail,
        request: { ...jobDetail.request, seeds: [11, 22, 33] },
      },
    ],
    [
      "duplicate seeds",
      {
        ...jobDetail,
        request: { ...jobDetail.request, seeds: [11, 22, 33, 33] },
      },
    ],
    [
      "unsafe seed",
      {
        ...jobDetail,
        request: {
          ...jobDetail.request,
          seeds: [11, 22, 33, Number.MAX_SAFE_INTEGER + 1],
        },
      },
    ],
    [
      "unknown candidate state",
      {
        ...jobDetail,
        state: {
          ...jobDetail.state,
          candidates: [
            { ...candidates[0], status: "paused" },
            ...candidates.slice(1),
          ],
        },
      },
    ],
    [
      "negative state revision",
      {
        ...jobDetail,
        state: { ...jobDetail.state, revision: -1 },
      },
    ],
    [
      "naive state timestamp",
      {
        ...jobDetail,
        state: { ...jobDetail.state, updated_at: "2026-07-29T10:00:00" },
      },
    ],
    [
      "failure data on a nonfailed state",
      {
        ...jobDetail,
        state: {
          ...jobDetail.state,
          failure: {
            code: "JOB_INTERRUPTED",
            stage: "recovery",
            user_message: "unexpected failure",
            recommended_actions: [],
            technical_details: null,
            log_reference: null,
          },
        },
      },
    ],
    [
      "failure data on a nonfailed candidate",
      {
        ...jobDetail,
        state: {
          ...jobDetail.state,
          candidates: [
            {
              ...candidates[0],
              failure: {
                code: "JOB_INTERRUPTED",
                stage: "recovery",
                user_message: "unexpected failure",
                recommended_actions: [],
                technical_details: null,
                log_reference: null,
              },
            },
            ...candidates.slice(1),
          ],
        },
      },
    ],
    [
      "whitespace-only prompt",
      {
        ...jobDetail,
        request: { ...jobDetail.request, prompt: "   " },
      },
    ],
    [
      "overlong prompt",
      {
        ...jobDetail,
        request: { ...jobDetail.request, prompt: "x".repeat(4001) },
      },
    ],
  ])("rejects job detail with %s", async (_label, invalidDetail) => {
    respondWith(invalidDetail);
    await expectInvalidResponse(() => getJob(projectId, jobId));
  });

  it.each([
    ["parent traversal target", "../escape.png", jobDetail.request.style_references, null],
    [
      "backslash target",
      "assets\\minecraft\\textures\\block\\stone.png",
      jobDetail.request.style_references,
      null,
    ],
    [
      "empty target segment",
      "assets//stone.png",
      jobDetail.request.style_references,
      null,
    ],
    [
      "Windows device style reference",
      jobDetail.request.target_relative_path,
      ["assets/minecraft/CON.png"],
      null,
    ],
    [
      "trailing-dot style reference",
      jobDetail.request.target_relative_path,
      ["assets/minecraft/stone./reference.png"],
      null,
    ],
    [
      "absolute structure reference",
      jobDetail.request.target_relative_path,
      jobDetail.request.style_references,
      "/uploads/reference.png",
    ],
  ])(
    "rejects job detail with %s",
    async (_label, targetRelativePath, styleReferences, structureReference) => {
      respondWith({
        ...jobDetail,
        request: {
          ...jobDetail.request,
          target_relative_path: targetRelativePath,
          style_references: styleReferences,
          structure_reference: structureReference,
        },
      });
      await expectInvalidResponse(() => getJob(projectId, jobId));
    },
  );

  it.each([
    "COM¹",
    "com².txt",
    "CoM³.bin",
    "LPT¹",
    "lpt².txt",
    "LpT³.bin",
  ])("rejects the Windows reserved superscript alias %s", async (alias) => {
    respondWith({
      ...jobDetail,
      request: {
        ...jobDetail.request,
        style_references: [`assets/minecraft/${alias}`],
      },
    });

    await expectInvalidResponse(() => getJob(projectId, jobId));
  });

  it.each([
    [
      "pending candidate with a start time",
      { ...candidates[0], started_at: createdAt },
    ],
    [
      "active candidate without a start time",
      { ...candidates[0], status: "generating" },
    ],
    [
      "completed candidate without a finish time",
      { ...candidates[0], status: "completed", started_at: createdAt },
    ],
    [
      "canceled candidate without a finish time",
      { ...candidates[0], status: "canceled" },
    ],
    [
      "candidate finishing before it started",
      {
        ...candidates[0],
        status: "completed",
        started_at: "2026-07-29T10:04:00+08:00",
        finished_at: "2026-07-29T10:03:00+08:00",
      },
    ],
  ])("rejects %s", async (_label, invalidCandidate) => {
    respondWith({
      ...jobDetail,
      state: {
        ...jobDetail.state,
        status: "generating",
        updated_at: "2026-07-29T10:05:00+08:00",
        started_at: createdAt,
        candidates: [invalidCandidate, ...candidates.slice(1)],
      },
    });
    await expectInvalidResponse(() => getJob(projectId, jobId));
  });

  it.each([
    [
      "state updated before creation",
      {
        ...jobDetail.state,
        updated_at: "2026-07-29T09:59:00+08:00",
      },
    ],
    [
      "queued state with lifecycle timestamps",
      { ...jobDetail.state, started_at: createdAt },
    ],
    [
      "active state without a start time",
      { ...jobDetail.state, status: "generating" },
    ],
    [
      "completed state without a finish time",
      {
        ...jobDetail.state,
        status: "completed",
        started_at: createdAt,
      },
    ],
    [
      "canceled state without a finish time",
      { ...jobDetail.state, status: "canceled" },
    ],
    [
      "state finishing before it started",
      {
        ...jobDetail.state,
        status: "canceled",
        updated_at: "2026-07-29T10:05:00+08:00",
        started_at: "2026-07-29T10:04:00+08:00",
        finished_at: "2026-07-29T10:03:00+08:00",
        candidates: candidates.map((candidate) => ({
          ...candidate,
          status: "canceled",
          finished_at: "2026-07-29T10:03:00+08:00",
        })),
      },
    ],
    [
      "queued state containing a nonpending candidate",
      {
        ...jobDetail.state,
        candidates: [
          {
            ...candidates[0],
            status: "generating",
            started_at: createdAt,
          },
          ...candidates.slice(1),
        ],
      },
    ],
    [
      "completed state containing pending candidates",
      {
        ...jobDetail.state,
        status: "completed",
        updated_at: "2026-07-29T10:05:00+08:00",
        started_at: createdAt,
        finished_at: "2026-07-29T10:05:00+08:00",
      },
    ],
    [
      "terminal state containing an active candidate",
      {
        ...jobDetail.state,
        status: "failed",
        failure,
        updated_at: "2026-07-29T10:05:00+08:00",
        started_at: createdAt,
        finished_at: "2026-07-29T10:05:00+08:00",
        candidates: [
          {
            ...candidates[0],
            status: "generating",
            started_at: createdAt,
          },
          ...candidates.slice(1),
        ],
      },
    ],
    [
      "candidate timestamp outside the job lifetime",
      {
        ...jobDetail.state,
        status: "generating",
        updated_at: "2026-07-29T10:05:00+08:00",
        started_at: createdAt,
        candidates: [
          {
            ...candidates[0],
            status: "generating",
            started_at: "2026-07-29T09:59:00+08:00",
          },
          ...candidates.slice(1),
        ],
      },
    ],
  ])("rejects %s", async (_label, invalidState) => {
    respondWith({ ...jobDetail, state: invalidState });
    await expectInvalidResponse(() => getJob(projectId, jobId));
  });

  it("parses a path-free recovery report", async () => {
    respondWith({
      ...recoveryReport,
      issues: [
        {
          project_id: projectId,
          job_id: jobId,
          code: "CORRUPT_JOB_RECORD",
          user_message: "任务记录损坏",
        },
      ],
    });

    const report = await getRecoveryReport();

    expect(report.issues[0]).toEqual({
      projectId,
      jobId,
      code: "CORRUPT_JOB_RECORD",
      userMessage: "任务记录损坏",
    });
  });

  it.each([
    ["negative count", { ...recoveryReport, job_count: -1 }],
    [
      "unsafe count",
      { ...recoveryReport, project_count: Number.MAX_SAFE_INTEGER + 1 },
    ],
    [
      "naive completion timestamp",
      { ...recoveryReport, completed_at: "2026-07-29T12:00:00" },
    ],
  ])("rejects a recovery report with %s", async (_label, invalidReport) => {
    respondWith(invalidReport);
    await expectInvalidResponse(() => getRecoveryReport());
  });
});

describe("strict inference setup API parsing", () => {
  const timestamp = "2026-08-02T00:00:00Z";
  const digest = "a".repeat(64);
  const sha = "b".repeat(64);
  const inferenceStatus = {
    environment: {
      supported: true,
      platform: "windows",
      architecture: "x86_64",
      gpu_vendor: "nvidia",
      gpu_name: "RTX 4060",
      driver_version: "552.44",
      vram_bytes: 8589934592,
      disk_free_bytes: 1000000000000,
      blocking_issues: [],
    },
    runtime: { state: "missing", selected_version: null, error: null },
    profile: {
      profile_id: "sdxl-mapchip-ipadapter",
      profile_version: "1",
      support_state: "candidate_unverified",
      components: [
        { artifact_id: "checkpoint", state: "missing", installed_bytes: null },
      ],
      ready: false,
    },
    process: { state: "stopped", pid: null, version: null, errors: [] },
  };
  const plan = {
    runtime_id: "comfyui-windows-nvidia",
    runtime_version: "0.29.2",
    profile_id: "sdxl-mapchip-ipadapter",
    profile_version: "1",
    plan_digest: digest,
    components: [
      {
        artifact_id: "checkpoint",
        source_url: "https://example.com/source",
        revision: "r1",
        byte_size: 1500000000,
        sha256: sha,
        destination: "models/checkpoints/x.safetensors",
        license_name: "Apache-2.0",
        license_source_url: "https://example.com/license",
        state: "missing",
      },
    ],
    total_download_bytes: 1500000000,
    temporary_headroom_bytes: 8000000000,
    required_free_bytes: 9500000000,
    disk_free_bytes: 1000000000000,
    can_install: true,
    blockers: [],
  };
  const operation = {
    operation_id: projectId,
    runtime_id: "comfyui-windows-nvidia",
    profile_id: "sdxl-mapchip-ipadapter",
    plan_digest: digest,
    accepted_component_ids: ["checkpoint"],
    state: "completed",
    revision: 2,
    created_at: timestamp,
    updated_at: timestamp,
    error: null,
  };

  it("parses a valid inference status and install plan", async () => {
    respondWith(inferenceStatus);
    await expect(getInferenceStatus()).resolves.toMatchObject({
      environment: { gpuName: "RTX 4060" },
      process: { state: "stopped" },
    });
    respondWith(plan);
    const parsedPlan = await getInstallPlan();
    expect(parsedPlan.totalDownloadBytes).toBe(1500000000);
    expect(parsedPlan.components[0].licenseName).toBe("Apache-2.0");
  });

  it.each([
    ["unknown process state", { ...inferenceStatus, process: { ...inferenceStatus.process, state: "paused" } }],
    ["unknown runtime state", { ...inferenceStatus, runtime: { state: "unknown" } }],
    [
      "unsafe byte size",
      {
        ...inferenceStatus,
        environment: {
          ...inferenceStatus.environment,
          vram_bytes: Number.MAX_SAFE_INTEGER + 1,
        },
      },
    ],
  ])("rejects inference status with %s", async (_label, invalidStatus) => {
    respondWith(invalidStatus);
    await expectInvalidResponse(() => getInferenceStatus());
  });

  it.each([
    [
      "malformed sha256",
      {
        ...plan,
        components: [{ ...plan.components[0], sha256: "xyz" }],
      },
    ],
    [
      "non-http source URL",
      {
        ...plan,
        components: [{ ...plan.components[0], source_url: "file:///x" }],
      },
    ],
    [
      "absolute destination",
      {
        ...plan,
        components: [
          { ...plan.components[0], destination: "C:\\models\\x.safetensors" },
        ],
      },
    ],
    [
      "empty license name",
      {
        ...plan,
        components: [
          plan.components[0],
          {
            ...plan.components[0],
            artifact_id: "second",
            license_name: "",
            byte_size: 1,
            destination: "models/loras/y.safetensors",
          },
        ],
      },
    ],
    [
      "unsafe total size",
      { ...plan, total_download_bytes: Number.MAX_SAFE_INTEGER + 1 },
    ],
  ])("rejects install plan with %s", async (_label, invalidPlan) => {
    respondWith(invalidPlan);
    await expectInvalidResponse(() => getInstallPlan());
  });

  it("allows the same license record to be shared by components", async () => {
    respondWith({
      ...plan,
      components: [
        plan.components[0],
        {
          ...plan.components[0],
          artifact_id: "second",
          byte_size: 1,
          destination: "models/loras/y.safetensors",
        },
      ],
    });

    const parsed = await getInstallPlan();
    expect(parsed.components).toHaveLength(2);
  });

  it("parses a terminal installation operation", async () => {
    respondWith(operation);
    const parsed = await getInstallation(projectId);
    expect(parsed.state).toBe("completed");
    expect(parsed.revision).toBe(2);
  });

  it.each([
    ["unknown installation state", { ...operation, state: "paused" }],
    [
      "malformed operation id",
      { ...operation, operation_id: "not-a-uuid" },
    ],
  ])("rejects installation operation with %s", async (_label, invalidOperation) => {
    respondWith(invalidOperation);
    await expectInvalidResponse(() => getInstallation(projectId));
  });
});
