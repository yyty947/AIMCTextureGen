import { useEffect, useRef, useState } from "react";

import {
  ApiRequestError,
  InferenceStatus,
  InstallPlan,
  InstallationOperation,
  beginInstallation,
  cancelInstallation,
  formatDecimalGb,
  formatDecimalGib,
  getComfyUILog,
  getInferenceStatus,
  getInstallPlan,
  getInstallation,
  startComfyUI,
  stopComfyUI,
} from "./api";

const ACTIVE_INSTALLATION_STATES = new Set([
  "planned",
  "downloading",
  "extracting",
  "installing",
]);

export default function InferenceSetup() {
  const [expanded, setExpanded] = useState(false);
  const [status, setStatus] = useState<InferenceStatus | null>(null);
  const [plan, setPlan] = useState<InstallPlan | null>(null);
  const [operation, setOperation] = useState<InstallationOperation | null>(
    null,
  );
  const [accepted, setAccepted] = useState<ReadonlySet<string>>(new Set());
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string | null>(null);
  const operationRef = useRef<InstallationOperation | null>(null);

  function setCurrentOperation(next: InstallationOperation | null) {
    operationRef.current = next;
    setOperation(next);
  }

  useEffect(() => {
    return () => {
      operationRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!expanded) {
      return;
    }
    const controller = new AbortController();
    let disposed = false;
    let lastRevision = 0;
    async function refresh() {
      if (disposed || controller.signal.aborted) {
        return;
      }
      try {
        const [nextStatus, nextPlan] = await Promise.all([
          getInferenceStatus(),
          getInstallPlan(),
        ]);
        if (disposed) {
          return;
        }
        setStatus(nextStatus);
        setPlan(nextPlan);
        setError(null);
        const current = operationRef.current;
        if (
          current !== null &&
          ACTIVE_INSTALLATION_STATES.has(current.state)
        ) {
          const nextOperation = await getInstallation(current.operationId);
          if (disposed) {
            return;
          }
          if (nextOperation.revision < lastRevision) {
            throw new TypeError("Installation revision went backwards");
          }
          lastRevision = nextOperation.revision;
          setCurrentOperation(nextOperation);
        }
      } catch (cause) {
        if (!disposed && !controller.signal.aborted) {
          setError(
            cause instanceof ApiRequestError
              ? cause
              : new ApiRequestError({
                  code: "INFERENCE_STATUS_FAILED",
                  stage: "displaying_inference",
                  userMessage: "推理环境状态读取失败",
                  recommendedActions: ["重试操作"],
                  technicalDetails:
                    cause instanceof Error ? cause.message : null,
                }),
          );
        }
      } finally {
        if (!disposed && !controller.signal.aborted) {
          window.setTimeout(() => {
            void refresh();
          }, 1500);
        }
      }
    }
    void refresh();
    return () => {
      disposed = true;
      controller.abort();
    };
  }, [expanded]);

  function toggleAccepted(artifactId: string) {
    setAccepted((current) => {
      const next = new Set(current);
      if (next.has(artifactId)) {
        next.delete(artifactId);
      } else {
        next.add(artifactId);
      }
      return next;
    });
  }

  async function handleInstall() {
    if (plan === null || busy) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setCurrentOperation(await beginInstallation([...accepted]));
    } catch (cause) {
      setError(toError(cause, "安装确认失败"));
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    const current = operationRef.current;
    if (current === null || busy) {
      return;
    }
    setBusy(true);
    try {
      setCurrentOperation(await cancelInstallation(current.operationId));
    } catch (cause) {
      setError(toError(cause, "取消安装失败"));
    } finally {
      setBusy(false);
    }
  }

  async function handleStart() {
    setBusy(true);
    setError(null);
    try {
      const process = await startComfyUI();
      setStatus((current) =>
        current === null
          ? current
          : { ...current, process: { ...current.process, ...process } },
      );
    } catch (cause) {
      setError(toError(cause, "启动受管 ComfyUI 失败"));
    } finally {
      setBusy(false);
    }
  }

  async function handleStop() {
    setBusy(true);
    setError(null);
    try {
      const process = await stopComfyUI();
      setStatus((current) =>
        current === null
          ? current
          : { ...current, process: { ...current.process, ...process } },
      );
    } catch (cause) {
      setError(toError(cause, "停止受管 ComfyUI 失败"));
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleLog() {
    if (log !== null) {
      setLog(null);
      return;
    }
    try {
      setLog(await getComfyUILog(4096));
    } catch (cause) {
      setError(toError(cause, "读取日志失败"));
    }
  }

  const allAccepted =
    plan !== null &&
    plan.components.every((component) => accepted.has(component.artifactId));
  const activeInstallation =
    operation !== null && ACTIVE_INSTALLATION_STATES.has(operation.state);

  return (
    <section className="panel inference-panel" aria-labelledby="inference-title">
      <div className="section-heading">
        <span className="step-marker" aria-hidden="true">环境</span>
        <div>
          <p className="eyebrow">推理环境</p>
          <h2 id="inference-title">受管 ComfyUI 与模型配置</h2>
          <p>仅显示安装/状态/启停控制；本阶段不提供生成按钮。</p>
        </div>
      </div>

      <button
        type="button"
        className="retry-button"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
      >
        {expanded ? "收起推理环境" : "展开推理环境"}
      </button>

      {expanded && (
        <div className="inference-body" aria-busy={busy}>
          {error !== null && (
            <p className="inline-error" role="alert">
              {error.userMessage}
            </p>
          )}
          {status === null || plan === null ? (
            <p className="loading-note">正在读取推理环境状态…</p>
          ) : (
            <>
              <EnvironmentSummary status={status} />
              <ComponentList
                plan={plan}
                accepted={accepted}
                onToggle={toggleAccepted}
              />
              <p className="download-totals" aria-label="下载总量">
                下载总量 {formatDecimalGb(plan.totalDownloadBytes)} GB（
                {formatDecimalGib(plan.totalDownloadBytes)} GiB），临时解压余量{" "}
                {formatDecimalGb(plan.temporaryHeadroomBytes)} GB
              </p>
              {plan.blockers.length > 0 && (
                <ul className="blocker-list">
                  {plan.blockers.map((blocker) => (
                    <li key={blocker}>{blocker}</li>
                  ))}
                </ul>
              )}
              {operation !== null && (
                <p className="progress-note" role="status">
                  安装操作 {operation.state}（修订 {operation.revision}）
                  {operation.error !== null &&
                    `：${operation.error.code} ${operation.error.message}`}
                </p>
              )}
              <div className="comfy-controls">
                <button
                  type="button"
                  disabled={!allAccepted || busy || !plan.canInstall}
                  onClick={() => void handleInstall()}
                >
                  确认并开始安装
                </button>
                <button
                  type="button"
                  disabled={!activeInstallation || busy}
                  onClick={() => void handleCancel()}
                >
                  取消安装
                </button>
                <button
                  type="button"
                  disabled={busy || status.process.state === "ready"}
                  onClick={() => void handleStart()}
                >
                  启动受管 ComfyUI
                </button>
                <button
                  type="button"
                  disabled={busy || status.process.state === "stopped"}
                  onClick={() => void handleStop()}
                >
                  停止受管 ComfyUI
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void handleToggleLog()}
                >
                  {log === null ? "查看受管日志" : "收起日志"}
                </button>
              </div>
              {log !== null && (
                <pre className="managed-log">{log || "（日志为空）"}</pre>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

function EnvironmentSummary({ status }: { readonly status: InferenceStatus }) {
  const { environment, runtime, process } = status;
  return (
    <div className="environment-summary" aria-label="主机与运行时状态">
      <p>
        主机支持：{environment.supported ? "是" : "否"}
        {environment.blockingIssues.length > 0 &&
          `（${environment.blockingIssues.join("、")}）`}
      </p>
      {environment.gpuName !== null && (
        <p>
          GPU：{environment.gpuName}，驱动 {environment.driverVersion}
        </p>
      )}
      <p>
        运行时：{runtime.state}
        {runtime.selectedVersion !== null && `（${runtime.selectedVersion}）`}
      </p>
      <p>
        受管进程：{process.state}
        {process.errors.length > 0 && `（${process.errors.join("、")}）`}
      </p>
    </div>
  );
}

function ComponentList({
  plan,
  accepted,
  onToggle,
}: {
  readonly plan: InstallPlan;
  readonly accepted: ReadonlySet<string>;
  readonly onToggle: (artifactId: string) => void;
}) {
  return (
    <ul className="license-list" aria-label="组件与许可清单">
      {plan.components.map((component) => (
        <li key={component.artifactId}>
          <label>
            <input
              type="checkbox"
              checked={accepted.has(component.artifactId)}
              onChange={() => onToggle(component.artifactId)}
            />
            <strong>{component.artifactId}</strong>
          </label>
          <span>
            {formatDecimalGb(component.byteSize)} GB（
            {formatDecimalGib(component.byteSize)} GiB），状态 {component.state}
          </span>
          <a
            href={component.licenseSourceUrl}
            target="_blank"
            rel="noreferrer"
          >
            {component.licenseName}
          </a>
          <a href={component.sourceUrl} target="_blank" rel="noreferrer">
            来源
          </a>
        </li>
      ))}
    </ul>
  );
}

function toError(cause: unknown, fallback: string): ApiRequestError {
  if (cause instanceof ApiRequestError) {
    return cause;
  }
  return new ApiRequestError({
    code: "UNEXPECTED_INFERENCE_ERROR",
    stage: "displaying_inference",
    userMessage: fallback,
    recommendedActions: ["重试操作；若问题持续，请查看应用日志"],
    technicalDetails: cause instanceof Error ? cause.message : null,
  });
}
