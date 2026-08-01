export interface ProjectManifest {
  schemaVersion: 2;
  projectId: string;
  projectName: string;
  edition: "java";
  javaPackFormat: number;
  supportedFormats: readonly [number, number] | null;
  catalogId: string;
  sourceSha256: string;
  createdAt: string;
  updatedAt: string;
  defaultResolution: 16 | 32 | 64;
  defaultParallelism: 1 | 2 | 4;
  styleReferences: readonly string[];
}

export interface ProjectSummary {
  projectId: string;
  projectName: string;
  edition: "java";
  javaPackFormat: number;
  catalogId: string;
  createdAt: string;
  updatedAt: string;
}

export type JobStatus =
  | "queued"
  | "generating"
  | "postprocessing"
  | "completed"
  | "failed"
  | "canceled";

export type CandidateStatus =
  | "pending"
  | "generating"
  | "postprocessing"
  | "completed"
  | "failed"
  | "canceled";

export interface JobFailure {
  code: string;
  stage: string;
  userMessage: string;
  recommendedActions: readonly string[];
  technicalDetails: string | null;
  logReference: string | null;
}

export interface CandidateRecord {
  candidateIndex: 0 | 1 | 2 | 3;
  seed: number;
  status: CandidateStatus;
  failure: JobFailure | null;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface JobSummary {
  jobId: string;
  projectId: string;
  retryOfJobId: string | null;
  targetSemanticId: string;
  targetDisplayName: string;
  resolution: 16 | 32 | 64;
  parallelism: 1 | 2 | 4;
  status: JobStatus;
  revision: number;
  candidateStatuses: readonly [
    CandidateStatus,
    CandidateStatus,
    CandidateStatus,
    CandidateStatus,
  ];
  createdAt: string;
  updatedAt: string;
}

export interface JobRequest {
  schemaVersion: 1;
  jobId: string;
  projectId: string;
  retryOfJobId: string | null;
  catalogId: string;
  targetSemanticId: string;
  targetDisplayName: string;
  targetRelativePath: string;
  prompt: string;
  resolution: 16 | 32 | 64;
  parallelism: 1 | 2 | 4;
  styleReferences: readonly string[];
  structureReference: string | null;
  seeds: readonly [number, number, number, number];
  createdAt: string;
}

export interface JobStateRecord {
  schemaVersion: 1;
  jobId: string;
  projectId: string;
  revision: number;
  status: JobStatus;
  candidates: readonly [
    CandidateRecord,
    CandidateRecord,
    CandidateRecord,
    CandidateRecord,
  ];
  failure: JobFailure | null;
  createdAt: string;
  updatedAt: string;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface JobDetail {
  request: JobRequest;
  state: JobStateRecord;
}

export interface RecoveryIssue {
  projectId: string;
  jobId: string | null;
  code: string;
  userMessage: string;
}

export interface RecoveryReport {
  projectCount: number;
  jobCount: number;
  recoveredJobCount: number;
  issues: readonly RecoveryIssue[];
  completedAt: string;
}

export interface CoverageItem {
  semanticId: string;
  displayName: string;
  relativePath: string;
  mvpEligible: boolean;
  status: "covered" | "missing";
}

export interface CoverageReport {
  catalogId: string;
  catalogStatus: "development_fixture" | "production";
  coveredCount: number;
  missingCount: number;
  unknownPaths: readonly string[];
  items: readonly CoverageItem[];
}

export interface ApiError {
  code: string;
  stage: string;
  userMessage: string;
  recommendedActions: readonly string[];
  technicalDetails: string | null;
}

export class ApiRequestError extends Error implements ApiError {
  readonly code: string;
  readonly stage: string;
  readonly userMessage: string;
  readonly recommendedActions: readonly string[];
  readonly technicalDetails: string | null;

  constructor(error: ApiError) {
    super(error.userMessage);
    this.name = "ApiRequestError";
    this.code = error.code;
    this.stage = error.stage;
    this.userMessage = error.userMessage;
    this.recommendedActions = error.recommendedActions;
    this.technicalDetails = error.technicalDetails;
  }
}

export const MAX_PROJECT_NAME_LENGTH = 128;
const MAX_PROMPT_CODE_POINTS = 4000;

export async function importProject(
  projectName: string,
  pack: File,
): Promise<ProjectManifest> {
  const form = new FormData();
  form.append("project_name", projectName);
  form.append("pack", pack);

  const payload = await requestJson("/api/projects/import", {
    method: "POST",
    body: form,
  });
  return parseSuccessfulResponse(payload, parseProjectManifest);
}

export async function listProjects(): Promise<readonly ProjectSummary[]> {
  const payload = await requestJson("/api/projects");
  return parseSuccessfulValue(payload, (value) =>
    requireArray(value).map(parseProjectSummary),
  );
}

export async function getProject(projectId: string): Promise<ProjectManifest> {
  const payload = await requestJson(
    `/api/projects/${encodeURIComponent(projectId)}`,
  );
  return parseSuccessfulResponse(payload, (data) => {
    const manifest = parseProjectManifest(data);
    if (manifest.projectId !== projectId) {
      throw new TypeError("Project detail identity does not match the request");
    }
    return manifest;
  });
}

export async function getCoverage(projectId: string): Promise<CoverageReport> {
  const payload = await requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/coverage`,
  );
  return parseSuccessfulResponse(payload, (data): CoverageReport => ({
    catalogId: requireString(data.catalog_id),
    catalogStatus: requireCatalogStatus(data.catalog_status),
    coveredCount: requireNonnegativeInteger(data.covered_count),
    missingCount: requireNonnegativeInteger(data.missing_count),
    unknownPaths: requireArray(data.unknown_paths).map(requireString),
    items: requireArray(data.items).map(parseCoverageItem),
  }));
}

export async function listJobs(
  projectId: string,
): Promise<readonly JobSummary[]> {
  const payload = await requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/jobs`,
  );
  return parseSuccessfulValue(payload, (value) =>
    requireArray(value).map(parseJobSummary),
  );
}

export async function getJob(
  projectId: string,
  jobId: string,
): Promise<JobDetail> {
  const payload = await requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(jobId)}`,
  );
  return parseSuccessfulResponse(payload, (data) =>
    parseJobDetail(data, projectId, jobId),
  );
}

export async function getRecoveryReport(): Promise<RecoveryReport> {
  const payload = await requestJson("/api/system/recovery");
  return parseSuccessfulResponse(payload, parseRecoveryReport);
}

async function requestJson(url: string, init?: RequestInit): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (cause) {
    throw new ApiRequestError({
      code: "NETWORK_ERROR",
      stage: "requesting",
      userMessage: "无法连接到本地服务",
      recommendedActions: ["确认 FastAPI 服务已经启动，然后重试"],
      technicalDetails: cause instanceof Error ? cause.message : null,
    });
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch (cause) {
    throw invalidResponseError(cause);
  }

  if (!response.ok) {
    throw parseApiError(payload);
  }
  return payload;
}

function parseApiError(payload: unknown): ApiRequestError {
  try {
    const data = requireRecord(payload);
    const technicalDetails = data.technical_details;
    return new ApiRequestError({
      code: requireString(data.code),
      stage: requireString(data.stage),
      userMessage: requireString(data.user_message),
      recommendedActions: requireArray(data.recommended_actions).map(requireString),
      technicalDetails:
        technicalDetails === null ? null : requireString(technicalDetails),
    });
  } catch (cause) {
    if (cause instanceof ApiRequestError) {
      return cause;
    }
    return invalidResponseError(cause);
  }
}

export function invalidResponseError(cause: unknown): ApiRequestError {
  return new ApiRequestError({
    code: "INVALID_API_RESPONSE",
    stage: "requesting",
    userMessage: "本地服务返回了无法识别的响应",
    recommendedActions: ["重试操作；若问题持续，请查看应用日志"],
    technicalDetails: cause instanceof Error ? cause.message : null,
  });
}

function parseSuccessfulResponse<T>(
  payload: unknown,
  parse: (data: Record<string, unknown>) => T,
): T {
  try {
    return parse(requireRecord(payload));
  } catch (cause) {
    if (cause instanceof ApiRequestError) {
      throw cause;
    }
    throw invalidResponseError(cause);
  }
}

function parseSuccessfulValue<T>(
  payload: unknown,
  parse: (value: unknown) => T,
): T {
  try {
    return parse(payload);
  } catch (cause) {
    if (cause instanceof ApiRequestError) {
      throw cause;
    }
    throw invalidResponseError(cause);
  }
}

function parseProjectManifest(data: Record<string, unknown>): ProjectManifest {
  return {
    schemaVersion: requireLiteral(data.schema_version, 2),
    projectId: requireCanonicalUuid(data.project_id),
    projectName: requireBoundedCodePointString(
      data.project_name,
      1,
      MAX_PROJECT_NAME_LENGTH,
    ),
    edition: requireJava(data.edition),
    javaPackFormat: requireNonnegativeInteger(data.java_pack_format),
    supportedFormats: parseSupportedFormats(data.supported_formats),
    catalogId: requireString(data.catalog_id),
    sourceSha256: requireSha256(data.source_sha256),
    createdAt: requireTimestamp(data.created_at),
    updatedAt: requireTimestamp(data.updated_at),
    defaultResolution: requireResolution(data.default_resolution),
    defaultParallelism: requireParallelism(data.default_parallelism),
    styleReferences: requireBoundedStringArray(
      data.style_references,
      0,
      8,
    ).map(requireProjectRelativePath),
  };
}

function parseProjectSummary(value: unknown): ProjectSummary {
  const data = requireRecord(value);
  return {
    projectId: requireCanonicalUuid(data.project_id),
    projectName: requireBoundedCodePointString(
      data.project_name,
      1,
      MAX_PROJECT_NAME_LENGTH,
    ),
    edition: requireJava(data.edition),
    javaPackFormat: requireNonnegativeInteger(data.java_pack_format),
    catalogId: requireString(data.catalog_id),
    createdAt: requireTimestamp(data.created_at),
    updatedAt: requireTimestamp(data.updated_at),
  };
}

function parseCoverageItem(value: unknown): CoverageItem {
  const data = requireRecord(value);
  return {
    semanticId: requireString(data.semantic_id),
    displayName: requireString(data.display_name),
    relativePath: requireString(data.relative_path),
    mvpEligible: requireBoolean(data.mvp_eligible),
    status: requireCoverageStatus(data.status),
  };
}

function parseJobSummary(value: unknown): JobSummary {
  const data = requireRecord(value);
  const jobId = requireCanonicalUuid(data.job_id);
  const retryOfJobId = requireNullableUuid(data.retry_of_job_id);
  if (retryOfJobId === jobId) {
    throw new TypeError("A job cannot retry itself");
  }
  return {
    jobId,
    projectId: requireCanonicalUuid(data.project_id),
    retryOfJobId,
    targetSemanticId: requireNonemptyString(data.target_semantic_id),
    targetDisplayName: requireNonemptyString(data.target_display_name),
    resolution: requireResolution(data.resolution),
    parallelism: requireParallelism(data.parallelism),
    status: requireJobStatus(data.status),
    revision: requireNonnegativeInteger(data.revision),
    candidateStatuses: parseFourCandidateStatuses(data.candidate_statuses),
    createdAt: requireTimestamp(data.created_at),
    updatedAt: requireTimestamp(data.updated_at),
  };
}

function parseJobDetail(
  data: Record<string, unknown>,
  expectedProjectId: string,
  expectedJobId: string,
): JobDetail {
  const request = parseJobRequest(data.request);
  const state = parseJobState(data.state);
  if (
    request.projectId !== expectedProjectId ||
    state.projectId !== expectedProjectId ||
    request.jobId !== expectedJobId ||
    state.jobId !== expectedJobId
  ) {
    throw new TypeError("Job detail identity does not match the request");
  }
  for (let index = 0; index < 4; index += 1) {
    const candidate = state.candidates[index];
    if (
      candidate.candidateIndex !== index ||
      candidate.seed !== request.seeds[index]
    ) {
      throw new TypeError("Job candidates do not match the persisted seeds");
    }
  }
  return { request, state };
}

function parseJobRequest(value: unknown): JobRequest {
  const data = requireRecord(value);
  const jobId = requireCanonicalUuid(data.job_id);
  const retryOfJobId = requireNullableUuid(data.retry_of_job_id);
  if (retryOfJobId === jobId) {
    throw new TypeError("A job cannot retry itself");
  }
  const styleReferences = requireBoundedStringArray(
    data.style_references,
    1,
    8,
  ).map(requireProjectRelativePath);
  const structureReference =
    data.structure_reference === null
      ? null
      : requireProjectRelativePath(data.structure_reference);
  return {
    schemaVersion: requireLiteral(data.schema_version, 1),
    jobId,
    projectId: requireCanonicalUuid(data.project_id),
    retryOfJobId,
    catalogId: requireNonemptyString(data.catalog_id),
    targetSemanticId: requireNonemptyString(data.target_semantic_id),
    targetDisplayName: requireNonemptyString(data.target_display_name),
    targetRelativePath: requireProjectRelativePath(data.target_relative_path),
    prompt: requirePrompt(data.prompt),
    resolution: requireResolution(data.resolution),
    parallelism: requireParallelism(data.parallelism),
    styleReferences,
    structureReference,
    seeds: parseFourSeeds(data.seeds),
    createdAt: requireTimestamp(data.created_at),
  };
}

function parseJobState(value: unknown): JobStateRecord {
  const data = requireRecord(value);
  const status = requireJobStatus(data.status);
  const failure = parseNullableFailure(data.failure);
  requireFailureConsistency(status, failure);
  const state: JobStateRecord = {
    schemaVersion: requireLiteral(data.schema_version, 1),
    jobId: requireCanonicalUuid(data.job_id),
    projectId: requireCanonicalUuid(data.project_id),
    revision: requireNonnegativeInteger(data.revision),
    status,
    candidates: parseFourCandidates(data.candidates),
    failure,
    createdAt: requireTimestamp(data.created_at),
    updatedAt: requireTimestamp(data.updated_at),
    startedAt: requireNullableTimestamp(data.started_at),
    finishedAt: requireNullableTimestamp(data.finished_at),
  };
  validateJobLifecycle(state);
  return state;
}

function parseFourCandidates(
  value: unknown,
): JobStateRecord["candidates"] {
  const items = requireArray(value);
  if (items.length !== 4) {
    throw new TypeError("Expected exactly four candidate records");
  }
  return [
    parseCandidate(items[0]),
    parseCandidate(items[1]),
    parseCandidate(items[2]),
    parseCandidate(items[3]),
  ];
}

function parseCandidate(value: unknown): CandidateRecord {
  const data = requireRecord(value);
  const status = requireCandidateStatus(data.status);
  const failure = parseNullableFailure(data.failure);
  requireFailureConsistency(status, failure);
  const candidate: CandidateRecord = {
    candidateIndex: requireCandidateIndex(data.candidate_index),
    seed: requireNonnegativeInteger(data.seed),
    status,
    failure,
    startedAt: requireNullableTimestamp(data.started_at),
    finishedAt: requireNullableTimestamp(data.finished_at),
  };
  validateCandidateLifecycle(candidate);
  return candidate;
}

function validateCandidateLifecycle(candidate: CandidateRecord): void {
  const { status, startedAt, finishedAt } = candidate;
  if (status === "pending") {
    if (startedAt !== null || finishedAt !== null) {
      throw new TypeError("Pending candidate has lifecycle timestamps");
    }
  } else if (status === "generating" || status === "postprocessing") {
    if (startedAt === null || finishedAt !== null) {
      throw new TypeError("Active candidate lifecycle is inconsistent");
    }
  } else if (status === "completed") {
    if (startedAt === null || finishedAt === null) {
      throw new TypeError("Completed candidate lifecycle is incomplete");
    }
  } else if (finishedAt === null) {
    throw new TypeError("Terminal candidate has no finish timestamp");
  }
  if (
    startedAt !== null &&
    finishedAt !== null &&
    timestampValue(finishedAt) < timestampValue(startedAt)
  ) {
    throw new TypeError("Candidate finished before it started");
  }
}

function validateJobLifecycle(state: JobStateRecord): void {
  const createdAt = timestampValue(state.createdAt);
  const updatedAt = timestampValue(state.updatedAt);
  const startedAt =
    state.startedAt === null ? null : timestampValue(state.startedAt);
  const finishedAt =
    state.finishedAt === null ? null : timestampValue(state.finishedAt);
  if (updatedAt < createdAt) {
    throw new TypeError("Job updated before it was created");
  }
  for (const timestamp of [startedAt, finishedAt]) {
    if (timestamp !== null && (timestamp < createdAt || timestamp > updatedAt)) {
      throw new TypeError("Job lifecycle timestamp is out of range");
    }
  }
  if (
    startedAt !== null &&
    finishedAt !== null &&
    finishedAt < startedAt
  ) {
    throw new TypeError("Job finished before it started");
  }

  if (state.status === "queued") {
    if (startedAt !== null || finishedAt !== null) {
      throw new TypeError("Queued job has lifecycle timestamps");
    }
  } else if (
    state.status === "generating" ||
    state.status === "postprocessing"
  ) {
    if (startedAt === null || finishedAt !== null) {
      throw new TypeError("Active job lifecycle is inconsistent");
    }
  } else if (state.status === "completed" || state.status === "failed") {
    if (startedAt === null || finishedAt === null) {
      throw new TypeError("Completed or failed job lifecycle is incomplete");
    }
  } else if (finishedAt === null) {
    throw new TypeError("Canceled job has no finish timestamp");
  }

  const candidateStatuses = state.candidates.map(
    (candidate) => candidate.status,
  );
  if (
    state.status === "queued" &&
    candidateStatuses.some((status) => status !== "pending")
  ) {
    throw new TypeError("Queued job has a nonpending candidate");
  }
  if (
    state.status === "completed" &&
    candidateStatuses.some((status) => status !== "completed")
  ) {
    throw new TypeError("Completed job has an incomplete candidate");
  }
  if (
    isTerminalJobStatus(state.status) &&
    candidateStatuses.some((status) => !isTerminalCandidateStatus(status))
  ) {
    throw new TypeError("Terminal job has an active candidate");
  }

  for (const candidate of state.candidates) {
    for (const value of [candidate.startedAt, candidate.finishedAt]) {
      if (value === null) {
        continue;
      }
      const timestamp = timestampValue(value);
      if (timestamp < createdAt || timestamp > updatedAt) {
        throw new TypeError("Candidate timestamp is outside the job lifetime");
      }
      if (startedAt !== null && timestamp < startedAt) {
        throw new TypeError("Candidate timestamp precedes the job lifecycle");
      }
      if (finishedAt !== null && timestamp > finishedAt) {
        throw new TypeError("Candidate timestamp exceeds the job lifecycle");
      }
    }
  }
}

function isTerminalJobStatus(status: JobStatus): boolean {
  return status === "completed" || status === "failed" || status === "canceled";
}

function isTerminalCandidateStatus(status: CandidateStatus): boolean {
  return status === "completed" || status === "failed" || status === "canceled";
}

function timestampValue(timestamp: string): number {
  return Date.parse(timestamp);
}

function parseNullableFailure(value: unknown): JobFailure | null {
  if (value === null) {
    return null;
  }
  const data = requireRecord(value);
  return {
    code: requireNonemptyString(data.code),
    stage: requireNonemptyString(data.stage),
    userMessage: requireNonemptyString(data.user_message),
    recommendedActions: requireBoundedStringArray(
      data.recommended_actions,
      0,
      Number.MAX_SAFE_INTEGER,
    ),
    technicalDetails: requireNullableString(data.technical_details),
    logReference: requireNullableString(data.log_reference),
  };
}

function requireFailureConsistency(
  status: JobStatus | CandidateStatus,
  failure: JobFailure | null,
): void {
  if ((status === "failed") !== (failure !== null)) {
    throw new TypeError("Failure data does not match the record status");
  }
}

function parseRecoveryReport(data: Record<string, unknown>): RecoveryReport {
  return {
    projectCount: requireNonnegativeInteger(data.project_count),
    jobCount: requireNonnegativeInteger(data.job_count),
    recoveredJobCount: requireNonnegativeInteger(data.recovered_job_count),
    issues: requireArray(data.issues).map(parseRecoveryIssue),
    completedAt: requireTimestamp(data.completed_at),
  };
}

function parseRecoveryIssue(value: unknown): RecoveryIssue {
  const data = requireRecord(value);
  return {
    projectId: requireCanonicalUuid(data.project_id),
    jobId: requireNullableUuid(data.job_id),
    code: requireNonemptyString(data.code),
    userMessage: requireNonemptyString(data.user_message),
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

function requireBoundedCodePointString(
  value: unknown,
  minimum: number,
  maximum: number,
): string {
  const text = requireString(value);
  const length = Array.from(text).length;
  if (length < minimum || length > maximum) {
    throw new TypeError("Response string length is outside its contract");
  }
  return text;
}

function requirePrompt(value: unknown): string {
  const prompt = requireBoundedCodePointString(
    value,
    1,
    MAX_PROMPT_CODE_POINTS,
  );
  if (prompt.trim().length === 0) {
    throw new TypeError("Expected a prompt containing non-whitespace text");
  }
  return prompt;
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
  const windowsDeviceStems = new Set([
    "con",
    "prn",
    "aux",
    "nul",
    ...Array.from({ length: 9 }, (_, index) => `com${index + 1}`),
    ...Array.from({ length: 9 }, (_, index) => `lpt${index + 1}`),
    "com¹",
    "com²",
    "com³",
    "lpt¹",
    "lpt²",
    "lpt³",
  ]);
  for (const segment of path.split("/")) {
    if (
      segment.length === 0 ||
      segment === "." ||
      segment === ".." ||
      /[<>:"|?*]/.test(segment) ||
      Array.from(segment).some((character) => character.codePointAt(0)! < 32) ||
      segment.endsWith(" ") ||
      segment.endsWith(".") ||
      windowsDeviceStems.has(segment.split(".", 1)[0].toLocaleLowerCase("en-US"))
    ) {
      throw new TypeError("Expected a safe project-relative path");
    }
  }
  return path;
}

function requireNonnegativeInteger(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new TypeError("Expected a nonnegative safe integer response field");
  }
  return value;
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
  const match =
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(
      timestamp,
    );
  if (match === null) {
    throw new TypeError("Expected an RFC3339 timestamp response field");
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const offsetHour = match[7] === undefined ? 0 : Number(match[7]);
  const offsetMinute = match[8] === undefined ? 0 : Number(match[8]);
  const daysInMonth = gregorianDaysInMonth(year, month);
  if (
    year < 1 ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > daysInMonth ||
    hour > 23 ||
    minute > 59 ||
    second > 59 ||
    offsetHour > 23 ||
    offsetMinute > 59 ||
    !Number.isFinite(Date.parse(timestamp))
  ) {
    throw new TypeError("Expected an RFC3339 timestamp response field");
  }
  return timestamp;
}

function gregorianDaysInMonth(year: number, month: number): number {
  if (month === 2) {
    const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    return leapYear ? 29 : 28;
  }
  if (month === 4 || month === 6 || month === 9 || month === 11) {
    return 30;
  }
  if (
    month === 1 ||
    month === 3 ||
    month === 5 ||
    month === 7 ||
    month === 8 ||
    month === 10 ||
    month === 12
  ) {
    return 31;
  }
  return 0;
}

function requireNullableTimestamp(value: unknown): string | null {
  return value === null ? null : requireTimestamp(value);
}

function requireBoolean(value: unknown): boolean {
  if (typeof value !== "boolean") {
    throw new TypeError("Expected a boolean response field");
  }
  return value;
}

function requireLiteral<T extends 1 | 2>(value: unknown, literal: T): T {
  if (value !== literal) {
    throw new TypeError("Unsupported schema version");
  }
  return literal;
}

function requireJava(value: unknown): "java" {
  if (value !== "java") {
    throw new TypeError("Unsupported project edition");
  }
  return value;
}

function requireCatalogStatus(
  value: unknown,
): "development_fixture" | "production" {
  if (value !== "development_fixture" && value !== "production") {
    throw new TypeError("Unsupported catalog status");
  }
  return value;
}

function requireCoverageStatus(value: unknown): "covered" | "missing" {
  if (value !== "covered" && value !== "missing") {
    throw new TypeError("Unsupported coverage status");
  }
  return value;
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

function requireJobStatus(value: unknown): JobStatus {
  if (
    value !== "queued" &&
    value !== "generating" &&
    value !== "postprocessing" &&
    value !== "completed" &&
    value !== "failed" &&
    value !== "canceled"
  ) {
    throw new TypeError("Unsupported job status");
  }
  return value;
}

function requireCandidateStatus(value: unknown): CandidateStatus {
  if (
    value !== "pending" &&
    value !== "generating" &&
    value !== "postprocessing" &&
    value !== "completed" &&
    value !== "failed" &&
    value !== "canceled"
  ) {
    throw new TypeError("Unsupported candidate status");
  }
  return value;
}

function requireCandidateIndex(value: unknown): 0 | 1 | 2 | 3 {
  if (value !== 0 && value !== 1 && value !== 2 && value !== 3) {
    throw new TypeError("Unsupported candidate index");
  }
  return value;
}

function parseFourCandidateStatuses(
  value: unknown,
): JobSummary["candidateStatuses"] {
  const items = requireArray(value);
  if (items.length !== 4) {
    throw new TypeError("Expected exactly four candidate statuses");
  }
  return [
    requireCandidateStatus(items[0]),
    requireCandidateStatus(items[1]),
    requireCandidateStatus(items[2]),
    requireCandidateStatus(items[3]),
  ];
}

function parseFourSeeds(value: unknown): JobRequest["seeds"] {
  const items = requireArray(value);
  if (items.length !== 4) {
    throw new TypeError("Expected exactly four seeds");
  }
  const parsed: JobRequest["seeds"] = [
    requireNonnegativeInteger(items[0]),
    requireNonnegativeInteger(items[1]),
    requireNonnegativeInteger(items[2]),
    requireNonnegativeInteger(items[3]),
  ];
  if (new Set(parsed).size !== 4) {
    throw new TypeError("Expected four unique seeds");
  }
  return parsed;
}

function requireNullableString(value: unknown): string | null {
  return value === null ? null : requireString(value);
}

function requireBoundedStringArray(
  value: unknown,
  minimum: number,
  maximum: number,
): readonly string[] {
  const items = requireArray(value);
  if (items.length < minimum || items.length > maximum) {
    throw new TypeError("Unexpected response array length");
  }
  return items.map(requireString);
}

function parseSupportedFormats(
  value: unknown,
): readonly [number, number] | null {
  if (value === null) {
    return null;
  }
  const formats = requireArray(value);
  if (formats.length !== 2) {
    throw new TypeError("Expected a two-value supported format range");
  }
  return [
    requireNonnegativeInteger(formats[0]),
    requireNonnegativeInteger(formats[1]),
  ];
}
