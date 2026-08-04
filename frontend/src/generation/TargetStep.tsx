import type { GenerationTargetOption } from "./types";

export default function TargetStep({
  search,
  onSearchChange,
  targets,
  selectedSemanticId,
  onSelect,
  onNext,
}: {
  readonly search: string;
  readonly onSearchChange: (value: string) => void;
  readonly targets: readonly GenerationTargetOption[];
  readonly selectedSemanticId: string | null;
  readonly onSelect: (semanticId: string) => void;
  readonly onNext: () => void;
}) {
  return (
    <section className="panel generation-panel" aria-labelledby="generation-target-title">
      <div className="section-heading">
        <span className="step-marker" aria-hidden="true">02</span>
        <div>
          <h2 id="generation-target-title">选择目标</h2>
          <p>只显示当前缺失且可生成的普通方块。</p>
        </div>
      </div>

      <div className="field">
        <label htmlFor="target-search">搜索未覆盖目标</label>
        <input
          id="target-search"
          type="search"
          role="searchbox"
          value={search}
          onChange={(event) => onSearchChange(event.currentTarget.value)}
        />
      </div>

      <div className="target-list" role="radiogroup" aria-label="可生成目标">
        {targets.map((target) => (
          <label className="choice-card" key={target.semanticId}>
            <input
              checked={selectedSemanticId === target.semanticId}
              name="generation-target"
              type="radio"
              onChange={() => onSelect(target.semanticId)}
            />
            <span>{target.displayName}</span>
            <code>{target.relativePath}</code>
          </label>
        ))}
      </div>

      <div className="wizard-actions">
        <button
          disabled={selectedSemanticId === null}
          type="button"
          onClick={onNext}
        >
          下一步：参考图与描述
        </button>
      </div>
    </section>
  );
}
