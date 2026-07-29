import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import JobHistory from "./JobHistory";
import type { JobDetail, JobSummary } from "./api";

afterEach(cleanup);

const projectId = "6fda5078-1246-4cac-91e8-541808da14f4";
const jobId = "0f6fb74b-5d0f-46b0-bf03-2fb41aa83694";
const retryJobId = "3ec8b782-ec9b-48a6-b949-9ed175122414";
const createdAt = "2026-07-29T10:00:00+08:00";
const finishedAt = "2026-07-29T10:03:00+08:00";

const summary: JobSummary = {
  jobId,
  projectId,
  retryOfJobId: retryJobId,
  targetSemanticId: "minecraft:deepslate",
  targetDisplayName: "Deepslate",
  resolution: 32,
  parallelism: 2,
  status: "failed",
  revision: 3,
  candidateStatuses: ["completed", "failed", "canceled", "canceled"],
  createdAt,
  updatedAt: finishedAt,
};

const detail: JobDetail = {
  request: {
    schemaVersion: 1,
    jobId,
    projectId,
    retryOfJobId: retryJobId,
    catalogId: "java-dev-format-34",
    targetSemanticId: "minecraft:deepslate",
    targetDisplayName: "Deepslate",
    targetRelativePath: "assets/minecraft/textures/block/deepslate.png",
    prompt: "cold blue-gray stone",
    resolution: 32,
    parallelism: 2,
    styleReferences: ["assets/minecraft/textures/block/stone.png"],
    structureReference: null,
    seeds: [11, 22, 33, 44],
    createdAt,
  },
  state: {
    schemaVersion: 1,
    jobId,
    projectId,
    revision: 3,
    status: "failed",
    candidates: [
      {
        candidateIndex: 0,
        seed: 11,
        status: "completed",
        failure: null,
        startedAt: createdAt,
        finishedAt,
      },
      {
        candidateIndex: 1,
        seed: 22,
        status: "failed",
        failure: {
          code: "JOB_INTERRUPTED",
          stage: "recovery",
          userMessage: "应用重启时任务仍在运行",
          recommendedActions: ["检查已完成候选后重试任务"],
          technicalDetails: null,
          logReference: null,
        },
        startedAt: createdAt,
        finishedAt,
      },
      {
        candidateIndex: 2,
        seed: 33,
        status: "canceled",
        failure: null,
        startedAt: null,
        finishedAt,
      },
      {
        candidateIndex: 3,
        seed: 44,
        status: "canceled",
        failure: null,
        startedAt: null,
        finishedAt,
      },
    ],
    failure: {
      code: "JOB_INTERRUPTED",
      stage: "recovery",
      userMessage: "应用重启时任务仍在运行",
      recommendedActions: ["检查已完成候选后重试任务"],
      technicalDetails: null,
      logReference: null,
    },
    createdAt,
    updatedAt: finishedAt,
    startedAt: createdAt,
    finishedAt,
  },
};

it("shows read-only job facts, four candidate counts, and retry lineage", () => {
  render(<JobHistory jobs={[{ summary, detail }]} />);

  const row = screen.getByRole("article", { name: /Deepslate/ });
  expect(row).toHaveTextContent("minecraft:deepslate");
  expect(row).toHaveTextContent("32 × 32");
  expect(row).toHaveTextContent("并行 2");
  expect(row).toHaveTextContent("候选 4");
  expect(row).toHaveTextContent("完成 1");
  expect(row).toHaveTextContent("失败 1");
  expect(row).toHaveTextContent("已取消 2");
  expect(row).toHaveTextContent(retryJobId);
  expect(row.querySelector("time")).toHaveAttribute("dateTime", finishedAt);
});

it("explains JOB_INTERRUPTED without adding mutation controls", () => {
  render(<JobHistory jobs={[{ summary, detail }]} />);

  expect(screen.getByText(/应用重启时此任务仍在运行/)).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("clearly states that an empty project has no generation jobs", () => {
  render(<JobHistory jobs={[]} />);

  expect(screen.getByText("当前项目还没有生成任务。")).toBeInTheDocument();
});
