import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiRequestError,
  getJob,
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
  ])("rejects a project summary with %s", async (_label, invalidSummary) => {
    respondWith([invalidSummary]);
    await expectInvalidResponse(() => listProjects());
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
  ])("rejects job detail with %s", async (_label, invalidDetail) => {
    respondWith(invalidDetail);
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
