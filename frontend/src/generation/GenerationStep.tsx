import type { GenerationOptions } from "./types";

export default function GenerationStep({
  options,
  resolution,
  parallelism,
  userNegativePrompt,
  hasStyleReference,
  hasStructureReference,
  denoise,
  styleWeight,
  error,
  creating,
  onResolutionChange,
  onParallelismChange,
  onNegativePromptChange,
  onDenoiseChange,
  onStyleWeightChange,
  onBack,
  onCreateAndStart,
}: {
  readonly options: GenerationOptions;
  readonly resolution: 16 | 32 | 64;
  readonly parallelism: 1 | 2 | 4;
  readonly userNegativePrompt: string;
  readonly hasStyleReference: boolean;
  readonly hasStructureReference: boolean;
  readonly denoise: number | null;
  readonly styleWeight: number | null;
  readonly error: string | null;
  readonly creating: boolean;
  readonly onResolutionChange: (value: 16 | 32 | 64) => void;
  readonly onParallelismChange: (value: 1 | 2 | 4) => void;
  readonly onNegativePromptChange: (value: string) => void;
  readonly onDenoiseChange: (value: number | null) => void;
  readonly onStyleWeightChange: (value: number | null) => void;
  readonly onBack: () => void;
  readonly onCreateAndStart: () => void;
}) {
  return (
    <section className="panel generation-panel" aria-labelledby="generation-config-title">
      <div className="section-heading">
        <span className="step-marker" aria-hidden="true">04</span>
        <div>
          <h2 id="generation-config-title">生成配置</h2>
          <p>固定生成 4 个候选，不暴露 seed、workflow 或任意模型控制。</p>
        </div>
      </div>

      <div className="field">
        <label htmlFor="generation-resolution">分辨率</label>
        <select
          id="generation-resolution"
          value={resolution}
          onChange={(event) =>
            onResolutionChange(Number(event.currentTarget.value) as 16 | 32 | 64)
          }
        >
          {[16, 32, 64].map((value) => (
            <option key={value} value={value}>
              {value} × {value}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label htmlFor="generation-parallelism">并行方式</label>
        <select
          id="generation-parallelism"
          value={parallelism}
          onChange={(event) =>
            onParallelismChange(Number(event.currentTarget.value) as 1 | 2 | 4)
          }
        >
          {options.allowedParallelism.map((value) => (
            <option key={value} value={value}>
              并行 {value}
            </option>
          ))}
        </select>
      </div>

      <p className="field-hint">固定生成 4 个候选</p>
      <ul aria-label="资源提示" className="hint-list">
        {options.resourceHints.map((hint) => (
          <li key={hint.parallelism}>
            并行 {hint.parallelism}：显存约 {hint.peakVramMiB} MiB，进程内存约{" "}
            {hint.peakProcessRamMiB} MiB，系统内存约 {hint.peakSystemRamMiB} MiB，
            参考耗时 {hint.elapsedSeconds} 秒
          </li>
        ))}
      </ul>

      <p>已验证模型配置：{options.profile.profileId} v{options.profile.profileVersion}</p>

      {error !== null && (
        <p className="validation-error" role="alert">{error}</p>
      )}

      <details>
        <summary>高级设置</summary>
        <div className="field">
          <label htmlFor="negative-prompt">负面提示词</label>
          <textarea
            id="negative-prompt"
            value={userNegativePrompt}
            onChange={(event) => onNegativePromptChange(event.currentTarget.value)}
          />
        </div>
        {hasStructureReference && (
          <div className="field">
            <label htmlFor="generation-denoise">结构保持强度</label>
            <input
              id="generation-denoise"
              max="1"
              min="0"
              step="0.01"
              type="number"
              value={denoise ?? ""}
              onChange={(event) =>
                onDenoiseChange(
                  event.currentTarget.value === ""
                    ? null
                    : Number(event.currentTarget.value),
                )
              }
            />
          </div>
        )}
        {hasStyleReference && (
          <div className="field">
            <label htmlFor="generation-style-weight">风格强度</label>
            <input
              id="generation-style-weight"
              max="1"
              min="0"
              step="0.01"
              type="number"
              value={styleWeight ?? ""}
              onChange={(event) =>
                onStyleWeightChange(
                  event.currentTarget.value === ""
                    ? null
                    : Number(event.currentTarget.value),
                )
              }
            />
          </div>
        )}
      </details>

      <div className="wizard-actions">
        <button type="button" onClick={onBack}>
          上一步：参考图与描述
        </button>
        <button disabled={creating} type="button" onClick={onCreateAndStart}>
          {creating ? "正在创建并开始生成…" : "创建并开始生成"}
        </button>
      </div>
    </section>
  );
}
