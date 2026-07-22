import { useState, type ChangeEvent, type FormEvent } from "react";

import {
  ApiRequestError,
  MAX_PROJECT_NAME_LENGTH,
  getCoverage,
  importProject,
  type ApiError,
  type CoverageReport,
  type ProjectManifest,
} from "./api";

export default function App() {
  const [projectName, setProjectName] = useState("");
  const [pack, setPack] = useState<File | null>(null);
  const [packValidationError, setPackValidationError] = useState<string | null>(
    null,
  );
  const [activeRequest, setActiveRequest] = useState<
    "idle" | "import" | "coverage"
  >("idle");
  const [manifest, setManifest] = useState<ProjectManifest | null>(null);
  const [coverage, setCoverage] = useState<CoverageReport | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const trimmedProjectName = projectName.trim();
  const projectNameLength = Array.from(trimmedProjectName).length;
  const canImport =
    projectNameLength > 0 &&
    projectNameLength <= MAX_PROJECT_NAME_LENGTH &&
    pack !== null;
  const isBusy = activeRequest !== "idle";
  const needsCoverageRetry = manifest !== null && coverage === null;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canImport || pack === null || isBusy) {
      return;
    }

    setActiveRequest("import");
    setError(null);
    setManifest(null);
    setCoverage(null);
    try {
      const imported = await importProject(trimmedProjectName, pack);
      setManifest(imported);
      const report = await getCoverage(imported.projectId);
      setCoverage(report);
    } catch (cause) {
      setError(toApiError(cause));
    } finally {
      setActiveRequest("idle");
    }
  }

  function handlePackChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedPack = event.currentTarget.files?.item(0) ?? null;
    if (selectedPack === null) {
      setPack(null);
      setPackValidationError(null);
      return;
    }
    if (!selectedPack.name.toLowerCase().endsWith(".zip")) {
      setPack(null);
      setPackValidationError("请选择扩展名为 .zip 的资源包文件");
      return;
    }
    setPack(selectedPack);
    setPackValidationError(null);
  }

  async function handleCoverageRetry() {
    if (manifest === null || isBusy) {
      return;
    }

    setActiveRequest("coverage");
    try {
      const report = await getCoverage(manifest.projectId);
      setCoverage(report);
      setError(null);
    } catch (cause) {
      setError(toApiError(cause));
    } finally {
      setActiveRequest("idle");
    }
  }

  return (
    <main className="app-shell">
      <header className="hero">
        <p className="eyebrow">AIMCTextureGen / Phase 1</p>
        <h1>导入 Java 资源包</h1>
        <p className="intro">
          上传 ZIP，建立只读快照与独立工作副本，并查看当前目录配置下的材质覆盖情况。
        </p>
      </header>

      <section className="panel import-panel" aria-labelledby="import-title">
        <div className="section-heading">
          <span className="step-marker" aria-hidden="true">01</span>
          <div>
            <h2 id="import-title">选择资源包</h2>
            <p>原始 ZIP 不会被修改。当前阶段不包含生成或采用操作。</p>
          </div>
        </div>

        <form
          aria-label="资源包导入"
          aria-busy={isBusy}
          onSubmit={handleSubmit}
        >
          <div className="field">
            <label htmlFor="project-name">项目名称</label>
            <input
              id="project-name"
              name="project-name"
              type="text"
              autoComplete="off"
              maxLength={MAX_PROJECT_NAME_LENGTH * 2}
              disabled={isBusy || needsCoverageRetry}
              value={projectName}
              onChange={(event) => setProjectName(event.currentTarget.value)}
              placeholder="例如：深岩材质包"
            />
          </div>

          <div className="field">
            <label htmlFor="pack-file">ZIP 资源包</label>
            <input
              id="pack-file"
              name="pack-file"
              type="file"
              accept=".zip,application/zip"
              aria-describedby={
                packValidationError === null
                  ? "pack-file-hint"
                  : "pack-file-hint pack-file-error"
              }
              aria-invalid={packValidationError !== null}
              disabled={isBusy || needsCoverageRetry}
              onChange={handlePackChange}
            />
            <p className="field-hint" id="pack-file-hint">
              仅支持 Java 版资源包 ZIP。
            </p>
            {packValidationError !== null && (
              <p className="validation-error" id="pack-file-error">
                {packValidationError}
              </p>
            )}
          </div>

          <button
            type="submit"
            disabled={!canImport || isBusy || needsCoverageRetry}
          >
            {activeRequest === "import" ? "正在导入并分析…" : "导入并分析"}
          </button>
        </form>
      </section>

      {error !== null && (
        <ErrorPanel
          error={error}
          importedProject={needsCoverageRetry ? manifest : null}
          isRetrying={activeRequest === "coverage"}
          onCoverageRetry={handleCoverageRetry}
        />
      )}
      {manifest !== null && coverage !== null && (
        <CoverageSummary manifest={manifest} coverage={coverage} />
      )}
    </main>
  );
}

function ErrorPanel({
  error,
  importedProject,
  isRetrying,
  onCoverageRetry,
}: {
  readonly error: ApiError;
  readonly importedProject: ProjectManifest | null;
  readonly isRetrying: boolean;
  readonly onCoverageRetry: () => void;
}) {
  return (
    <section
      className="panel error-panel"
      role="alert"
      aria-busy={isRetrying}
    >
      <p className="eyebrow">
        {importedProject === null ? "导入未完成" : "覆盖读取未完成"} / {error.code}
      </p>
      <h2>{error.userMessage}</h2>
      {importedProject !== null && (
        <p className="recovery-note">
          项目已创建：{importedProject.projectName}。无需重新导入 ZIP，请重试覆盖分析。
        </p>
      )}
      {error.recommendedActions.length > 0 && (
        <div>
          <p className="error-guidance">建议操作</p>
          <ul>
            {error.recommendedActions.map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </div>
      )}
      {error.technicalDetails !== null && (
        <details>
          <summary>技术详情</summary>
          <code>{error.technicalDetails}</code>
        </details>
      )}
      {importedProject !== null && (
        <button
          className="retry-button"
          type="button"
          disabled={isRetrying}
          onClick={onCoverageRetry}
        >
          {isRetrying ? "正在重试覆盖分析…" : "重试覆盖分析"}
        </button>
      )}
    </section>
  );
}

function CoverageSummary({
  manifest,
  coverage,
}: {
  readonly manifest: ProjectManifest;
  readonly coverage: CoverageReport;
}) {
  const missingEligibleItems = coverage.items.filter(
    (item) => item.status === "missing" && item.mvpEligible,
  );

  return (
    <section className="panel summary-panel" aria-labelledby="summary-title">
      <div className="section-heading">
        <span className="step-marker complete-marker" aria-hidden="true">02</span>
        <div>
          <p className="eyebrow">导入完成</p>
          <h2 id="summary-title">{manifest.projectName}</h2>
        </div>
      </div>

      {coverage.catalogStatus === "development_fixture" && (
        <p className="fixture-warning">
          当前使用开发测试目录，仅用于验证导入流程，不代表完整的生产兼容目录。
        </p>
      )}

      <div className="summary-grid" aria-label="覆盖统计">
        <p>资源格式 {manifest.javaPackFormat}</p>
        <p>已覆盖 {coverage.coveredCount}</p>
        <p>未覆盖 {coverage.missingCount}</p>
      </div>

      <div className="missing-section">
        <h3>未覆盖的可生成条目</h3>
        {missingEligibleItems.length > 0 ? (
          <ul className="missing-list">
            {missingEligibleItems.map((item) => (
              <li key={item.semanticId}>
                <strong>{item.displayName}</strong>
                <code>{item.relativePath}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p>当前没有未覆盖的 MVP 候选。</p>
        )}
      </div>

      <p className="unknown-count">
        未知/自定义 {coverage.unknownPaths.length}
        <span>这些文件会保留在工作副本中。</span>
      </p>
    </section>
  );
}

function toApiError(cause: unknown): ApiError {
  if (cause instanceof ApiRequestError) {
    return cause;
  }
  return {
    code: "UNEXPECTED_UI_ERROR",
    stage: "displaying_import",
    userMessage: "导入时发生未知错误",
    recommendedActions: ["重试操作；若问题持续，请查看应用日志"],
    technicalDetails: cause instanceof Error ? cause.message : null,
  };
}
