import {
  invalidResponseError,
  parseSuccessfulResponse,
  requestJson,
  type CoverageReport,
  type ProjectManifest,
} from "../api";
import type {
  CreateGenerationInput,
  GenerationJobDetail,
  GenerationOptions,
  PackReferenceRecord,
  ReferenceSelection,
  UploadedReferenceRecord,
} from "./types";

export type { CreateGenerationInput } from "./types";

export async function getGenerationOptions(
  projectId: string,
  _manifest: ProjectManifest,
  _coverage: CoverageReport,
): Promise<GenerationOptions> {
  const payload = await requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/generation-options`,
  );
  return parseSuccessfulResponse(payload, parseGenerationOptions);
}

export async function listPackReferences(
  projectId: string,
): Promise<readonly PackReferenceRecord[]> {
  const payload = await requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/references/pack`,
  );
  try {
    return requireArray(payload).map(parsePackReference);
  } catch (cause) {
    throw invalidResponseError(cause);
  }
}

export async function listUploadedReferences(
  projectId: string,
  kind: "style" | "structure",
): Promise<readonly UploadedReferenceRecord[]> {
  const payload = await requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/references?kind=${encodeURIComponent(kind)}`,
  );
  try {
    return requireArray(payload).map((value) => parseUploadedReference(value, kind));
  } catch (cause) {
    throw invalidResponseError(cause);
  }
}

export async function uploadReference(
  projectId: string,
  kind: "style" | "structure",
  file: File,
): Promise<UploadedReferenceRecord> {
  const payload = await requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/references?kind=${encodeURIComponent(kind)}`,
    {
      method: "POST",
      headers: { "Content-Type": file.type || "image/png" },
      body: file,
    },
  );
  return parseSuccessfulResponse(payload, (data) => parseUploadedReference(data, kind));
}

export async function deleteUploadedReference(
  projectId: string,
  kind: "style" | "structure",
  referenceId: string,
): Promise<void> {
  await requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(kind)}/${encodeURIComponent(referenceId)}`,
    { method: "DELETE" },
    { allowEmptyResponse: true },
  );
}

export async function createGenerationJob(
  projectId: string,
  input: CreateGenerationInput,
): Promise<GenerationJobDetail> {
  const payload = await requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/jobs`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(serializeCreateInput(input)),
    },
  );
  return parseSuccessfulResponse(payload, (data) =>
    parseGenerationJobDetail(data, projectId),
  );
}

export async function startGenerationJob(
  projectId: string,
  jobId: string,
): Promise<GenerationJobDetail> {
  const payload = await requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(jobId)}/start`,
    { method: "POST" },
  );
  return parseSuccessfulResponse(payload, (data) =>
    parseGenerationJobDetail(data, projectId, jobId),
  );
}

export async function getGenerationJob(
  projectId: string,
  jobId: string,
): Promise<GenerationJobDetail> {
  const payload = await requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(jobId)}`,
  );
  return parseSuccessfulResponse(payload, (data) =>
    parseGenerationJobDetail(data, projectId, jobId),
  );
}

export function readCandidateArtifactUrl(
  projectId: string,
  jobId: string,
  candidateIndex: 0 | 1 | 2 | 3,
  artifactKind: string,
): string {
  return `/api/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(jobId)}/candidates/${candidateIndex}/artifacts/${encodeURIComponent(artifactKind)}`;
}

function serializeCreateInput(input: CreateGenerationInput) {
  return {
    target: { semantic_id: input.targetSemanticId },
    description: input.userDescription,
    negative_prompt: input.userNegativePrompt,
    resolution: input.resolution,
    parallelism: input.parallelism,
    references: {
      style: input.styleReferences.map(serializeReferenceSelection),
      structure:
        input.structureReference === null
          ? null
          : serializeReferenceSelection(input.structureReference),
    },
    denoise: input.denoise,
    style_weight: input.styleWeight,
  };
}

function serializeReferenceSelection(selection: ReferenceSelection) {
  return selection.source === "pack"
    ? { source: "pack" as const, relative_path: selection.relativePath }
    : { source: "upload" as const, reference_id: selection.referenceId };
}

function parseGenerationOptions(data: Record<string, unknown>): GenerationOptions {
  const allowedParallelism = requireArray(data.allowed_parallelism).map(
    requireParallelism,
  );
  if (allowedParallelism.join(",") !== "1,2,4") {
    throw new TypeError("Allowed parallelism must stay fixed at 1,2,4");
  }
  if (data.candidate_count !== 4) {
    throw new TypeError("Candidate count must stay fixed at four");
  }
  const defaults = requireRecord(data.defaults);
  const profile = requireRecord(data.profile);
  return {
    candidateCount: 4,
    allowedParallelism: [1, 2, 4],
    defaults: {
      resolution: requireResolution(defaults.resolution),
      parallelism: requireParallelism(defaults.parallelism),
    },
    profile: {
      profileId: requireNonemptyString(profile.profile_id),
      profileVersion: requireNonemptyString(profile.profile_version),
      supportState: requireStringLiteral(
        profile.support_state,
        "candidate_unverified",
        "verified",
      ),
    },
    resourceHints: requireArray(data.resource_hints).map((value) => {
      const hint = requireRecord(value);
      return {
        parallelism: requireParallelism(hint.parallelism),
        peakVramMiB: requireNonnegativeInteger(hint.peak_vram_mib),
        peakProcessRamMiB: requireNonnegativeInteger(
          hint.peak_process_ram_mib,
        ),
        peakSystemRamMiB: requireNonnegativeInteger(
          hint.peak_system_ram_mib,
        ),
        elapsedSeconds: requireFiniteNumber(hint.elapsed_seconds),
      };
    }),
    targets: requireArray(data.targets).map((value) => {
      const target = requireRecord(value);
      return {
        semanticId: requireNonemptyString(target.semantic_id),
        displayName: requireNonemptyString(target.display_name),
        relativePath: requireProjectRelativePath(target.relative_path),
      };
    }),
  };
}

function parsePackReference(value: unknown): PackReferenceRecord {
  const data = requireRecord(value);
  return {
    source: requireStringLiteral(data.source, "pack"),
    relativePath: requireProjectRelativePath(data.relative_path),
    displayName: requireNonemptyString(data.display_name),
    sha256: requireSha256(data.sha256),
    byteSize: requireNonnegativeInteger(data.byte_size),
    width: requireNonnegativeInteger(data.width),
    height: requireNonnegativeInteger(data.height),
    mode: requireStringLiteral(data.mode, "RGB", "RGBA"),
  };
}

function parseUploadedReference(
  value: unknown,
  expectedKind: "style" | "structure",
): UploadedReferenceRecord {
  const data = requireRecord(value);
  return {
    referenceId: requireCanonicalUuid(data.reference_id),
    kind: requireStringLiteral(data.kind, expectedKind),
    sha256: requireSha256(data.sha256),
    byteSize: requireNonnegativeInteger(data.byte_size),
    width: requireNonnegativeInteger(data.width),
    height: requireNonnegativeInteger(data.height),
    mode: requireStringLiteral(data.mode, "RGB", "RGBA"),
    createdAt: requireTimestamp(data.created_at),
  };
}

function parseGenerationJobDetail(
  data: Record<string, unknown>,
  expectedProjectId: string,
  expectedJobId?: string,
): GenerationJobDetail {
  const request = parseGenerationJobRequest(requireRecord(data.request));
  const state = parseGenerationJobState(requireRecord(data.state), request);
  if (request.projectId !== expectedProjectId || state.projectId !== expectedProjectId) {
    throw new TypeError("Generation job project identity does not match");
  }
  if (
    expectedJobId !== undefined &&
    (request.jobId !== expectedJobId || state.jobId !== expectedJobId)
  ) {
    throw new TypeError("Generation job identity does not match");
  }
  return { request, state };
}

function parseGenerationJobRequest(
  data: Record<string, unknown>,
): GenerationJobDetail["request"] {
  const target = requireRecord(data.target);
  const prompt = requireRecord(data.prompt);
  const references = requireRecord(data.references);
  const advanced = requireRecord(data.advanced);
  const modelProfile = requireRecord(data.model_profile);
  const executionBatches = requireArray(data.execution_batches).map((value) => {
    const batch = requireRecord(value);
    return {
      batchIndex: requireNonnegativeInteger(batch.batch_index),
      candidateIndices: requireArray(batch.candidate_indices).map(
        requireCandidateIndex,
      ),
      seed: requireNonnegativeInteger(batch.seed),
    };
  });
  const parallelism = requireParallelism(data.parallelism);
  validateExecutionBatches(executionBatches, parallelism);
  const styleReferences = requireArray(references.style).map(parseFrozenReference);
  const structureReferences = requireArray(references.structure).map(
    parseFrozenReference,
  );
  if (styleReferences.length > 8 || structureReferences.length > 1) {
    throw new TypeError("Frozen reference count exceeds its contract");
  }
  return {
    schemaVersion: requireLiteral3(data.schema_version),
    jobId: requireCanonicalUuid(data.job_id),
    projectId: requireCanonicalUuid(data.project_id),
    parentJobId: requireNullableUuid(data.parent_job_id),
    target: {
      semanticId: requireNonemptyString(target.target_semantic_id),
      displayName: requireNonemptyString(target.target_display_name),
      relativePath: requireProjectRelativePath(target.target_relative_path),
      catalogId: requireNonemptyString(target.catalog_id),
    },
    prompt: {
      promptVersion: requireNonemptyString(prompt.prompt_version),
      positivePrompt: requireNonemptyString(prompt.positive_prompt),
      negativePrompt: requireNonemptyString(prompt.negative_prompt),
      userPrompt: requireString(prompt.user_prompt),
    },
    resolution: requireResolution(data.resolution),
    parallelism,
    executionBatches: executionBatches.map((batch) => ({
      ...batch,
      candidateIndices: [...batch.candidateIndices] as readonly (0 | 1 | 2 | 3)[],
    })),
    references: {
      style: styleReferences,
      structure: structureReferences,
    },
    advanced: {
      styleWeight: requireNullableFiniteNumber(advanced.style_strength),
      denoise: requireNullableFiniteNumber(advanced.denoise_strength),
      loraWeight: requireNullableFiniteNumber(advanced.lora_weight),
    },
    modelProfile: {
      profileId: requireNonemptyString(modelProfile.profile_id),
      profileVersion: requireNonemptyString(modelProfile.profile_version),
      profileManifestSha256: requireSha256(
        modelProfile.profile_manifest_sha256,
      ),
      runtimeId: requireNonemptyString(modelProfile.runtime_id),
      runtimeVersion: requireNonemptyString(modelProfile.runtime_version),
      runtimeManifestSha256: requireSha256(
        modelProfile.runtime_manifest_sha256,
      ),
      workflowVariant: requireStringLiteral(
        modelProfile.workflow_variant,
        "text2img-no-style",
        "text2img-style",
        "img2img-no-style",
        "img2img-style",
      ),
      workflowSha256: requireSha256(modelProfile.workflow_sha256),
      outputNodeId: requireNonemptyString(modelProfile.output_node_id),
    },
    createdAt: requireTimestamp(data.created_at),
  };
}

function parseGenerationJobState(
  data: Record<string, unknown>,
  request: GenerationJobDetail["request"],
): GenerationJobDetail["state"] {
  const batchAssignments = new Map<number, readonly (0 | 1 | 2 | 3)[]>(
    request.executionBatches.map((batch) => [batch.batchIndex, batch.candidateIndices]),
  );
  const batchSeeds = new Map<number, number>(
    request.executionBatches.map((batch) => [batch.batchIndex, batch.seed]),
  );
  const batches = requireArray(data.batches).map((value) => {
    const batch = requireRecord(value);
    return {
      batchIndex: requireNonnegativeInteger(batch.batch_index),
      candidateIndices: requireArray(batch.candidate_indices).map(
        requireCandidateIndex,
      ),
      seed: requireNonnegativeInteger(batch.seed),
      status: requireStringLiteral(
        batch.status,
        "pending",
        "generating",
        "raw_ready",
        "completed",
        "failed",
        "canceled",
      ),
      promptId: requireNullableString(batch.prompt_id),
      rawArtifacts: requireArray(batch.raw_artifacts).map(parseArtifact),
      startedAt: requireNullableTimestamp(batch.started_at),
      finishedAt: requireNullableTimestamp(batch.finished_at),
      failure: parseGenerationFailure(batch.failure),
    };
  });
  if (batches.length !== request.executionBatches.length) {
    throw new TypeError("State batch count does not match the request plan");
  }
  for (const [index, batch] of batches.entries()) {
    const expected = request.executionBatches[index];
    if (
      expected === undefined ||
      batch.batchIndex !== expected.batchIndex ||
      batch.seed !== expected.seed ||
      batch.candidateIndices.join(",") !== expected.candidateIndices.join(",")
    ) {
      throw new TypeError("State batch does not match the request plan");
    }
  }
  const candidates = requireArray(data.candidates);
  if (candidates.length !== 4) {
    throw new TypeError("Expected exactly four candidates");
  }
  const parsedCandidates = candidates.map((value) => {
    const candidate = requireRecord(value);
    return {
      candidateIndex: requireCandidateIndex(candidate.candidate_index),
      batchIndex: requireNonnegativeInteger(candidate.batch_index),
      positionInBatch: requireNonnegativeInteger(candidate.position_in_batch),
      batchSeed: requireNonnegativeInteger(candidate.batch_seed),
      status: requireStringLiteral(
        candidate.status,
        "pending",
        "generating",
        "raw_ready",
        "postprocessing",
        "completed",
        "failed",
        "canceled",
        "inherited",
      ),
      artifacts: parseArtifacts(candidate.artifacts),
      lineage: parseNullableLineage(candidate.lineage),
      failure: parseGenerationFailure(candidate.failure),
      startedAt: requireNullableTimestamp(candidate.started_at),
      finishedAt: requireNullableTimestamp(candidate.finished_at),
    };
  });
  for (let index = 0; index < parsedCandidates.length; index += 1) {
    const candidate = parsedCandidates[index]!;
    if (candidate.candidateIndex !== index) {
      throw new TypeError("Candidates must remain in 0..3 order");
    }
    const expectedIndices = batchAssignments.get(candidate.batchIndex);
    if (expectedIndices === undefined) {
      throw new TypeError("Candidate refers to an unknown batch");
    }
    if (expectedIndices[candidate.positionInBatch] !== candidate.candidateIndex) {
      throw new TypeError("Candidate position does not match its batch plan");
    }
    if (batchSeeds.get(candidate.batchIndex) !== candidate.batchSeed) {
      throw new TypeError("Candidate seed does not match its batch");
    }
  }
  return {
    schemaVersion: requireLiteral2(data.schema_version),
    jobId: requireCanonicalUuid(data.job_id),
    projectId: requireCanonicalUuid(data.project_id),
    revision: requireNonnegativeInteger(data.revision),
    status: requireStringLiteral(
      data.status,
      "queued",
      "generating",
      "postprocessing",
      "completed",
      "failed",
      "canceled",
    ),
    cancelRequestedAt: requireNullableTimestamp(data.cancel_requested_at),
    failure: parseGenerationFailure(data.failure),
    batches,
    candidates: [
      parsedCandidates[0]!,
      parsedCandidates[1]!,
      parsedCandidates[2]!,
      parsedCandidates[3]!,
    ],
    createdAt: requireTimestamp(data.created_at),
    updatedAt: requireTimestamp(data.updated_at),
    startedAt: requireNullableTimestamp(data.started_at),
    finishedAt: requireNullableTimestamp(data.finished_at),
  };
}

function validateExecutionBatches(
  batches: readonly {
    batchIndex: number;
    candidateIndices: readonly (0 | 1 | 2 | 3)[];
    seed: number;
  }[],
  parallelism: 1 | 2 | 4,
): void {
  const expected = {
    1: [[0], [1], [2], [3]],
    2: [[0, 1], [2, 3]],
    4: [[0, 1, 2, 3]],
  }[parallelism];
  if (
    batches.length !== expected.length ||
    batches.some(
      (batch, index) =>
        batch.batchIndex !== index ||
        batch.candidateIndices.join(",") !== expected[index]!.join(","),
    )
  ) {
    throw new TypeError("Execution batches must match the native partition");
  }
}

function parseFrozenReference(value: unknown) {
  const data = requireRecord(value);
  const width = requireNullablePositiveInteger(data.width);
  const height = requireNullablePositiveInteger(data.height);
  if ((width === null) !== (height === null)) {
    throw new TypeError("Reference dimensions must be paired");
  }
  return {
    kind: requireStringLiteral(data.kind, "raw"),
    relativePath: requireProjectRelativePath(data.relative_path),
    sha256: requireSha256(data.sha256),
    byteSize: requireNonnegativeInteger(data.byte_size),
    mediaType: requireNonemptyString(data.media_type),
    width,
    height,
  };
}

function parseArtifacts(value: unknown) {
  const data = requireRecord(value);
  return {
    raw: parseNullableArtifact(data.raw),
    final: parseNullableArtifact(data.final),
    nearest: parseNullableArtifact(data.nearest),
    tile: parseNullableArtifact(data.tile),
    report: parseNullableArtifact(data.report),
  };
}

function parseNullableArtifact(value: unknown) {
  if (value === null) {
    return null;
  }
  return parseArtifact(value);
}

function parseArtifact(value: unknown) {
  const data = requireRecord(value);
  const width = requireNullablePositiveInteger(data.width);
  const height = requireNullablePositiveInteger(data.height);
  if ((width === null) !== (height === null)) {
    throw new TypeError("Artifact dimensions must be paired");
  }
  return {
    kind: requireStringLiteral(
      data.kind,
      "raw",
      "final",
      "nearest",
      "tile",
      "report",
    ),
    relativePath: requireProjectRelativePath(data.relative_path),
    sha256: requireSha256(data.sha256),
    byteSize: requireNonnegativeInteger(data.byte_size),
    mediaType: requireNonemptyString(data.media_type),
    width,
    height,
  };
}

function parseGenerationFailure(value: unknown) {
  if (value === null) {
    return null;
  }
  const data = requireRecord(value);
  return {
    code: requireNonemptyString(data.error_code),
    stage: requireNonemptyString(data.stage),
    userMessage: requireNonemptyString(data.user_message),
    recommendedActions: requireArray(data.recommended_actions).map(
      requireNonemptyString,
    ),
    technicalDetails: requireNullableString(data.technical_details),
    retryable: requireBoolean(data.retryable),
    occurredAt: requireTimestamp(data.occurred_at),
  };
}

function parseNullableLineage(value: unknown) {
  if (value === null) {
    return null;
  }
  const data = requireRecord(value);
  return {
    parentJobId: requireCanonicalUuid(data.parent_job_id),
    parentCandidateIndex: requireCandidateIndex(data.parent_candidate_index),
  };
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("Expected an object response");
  }
  return value as Record<string, unknown>;
}

function requireArray(value: unknown): unknown[] {
  if (!Array.isArray(value)) {
    throw new TypeError("Expected an array response field");
  }
  return value;
}

function requireString(value: unknown): string {
  if (typeof value !== "string") {
    throw new TypeError("Expected a string response field");
  }
  return value;
}

function requireNonemptyString(value: unknown): string {
  const text = requireString(value);
  if (text.length === 0) {
    throw new TypeError("Expected a nonempty string response field");
  }
  return text;
}

function requireProjectRelativePath(value: unknown): string {
  const path = requireString(value);
  if (
    path.length === 0 ||
    path.startsWith("/") ||
    path.endsWith("/") ||
    path.includes("\\") ||
    path.includes("\0")
  ) {
    throw new TypeError("Expected a safe project-relative path");
  }
  for (const segment of path.split("/")) {
    if (
      segment.length === 0 ||
      segment === "." ||
      segment === ".." ||
      /[<>:"|?*]/.test(segment)
    ) {
      throw new TypeError("Expected a safe project-relative path");
    }
  }
  return path;
}

function requireCanonicalUuid(value: unknown): string {
  const uuid = requireString(value);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(uuid)) {
    throw new TypeError("Expected a canonical UUID response field");
  }
  return uuid;
}

function requireNullableUuid(value: unknown): string | null {
  return value === null ? null : requireCanonicalUuid(value);
}

function requireSha256(value: unknown): string {
  const digest = requireString(value);
  if (!/^[0-9a-f]{64}$/.test(digest)) {
    throw new TypeError("Expected a SHA-256 response field");
  }
  return digest;
}

function requireTimestamp(value: unknown): string {
  const timestamp = requireString(value);
  if (!Number.isFinite(Date.parse(timestamp))) {
    throw new TypeError("Expected an RFC3339 timestamp response field");
  }
  return timestamp;
}

function requireNullableTimestamp(value: unknown): string | null {
  return value === null ? null : requireTimestamp(value);
}

function requireNonnegativeInteger(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new TypeError("Expected a nonnegative safe integer response field");
  }
  return value;
}

function requireNullablePositiveInteger(value: unknown): number | null {
  if (value === null) {
    return null;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
    throw new TypeError("Expected a positive safe integer response field");
  }
  return value;
}

function requireFiniteNumber(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TypeError("Expected a finite numeric response field");
  }
  return value;
}

function requireBoolean(value: unknown): boolean {
  if (typeof value !== "boolean") {
    throw new TypeError("Expected a boolean response field");
  }
  return value;
}

function requireNullableFiniteNumber(value: unknown): number | null {
  return value === null ? null : requireFiniteNumber(value);
}

function requireResolution(value: unknown): 16 | 32 | 64 {
  if (value !== 16 && value !== 32 && value !== 64) {
    throw new TypeError("Unsupported texture resolution");
  }
  return value;
}

function requireParallelism(value: unknown): 1 | 2 | 4 {
  if (value !== 1 && value !== 2 && value !== 4) {
    throw new TypeError("Unsupported candidate parallelism");
  }
  return value;
}

function requireStringLiteral<T extends string>(
  value: unknown,
  ...allowed: readonly T[]
): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    throw new TypeError("Unsupported literal response field");
  }
  return value as T;
}

function requireCandidateIndex(value: unknown): 0 | 1 | 2 | 3 {
  if (value !== 0 && value !== 1 && value !== 2 && value !== 3) {
    throw new TypeError("Unsupported candidate index");
  }
  return value;
}

function requireNullableString(value: unknown): string | null {
  return value === null ? null : requireString(value);
}

function requireLiteral3(value: unknown): 3 {
  if (value !== 3) {
    throw new TypeError("Unsupported schema version");
  }
  return 3;
}

function requireLiteral2(value: unknown): 2 {
  if (value !== 2) {
    throw new TypeError("Unsupported schema version");
  }
  return 2;
}
