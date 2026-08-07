import { useEffect, useMemo, useRef, useState } from "react";

import type { ApiError } from "../api";
import { getCandidateReport, readCandidateArtifactUrl } from "./api";
import type { CandidateReport } from "./api";
import type { GenerationCandidateState, GenerationJobDetail } from "./types";

const candidateStatusLabels: Record<GenerationCandidateState["status"], string> = {
  pending: "待处理",
  generating: "生成中",
  raw_ready: "原始结果已就绪",
  postprocessing: "后处理中",
  completed: "已完成",
  failed: "失败",
  canceled: "已取消",
  inherited: "沿用父任务结果",
};

const artifactTabs = [
  { key: "final", label: "最终结果" },
  { key: "nearest", label: "放大预览" },
  { key: "tile", label: "3×3 平铺" },
] as const;

export default function CandidateStep({
  projectId,
  job,
  connected,
  error,
  onRefresh,
  onContinue,
  onCancel,
  onRetry,
}: {
  readonly projectId: string;
  readonly job: GenerationJobDetail | null;
  readonly connected: boolean;
  readonly error: ApiError | null;
  readonly onRefresh: () => Promise<void>;
  readonly onContinue: () => Promise<void>;
  readonly onCancel: () => Promise<void>;
  readonly onRetry: () => Promise<void>;
}) {
  const [selectedTabs, setSelectedTabs] = useState<Record<number, "final" | "nearest" | "tile">>({
    0: "final",
    1: "final",
    2: "final",
    3: "final",
  });
  const [reports, setReports] = useState<Partial<Record<number, CandidateReport>>>({});
  const [reportErrors, setReportErrors] = useState<Partial<Record<number, string>>>({});
  const [loadingReport, setLoadingReport] = useState<number | null>(null);
  const reportGeneration = useRef(0);

  useEffect(() => {
    setReports({});
    setReportErrors({});
    setLoadingReport(null);
    reportGeneration.current += 1;
  }, [job?.request.jobId]);

  const actionableFailures = [
    ...(error === null ? [] : [error]),
    ...(job?.state.failure !== null &&
    job?.state.failure !== undefined &&
    (error === null || job.state.failure.code !== error.code)
      ? [job.state.failure]
      : []),
  ];
  const retryable =
    job?.state.status === "failed" || job?.state.status === "canceled"
      ? job.state.failure?.retryable === true
      : false;

  const cards = useMemo(() => {
    if (job === null) {
      return [];
    }
    return job.state.candidates.map((candidate) => ({
      candidate,
      selectedTab: selectedTabs[candidate.candidateIndex] ?? "final",
      report: reports[candidate.candidateIndex],
    }));
  }, [job, reports, selectedTabs]);

  async function handleReport(candidateIndex: 0 | 1 | 2 | 3) {
    if (job === null) {
      return;
    }
    const requestedJobId = job.request.jobId;
    const requestedGeneration = reportGeneration.current;
    setLoadingReport(candidateIndex);
    setReportErrors((current) => ({ ...current, [candidateIndex]: undefined }));
    try {
      const report = await getCandidateReport(projectId, requestedJobId, candidateIndex);
      if (reportGeneration.current !== requestedGeneration) return;
      setReports((current) => ({ ...current, [candidateIndex]: report }));
    } catch {
      if (reportGeneration.current === requestedGeneration) {
        setReportErrors((current) => ({
          ...current,
          [candidateIndex]: "质量报告读取失败，请稍后重试。",
        }));
      }
    } finally {
      if (reportGeneration.current === requestedGeneration) setLoadingReport(null);
    }
  }

  if (job === null) {
    return (
      <section className="panel generation-panel">
        <p className="empty-state">当前还没有可展示的 Phase 5 任务。</p>
      </section>
    );
  }

  return (
    <section className="panel generation-panel" aria-labelledby="candidate-step-title">
      <div className="section-heading">
        <span className="step-marker" aria-hidden="true">05</span>
        <div>
          <h2 id="candidate-step-title">候选结果</h2>
          <p>固定四候选；已完成或沿用的候选会在取消、失败和重试期间继续保留。</p>
        </div>
      </div>

      <div className="candidate-toolbar">
        <p className={`connection-note ${connected ? "connected" : "disconnected"}`}>
          {connected ? "实时连接已建立" : "当前显示的是持久化快照"}
        </p>
        <div className="wizard-actions compact-actions">
          {job.state.status === "queued" && (
            <button type="button" onClick={() => void onContinue()}>
              继续任务
            </button>
          )}
          {(job.state.status === "generating" || job.state.status === "postprocessing") && (
            <button
              type="button"
              disabled={job.state.cancelRequestedAt !== null}
              onClick={() => void onCancel()}
            >
              {job.state.cancelRequestedAt === null ? "取消任务" : "正在等待取消确认…"}
            </button>
          )}
          {retryable && (
            <button type="button" onClick={() => void onRetry()}>
              重试任务
            </button>
          )}
          <button type="button" onClick={() => void onRefresh()}>
            刷新任务
          </button>
        </div>
      </div>

      {job.state.cancelRequestedAt !== null && (
        <p className="interrupted-note">已提交取消请求，正在等待受管 GPU 工作确认停止。</p>
      )}

      {actionableFailures.map((failure, index) => (
        <div className="inline-error" role="alert" key={`${failure.code}:${index}`}>
          <h3>{failure.userMessage}</h3>
          {failure.recommendedActions.length > 0 && (
            <ul>
              {failure.recommendedActions.map((action) => (
                <li key={action}>{action}</li>
              ))}
            </ul>
          )}
        </div>
      ))}

      <div className="candidate-grid">
        {cards.map(({ candidate, selectedTab, report }) => {
          const selectedArtifact = candidate.artifacts[selectedTab];
          return (
            <article
              className="candidate-card"
              key={candidate.candidateIndex}
              aria-label={`候选 ${candidate.candidateIndex + 1}`}
            >
              <div className="job-card-heading">
                <div>
                  <h3>{`候选 ${candidate.candidateIndex + 1}`}</h3>
                  <p className="lineage">
                    {candidate.lineage === null
                      ? "当前任务产物"
                      : `沿用自 ${candidate.lineage.parentJobId} / 候选 ${candidate.lineage.parentCandidateIndex + 1}`}
                  </p>
                </div>
                <span className="status-badge">{candidateStatusLabels[candidate.status]}</span>
              </div>

              <dl className="job-facts candidate-facts">
                <div>
                  <dt>批次 seed</dt>
                  <dd>{candidate.batchSeed}</dd>
                </div>
                <div>
                  <dt>批次位置</dt>
                  <dd>{`批次 ${candidate.batchIndex} / 位置 ${candidate.positionInBatch}`}</dd>
                </div>
              </dl>
              <p className="lineage">{`批次 seed ${candidate.batchSeed}`}</p>
              <p className="lineage">{`批次 ${candidate.batchIndex} / 位置 ${candidate.positionInBatch}`}</p>

              <div className="candidate-tabs" role="tablist" aria-label={`候选 ${candidate.candidateIndex + 1} 预览`}>
                {artifactTabs.map((tab) => (
                  <button
                    key={tab.key}
                    role="tab"
                    type="button"
                    aria-selected={selectedTab === tab.key}
                    className={selectedTab === tab.key ? "tab-selected" : "tab-button"}
                    onClick={() =>
                      setSelectedTabs((current) => ({
                        ...current,
                        [candidate.candidateIndex]: tab.key,
                      }))
                    }
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              {selectedArtifact === null ? (
                <p className="empty-state">当前视图尚未生成。</p>
              ) : (
                <img
                  className="candidate-preview"
                  alt={`候选 ${candidate.candidateIndex + 1}${artifactTabs.find((item) => item.key === selectedTab)?.label ?? ""}`}
                  src={readCandidateArtifactUrl(
                    projectId,
                    job.request.jobId,
                    candidate.candidateIndex,
                    selectedTab,
                  )}
                />
              )}

              {candidate.artifacts.report !== null && (
                <div className="candidate-report">
                  <button
                    type="button"
                    disabled={loadingReport === candidate.candidateIndex}
                    onClick={() => void handleReport(candidate.candidateIndex)}
                  >
                    {loadingReport === candidate.candidateIndex ? "正在读取质量报告…" : "读取质量报告"}
                  </button>
                  {report !== undefined && (
                    <p>{`seam score ${report.seamScore.average}`}</p>
                  )}
                  {reportErrors[candidate.candidateIndex] !== undefined && (
                    <p role="alert">{reportErrors[candidate.candidateIndex]}</p>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
