export interface ProjectManifest {
  schemaVersion: 1;
  projectId: string;
  projectName: string;
  edition: "java";
  javaPackFormat: number;
  supportedFormats: readonly [number, number] | null;
  catalogId: string;
  sourceSha256: string;
  createdAt: string;
  updatedAt: string;
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
  return parseSuccessfulResponse(payload, (data): ProjectManifest => ({
    schemaVersion: requireLiteralOne(data.schema_version),
    projectId: requireString(data.project_id),
    projectName: requireString(data.project_name),
    edition: requireJava(data.edition),
    javaPackFormat: requireNumber(data.java_pack_format),
    supportedFormats: parseSupportedFormats(data.supported_formats),
    catalogId: requireString(data.catalog_id),
    sourceSha256: requireString(data.source_sha256),
    createdAt: requireString(data.created_at),
    updatedAt: requireString(data.updated_at),
  }));
}

export async function getCoverage(projectId: string): Promise<CoverageReport> {
  const payload = await requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/coverage`,
  );
  return parseSuccessfulResponse(payload, (data): CoverageReport => ({
    catalogId: requireString(data.catalog_id),
    catalogStatus: requireCatalogStatus(data.catalog_status),
    coveredCount: requireNumber(data.covered_count),
    missingCount: requireNumber(data.missing_count),
    unknownPaths: requireArray(data.unknown_paths).map(requireString),
    items: requireArray(data.items).map(parseCoverageItem),
  }));
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

function invalidResponseError(cause: unknown): ApiRequestError {
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

function requireNumber(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TypeError("Expected a numeric response field");
  }
  return value;
}

function requireBoolean(value: unknown): boolean {
  if (typeof value !== "boolean") {
    throw new TypeError("Expected a boolean response field");
  }
  return value;
}

function requireLiteralOne(value: unknown): 1 {
  if (value !== 1) {
    throw new TypeError("Unsupported project schema version");
  }
  return value;
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
  return [requireNumber(formats[0]), requireNumber(formats[1])];
}
