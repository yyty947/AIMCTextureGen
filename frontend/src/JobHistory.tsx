import type {
  CandidateStatus,
  JobDetail,
  JobStatus,
  JobSummary,
} from "./api";

export interface JobHistoryEntry {
  readonly summary: JobSummary;
  readonly detail: JobDetail;
}

const jobStatusLabels: Record<JobStatus, string> = {
  queued: "排队中",
  generating: "生成中",
  postprocessing: "后处理中",
  completed: "已完成",
  failed: "失败",
  canceled: "已取消",
};

const candidateStatusLabels: Record<CandidateStatus, string> = {
  pending: "待处理",
  generating: "生成中",
  postprocessing: "后处理中",
  completed: "完成",
  failed: "失败",
  canceled: "已取消",
};

export default function JobHistory({
  jobs,
}: {
  readonly jobs: readonly JobHistoryEntry[];
}) {
  return (
    <section className="job-history" aria-labelledby="job-history-title">
      <div className="history-heading">
        <div>
          <p className="eyebrow">只读记录</p>
          <h3 id="job-history-title">任务历史</h3>
        </div>
        <span>{jobs.length} 项</span>
      </div>

      {jobs.length === 0 ? (
        <p className="empty-state">当前项目还没有生成任务。</p>
      ) : (
        <div className="job-items">
          {jobs.map(({ summary, detail }) => (
            <article
              className="job-card"
              aria-label={`${summary.targetDisplayName} 任务`}
              key={summary.jobId}
            >
              <div className="job-card-heading">
                <div>
                  <h4>{summary.targetDisplayName}</h4>
                  <code>{summary.targetSemanticId}</code>
                </div>
                <span className={`status-badge status-${summary.status}`}>
                  {jobStatusLabels[summary.status]}
                </span>
              </div>

              <dl className="job-facts">
                <div>
                  <dt>分辨率</dt>
                  <dd>{summary.resolution} × {summary.resolution}</dd>
                </div>
                <div>
                  <dt>并行</dt>
                  <dd>并行 {summary.parallelism}</dd>
                </div>
                <div>
                  <dt>候选</dt>
                  <dd>{candidateSummary(summary.candidateStatuses)}</dd>
                </div>
                <div>
                  <dt>更新时间</dt>
                  <dd>
                    <time dateTime={summary.updatedAt}>
                      {formatTimestamp(summary.updatedAt)}
                    </time>
                  </dd>
                </div>
              </dl>

              <p className="lineage">
                {summary.retryOfJobId === null ? (
                  "原始任务"
                ) : (
                  <>重试自 <code>{summary.retryOfJobId}</code></>
                )}
              </p>

              {detail.state.failure?.code === "JOB_INTERRUPTED" && (
                <p className="interrupted-note">
                  应用重启时此任务仍在运行，已安全标记为失败。已完成候选仍会保留。
                </p>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function candidateSummary(
  statuses: JobSummary["candidateStatuses"],
): string {
  const counts = new Map<CandidateStatus, number>();
  for (const status of statuses) {
    counts.set(status, (counts.get(status) ?? 0) + 1);
  }
  const parts = (Object.keys(candidateStatusLabels) as CandidateStatus[])
    .filter((status) => counts.has(status))
    .map((status) => `${candidateStatusLabels[status]} ${counts.get(status)}`);
  return `候选 4：${parts.join("，")}`;
}

function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
