import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import ProjectList from "./ProjectList";

afterEach(cleanup);

it("renders restorable projects and emits the selected project ID", async () => {
  const onSelect = vi.fn();
  const project = {
    projectId: "6fda5078-1246-4cac-91e8-541808da14f4",
    projectName: "现有材质项目",
    edition: "java" as const,
    javaPackFormat: 34,
    catalogId: "java-dev-format-34",
    createdAt: "2026-07-29T10:00:00+08:00",
    updatedAt: "2026-07-29T11:00:00+08:00",
  };
  render(
    <ProjectList
      projects={[project]}
      selectedProjectId={null}
      onSelect={onSelect}
    />,
  );

  const button = screen.getByRole("button", { name: /现有材质项目/ });
  expect(button).toHaveTextContent("资源格式 34");
  await userEvent.setup().click(button);

  expect(onSelect).toHaveBeenCalledWith(project.projectId);
});

it("marks the current project without owning any loading state", () => {
  const projectId = "6fda5078-1246-4cac-91e8-541808da14f4";
  render(
    <ProjectList
      projects={[
        {
          projectId,
          projectName: "当前项目",
          edition: "java",
          javaPackFormat: 34,
          catalogId: "java-dev-format-34",
          createdAt: "2026-07-29T10:00:00+08:00",
          updatedAt: "2026-07-29T11:00:00+08:00",
        },
      ]}
      selectedProjectId={projectId}
      onSelect={() => undefined}
    />,
  );

  expect(screen.getByRole("button", { name: /当前项目/ })).toHaveAttribute(
    "aria-current",
    "true",
  );
});

it("allows an unbroken project name to wrap inside a narrow list", () => {
  const longName = "UnbrokenProjectName".repeat(12);
  render(
    <ProjectList
      projects={[
        {
          projectId: "6fda5078-1246-4cac-91e8-541808da14f4",
          projectName: longName,
          edition: "java",
          javaPackFormat: 34,
          catalogId: "java-dev-format-34",
          createdAt: "2026-07-29T10:00:00+08:00",
          updatedAt: "2026-07-29T11:00:00+08:00",
        },
      ]}
      selectedProjectId={null}
      onSelect={() => undefined}
    />,
  );

  const name = screen.getByText(longName);
  expect(name.tagName).toBe("STRONG");
  expect(name).toHaveStyle({ minWidth: "0", overflowWrap: "anywhere" });
});
