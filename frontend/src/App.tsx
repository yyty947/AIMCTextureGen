import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import {
  ApiRequestError,
  MAX_PROJECT_NAME_LENGTH,
  getCoverage,
  getJob,
  getProject,
  getRecoveryReport,
  importProject,
  invalidResponseError,
  listJobs,
  listProjects,
  type ApiError,
  type CoverageReport,
  type JobDetail,
  type JobSummary,
  type ProjectManifest,
  type ProjectSummary,
  type RecoveryReport,
} from "./api";
import JobHistory, { type JobHistoryEntry } from "./JobHistory";
import ProjectList from "./ProjectList";

export default function App() {
  const [projectName, setProjectName] = useState("");
  const [pack, setPack] = useState<File | null>(null);
  const [packValidationError, setPackValidationError] = useState<string | null>(
    null,
  );
  const [activeRequest, setActiveRequest] = useState<
    "idle" | "import" | "coverage"
  >("idle");
  const [projects, setProjects] = useState<readonly ProjectSummary[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState<ApiError | null>(null);
  const [recovery, setRecovery] = useState<RecoveryReport | null>(null);
  const [recoveryError, setRecoveryError] = useState<ApiError | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardError, setDashboardError] = useState<ApiError | null>(null);
  const [manifest, setManifest] = useState<ProjectManifest | null>(null);
  const [coverage, setCoverage] = useState<CoverageReport | null>(null);
  const [jobs, setJobs] = useState<readonly JobHistoryEntry[]>([]);
  const [pendingImportedProject, setPendingImportedProject] =
    useState<ProjectManifest | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const componentGeneration = useRef(0);
  const dashboardRequest = useRef(0);
  const coverageRequest = useRef(0);
  const operationGeneration = useRef(0);
  const projectsEpoch = useRef(0);
  const selectedProject = useRef<string | null>(null);

  useEffect(() => {
    const generation = componentGeneration.current + 1;
    componentGeneration.current = generation;
    const requestEpoch = projectsEpoch.current;
    void listProjects()
      .then((loadedProjects) => {
        if (isCurrentComponentGeneration(generation)) {
          setProjects((current) =>
            projectsEpoch.current === requestEpoch
              ? loadedProjects
              : mergeProjectLists(current, loadedProjects),
          );
          setProjectsError(null);
        }
      })
      .catch((cause: unknown) => {
        if (
          isCurrentComponentGeneration(generation) &&
          projectsEpoch.current === requestEpoch
        ) {
          setProjectsError(toApiError(cause));
        }
      })
      .finally(() => {
        if (isCurrentComponentGeneration(generation)) {
          setProjectsLoading(false);
        }
      });
    void getRecoveryReport()
      .then((report) => {
        if (isCurrentComponentGeneration(generation)) {
          setRecovery(report);
          setRecoveryError(null);
        }
      })
      .catch((cause: unknown) => {
        if (isCurrentComponentGeneration(generation)) {
          setRecoveryError(toApiError(cause));
        }
      });
    return () => {
      if (componentGeneration.current === generation) {
        componentGeneration.current = generation + 1;
      }
      dashboardRequest.current += 1;
      coverageRequest.current += 1;
      operationGeneration.current += 1;
      projectsEpoch.current += 1;
      selectedProject.current = null;
    };
  }, []);

  function isCurrentComponentGeneration(generation: number): boolean {
    return (
      generation !== 0 && componentGeneration.current === generation
    );
  }

  const trimmedProjectName = projectName.trim();
  const projectNameLength = Array.from(trimmedProjectName).length;
  const canImport =
    projectNameLength > 0 &&
    projectNameLength <= MAX_PROJECT_NAME_LENGTH &&
    pack !== null;
  const isBusy = activeRequest !== "idle";
  const needsCoverageRetry = pendingImportedProject !== null;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canImport || pack === null || isBusy) {
      return;
    }

    const componentRequestGeneration = componentGeneration.current;
    const operationRequestGeneration = operationGeneration.current + 1;
    operationGeneration.current = operationRequestGeneration;
    setActiveRequest("import");
    setError(null);
    let importedProjectId: string | null = null;
    let importedCoverageRequestId: number | null = null;
    try {
      const imported = await importProject(trimmedProjectName, pack);
      if (!isCurrentComponentGeneration(componentRequestGeneration)) {
        return;
      }
      if (operationGeneration.current !== operationRequestGeneration) {
        projectsEpoch.current += 1;
        setProjects((current) =>
          mergeImportedProject(current, summaryFromManifest(imported)),
        );
        return;
      }
      importedProjectId = imported.projectId;
      dashboardRequest.current += 1;
      projectsEpoch.current += 1;
      selectedProject.current = imported.projectId;
      setDashboardError(null);
      setSelectedProjectId(imported.projectId);
      setDashboardLoading(false);
      setPendingImportedProject(imported);
      setManifest(imported);
      setCoverage(null);
      setJobs([]);
      setProjects((current) =>
        mergeImportedProject(current, summaryFromManifest(imported)),
      );
      importedCoverageRequestId = coverageRequest.current + 1;
      coverageRequest.current = importedCoverageRequestId;
      const report = await getCoverage(imported.projectId);
      if (
        isCurrentComponentGeneration(componentRequestGeneration) &&
        operationGeneration.current === operationRequestGeneration &&
        coverageRequest.current === importedCoverageRequestId &&
        selectedProject.current === imported.projectId
      ) {
        setCoverage(report);
        setPendingImportedProject(null);
      }
    } catch (cause) {
      if (
        isCurrentComponentGeneration(componentRequestGeneration) &&
        operationGeneration.current === operationRequestGeneration &&
        (importedCoverageRequestId === null ||
          (coverageRequest.current === importedCoverageRequestId &&
            selectedProject.current === importedProjectId))
      ) {
        setError(toApiError(cause));
      }
    } finally {
      if (
        isCurrentComponentGeneration(componentRequestGeneration) &&
        operationGeneration.current === operationRequestGeneration
      ) {
        setActiveRequest("idle");
      }
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
    if (pendingImportedProject === null || isBusy) {
      return;
    }

    const projectId = pendingImportedProject.projectId;
    const componentRequestGeneration = componentGeneration.current;
    const operationRequestGeneration = operationGeneration.current + 1;
    operationGeneration.current = operationRequestGeneration;
    const requestId = coverageRequest.current + 1;
    coverageRequest.current = requestId;
    setActiveRequest("coverage");
    try {
      const report = await getCoverage(projectId);
      if (
        isCurrentComponentGeneration(componentRequestGeneration) &&
        operationGeneration.current === operationRequestGeneration &&
        coverageRequest.current === requestId &&
        selectedProject.current === projectId
      ) {
        setCoverage(report);
        setPendingImportedProject(null);
        setError(null);
      }
    } catch (cause) {
      if (
        isCurrentComponentGeneration(componentRequestGeneration) &&
        operationGeneration.current === operationRequestGeneration &&
        coverageRequest.current === requestId &&
        selectedProject.current === projectId
      ) {
        setError(toApiError(cause));
      }
    } finally {
      if (
        isCurrentComponentGeneration(componentRequestGeneration) &&
        operationGeneration.current === operationRequestGeneration &&
        coverageRequest.current === requestId
      ) {
        setActiveRequest("idle");
      }
    }
  }

  async function refreshProjects() {
    const componentRequestGeneration = componentGeneration.current;
    const requestEpoch = projectsEpoch.current;
    setProjectsLoading(true);
    try {
      const loadedProjects = await listProjects();
      if (!isCurrentComponentGeneration(componentRequestGeneration)) {
        return;
      }
      if (projectsEpoch.current === requestEpoch) {
        setProjects(loadedProjects);
        setProjectsError(null);
      } else {
        setProjects((current) => mergeProjectLists(current, loadedProjects));
      }
    } catch (cause) {
      if (
        isCurrentComponentGeneration(componentRequestGeneration) &&
        projectsEpoch.current === requestEpoch
      ) {
        setProjectsError(toApiError(cause));
      }
    } finally {
      if (isCurrentComponentGeneration(componentRequestGeneration)) {
        setProjectsLoading(false);
      }
    }
  }

  async function refreshRecovery() {
    const componentRequestGeneration = componentGeneration.current;
    try {
      const report = await getRecoveryReport();
      if (!isCurrentComponentGeneration(componentRequestGeneration)) {
        return;
      }
      setRecovery(report);
      setRecoveryError(null);
    } catch (cause) {
      if (isCurrentComponentGeneration(componentRequestGeneration)) {
        setRecoveryError(toApiError(cause));
      }
    }
  }

  function handleProjectSelect(projectId: string) {
    operationGeneration.current += 1;
    coverageRequest.current += 1;
    selectedProject.current = projectId;
    setActiveRequest("idle");
    setSelectedProjectId(projectId);
    setPendingImportedProject(null);
    setError(null);
    setDashboardError(null);
    setManifest(null);
    setCoverage(null);
    setJobs([]);
    void loadProjectDashboard(projectId);
  }

  async function loadProjectDashboard(projectId: string) {
    const componentRequestGeneration = componentGeneration.current;
    const requestId = dashboardRequest.current + 1;
    dashboardRequest.current = requestId;
    setDashboardLoading(true);
    try {
      const [loadedManifest, loadedCoverage, summaries] = await Promise.all([
        getProject(projectId),
        getCoverage(projectId),
        listJobs(projectId),
      ]);
      if (
        !isCurrentComponentGeneration(componentRequestGeneration) ||
        dashboardRequest.current !== requestId ||
        selectedProject.current !== projectId
      ) {
        return;
      }
      const details = await Promise.all(
        summaries.map((summary) => getJob(projectId, summary.jobId)),
      );
      if (
        !isCurrentComponentGeneration(componentRequestGeneration) ||
        dashboardRequest.current !== requestId ||
        selectedProject.current !== projectId
      ) {
        return;
      }
      setManifest(loadedManifest);
      setCoverage(loadedCoverage);
      setJobs(
        summaries.map((summary, index) =>
          reconcileJobHistory(summary, details[index]),
        ),
      );
      setDashboardError(null);
    } catch (cause) {
      if (
        isCurrentComponentGeneration(componentRequestGeneration) &&
        dashboardRequest.current === requestId &&
        selectedProject.current === projectId
      ) {
        setDashboardError(toApiError(cause));
      }
    } finally {
      if (
        isCurrentComponentGeneration(componentRequestGeneration) &&
        dashboardRequest.current === requestId &&
        selectedProject.current === projectId
      ) {
        setDashboardLoading(false);
      }
    }
  }

  return (
    <main className="app-shell">
      <header className="hero">
        <p className="eyebrow">AIMCTextureGen / 项目面板</p>
        <h1>Java 资源包项目</h1>
        <p className="intro">
          导入新资源包或恢复已有项目，查看覆盖情况与持久化任务历史。
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

      {(recovery?.issues.length ?? 0) > 0 && (
        <RecoveryWarning report={recovery as RecoveryReport} />
      )}
      {recoveryError !== null && (
        <InlineRequestError
          title="恢复报告读取失败"
          error={recoveryError}
          retryLabel="重试恢复报告"
          onRetry={() => void refreshRecovery()}
        />
      )}

      <div className="dashboard-layout">
        <section className="panel project-navigation">
          <ProjectList
            projects={projects}
            selectedProjectId={selectedProjectId}
            onSelect={handleProjectSelect}
          />
          {projectsLoading && <p className="loading-note">正在读取已有项目…</p>}
          {projectsError !== null && (
            <InlineRequestError
              title="项目列表读取失败"
              error={projectsError}
              retryLabel="重试项目列表"
              onRetry={() => void refreshProjects()}
            />
          )}
        </section>

        <section
          className="panel project-dashboard"
          aria-label="所选项目概览"
          aria-busy={dashboardLoading}
        >
          {selectedProjectId === null ? (
            <p className="empty-state">选择已有项目，或导入新的 Java 资源包。</p>
          ) : dashboardLoading ? (
            <p className="loading-note">正在读取项目覆盖与任务历史…</p>
          ) : dashboardError !== null ? (
            <InlineRequestError
              title="当前项目读取失败"
              error={dashboardError}
              retryLabel="重试当前项目"
              onRetry={() => void loadProjectDashboard(selectedProjectId)}
            />
          ) : manifest !== null && coverage !== null ? (
            <>
              <CoverageSummary manifest={manifest} coverage={coverage} />
              <JobHistory jobs={jobs} />
            </>
          ) : (
            <p className="empty-state">项目已创建，覆盖分析尚未完成。</p>
          )}
        </section>
      </div>
    </main>
  );
}

function RecoveryWarning({ report }: { readonly report: RecoveryReport }) {
  return (
    <section className="panel recovery-warning" role="status">
      <p className="eyebrow">启动恢复警告</p>
      <h2>部分损坏记录已隔离</h2>
      <p>
        有效项目仍可正常打开；应用没有猜测、修改或删除损坏的 JSON 记录。
      </p>
      <ul>
        {report.issues.map((issue, index) => (
          <li key={`${issue.projectId}:${issue.jobId ?? "project"}:${issue.code}:${index}`}>
            <strong>{issue.code}</strong>：{issue.userMessage}
          </li>
        ))}
      </ul>
    </section>
  );
}

function InlineRequestError({
  title,
  error,
  retryLabel,
  onRetry,
}: {
  readonly title: string;
  readonly error: ApiError;
  readonly retryLabel: string;
  readonly onRetry: () => void;
}) {
  return (
    <div className="inline-error" role="alert">
      <h3>{title}</h3>
      <p>{error.userMessage}</p>
      <button className="retry-button" type="button" onClick={onRetry}>
        {retryLabel}
      </button>
    </div>
  );
}

function summaryFromManifest(manifest: ProjectManifest): ProjectSummary {
  return {
    projectId: manifest.projectId,
    projectName: manifest.projectName,
    edition: manifest.edition,
    javaPackFormat: manifest.javaPackFormat,
    catalogId: manifest.catalogId,
    createdAt: manifest.createdAt,
    updatedAt: manifest.updatedAt,
  };
}

function mergeImportedProject(
  projects: readonly ProjectSummary[],
  imported: ProjectSummary,
): readonly ProjectSummary[] {
  return mergeProjectLists([imported], projects);
}

function mergeProjectLists(
  preferred: readonly ProjectSummary[],
  incoming: readonly ProjectSummary[],
): readonly ProjectSummary[] {
  const preferredIds = new Set(preferred.map((project) => project.projectId));
  return [
    ...preferred,
    ...incoming.filter((project) => !preferredIds.has(project.projectId)),
  ];
}

function reconcileJobHistory(
  summary: JobSummary,
  detail: JobDetail,
): JobHistoryEntry {
  const { request, state } = detail;
  if (
    summary.jobId !== request.jobId ||
    summary.projectId !== request.projectId ||
    summary.retryOfJobId !== request.retryOfJobId ||
    summary.targetSemanticId !== request.targetSemanticId ||
    summary.targetDisplayName !== request.targetDisplayName ||
    summary.resolution !== request.resolution ||
    summary.parallelism !== request.parallelism ||
    summary.createdAt !== request.createdAt
  ) {
    throw invalidResponseError(
      new TypeError("Job list and detail immutable fields do not match"),
    );
  }
  return {
    summary: {
      ...summary,
      status: state.status,
      revision: state.revision,
      candidateStatuses: [
        state.candidates[0].status,
        state.candidates[1].status,
        state.candidates[2].status,
        state.candidates[3].status,
      ],
      updatedAt: state.updatedAt,
    },
    detail,
  };
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
