import type { ProjectSummary } from "./api";

export default function ProjectList({
  projects,
  selectedProjectId,
  onSelect,
}: {
  readonly projects: readonly ProjectSummary[];
  readonly selectedProjectId: string | null;
  readonly onSelect: (projectId: string) => void;
}) {
  return (
    <nav className="project-list" aria-labelledby="project-list-title">
      <div className="section-heading compact-heading">
        <span className="step-marker" aria-hidden="true">02</span>
        <div>
          <h2 id="project-list-title">已有项目</h2>
          <p>直接打开本地项目，无需重新导入 ZIP。</p>
        </div>
      </div>

      {projects.length === 0 ? (
        <p className="empty-state">还没有可恢复的项目。</p>
      ) : (
        <ul className="project-items">
          {projects.map((project) => {
            const selected = project.projectId === selectedProjectId;
            return (
              <li key={project.projectId}>
                <button
                  className={selected ? "project-button selected" : "project-button"}
                  type="button"
                  aria-current={selected ? "true" : undefined}
                  onClick={() => onSelect(project.projectId)}
                >
                  <strong style={{ minWidth: 0, overflowWrap: "anywhere" }}>
                    {project.projectName}
                  </strong>
                  <span>资源格式 {project.javaPackFormat}</span>
                  <time dateTime={project.updatedAt}>
                    更新于 {formatTimestamp(project.updatedAt)}
                  </time>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </nav>
  );
}

function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
