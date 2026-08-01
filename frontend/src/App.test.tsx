import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const projectId = "6fda5078-1246-4cac-91e8-541808da14f4";

const manifest = {
  schema_version: 2,
  project_id: projectId,
  project_name: "测试项目",
  edition: "java",
  java_pack_format: 34,
  supported_formats: [34, 35],
  catalog_id: "java-dev-format-34",
  source_sha256: "a".repeat(64),
  created_at: "2026-07-21T10:00:00+08:00",
  updated_at: "2026-07-21T10:00:00+08:00",
  default_resolution: 16,
  default_parallelism: 1,
  style_references: [],
};

const coverage = {
  catalog_id: "java-dev-format-34",
  catalog_status: "development_fixture",
  covered_count: 1,
  missing_count: 1,
  unknown_paths: ["assets/example/textures/block/custom.png"],
  items: [
    {
      semantic_id: "minecraft:stone",
      display_name: "Stone",
      relative_path: "assets/minecraft/textures/block/stone.png",
      mvp_eligible: true,
      status: "covered",
    },
    {
      semantic_id: "minecraft:deepslate",
      display_name: "Deepslate",
      relative_path: "assets/minecraft/textures/block/deepslate.png",
      mvp_eligible: true,
      status: "missing",
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  return { promise, reject, resolve };
}

const recoveryReport = {
  project_count: 0,
  job_count: 0,
  recovered_job_count: 0,
  issues: [],
  completed_at: "2026-07-29T12:00:00Z",
};

function renderImportApp() {
  const operationFetch = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/projects" && init === undefined) {
        return Promise.resolve(jsonResponse([]));
      }
      if (url === "/api/system/recovery") {
        return Promise.resolve(jsonResponse(recoveryReport));
      }
      return operationFetch.call(globalThis, input, init);
    }),
  );
  return render(<App />);
}

function mockSuccessfulImport(): void {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(manifest, 201))
      .mockResolvedValueOnce(jsonResponse(coverage)),
  );
}

async function completeForm(
  projectName = "测试项目",
  filename = "pack.zip",
) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("项目名称"), projectName);
  await user.upload(
    screen.getByLabelText("ZIP 资源包"),
    new File(["synthetic pack"], filename, { type: "application/zip" }),
  );
  return user;
}

describe("资源包导入与覆盖摘要", () => {
  it("marks the hero title for a desktop-specific layout rule", () => {
    renderImportApp();

    expect(
      screen.getByRole("heading", { level: 1, name: "Java 资源包项目" }),
    ).toHaveClass("hero-title");
  });

  it("导入 ZIP 后显示资源格式和覆盖统计", async () => {
    mockSuccessfulImport();
    renderImportApp();
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    expect(await screen.findByLabelText("覆盖统计")).toHaveTextContent(
      "资源格式 34",
    );
    expect(screen.getByText("已覆盖 1")).toBeInTheDocument();
    expect(screen.getByText("未覆盖 1")).toBeInTheDocument();
    expect(screen.getByText("Deepslate")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /测试项目/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("显示开发目录警告、缺失路径和未知文件数量", async () => {
    mockSuccessfulImport();
    renderImportApp();
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    expect(
      await screen.findByText(/开发测试目录/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("assets/minecraft/textures/block/deepslate.png"),
    ).toBeInTheDocument();
    expect(screen.getByText("未知\/自定义 1")).toBeInTheDocument();
  });

  it("把稳定 API 错误信封显示为可读警报", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            code: "UNSAFE_PACK_PATH",
            stage: "importing",
            user_message: "不安全的资源包路径",
            recommended_actions: ["移除危险路径后重试"],
            technical_details: null,
          },
          400,
        ),
      ),
    );
    renderImportApp();
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "不安全的资源包路径",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("移除危险路径后重试");
  });

  it.each([
    ["网络失败", () => Promise.reject(new TypeError("Failed to fetch")), "无法连接到本地服务"],
    ["非 JSON 响应", () => Promise.resolve(new Response("bad gateway", { status: 502 })), "本地服务返回了无法识别的响应"],
  ])("%s 时显示可读错误", async (_label, response, expectedMessage) => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(response));
    renderImportApp();
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(expectedMessage);
  });

  it("有效 JSON 的项目清单形状错误时显示无法识别的响应", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ ...manifest, project_id: 42 }, 201),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderImportApp();
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "本地服务返回了无法识别的响应",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("覆盖响应形状错误时保留项目并可只重试覆盖分析", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(manifest, 201))
      .mockResolvedValueOnce(
        jsonResponse({ ...coverage, covered_count: "one" }),
      )
      .mockResolvedValueOnce(jsonResponse(coverage));
    vi.stubGlobal("fetch", fetchMock);
    renderImportApp();
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("本地服务返回了无法识别的响应");
    expect(alert).toHaveTextContent("项目已创建");
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "重试覆盖分析" }));

    expect(await screen.findByLabelText("覆盖统计")).toHaveTextContent(
      "资源格式 34",
    );
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      `/api/projects/${projectId}/coverage`,
    );
  });

  it("覆盖请求失败时保留已创建项目并只重试覆盖分析", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(manifest, 201))
      .mockRejectedValueOnce(new TypeError("Failed to fetch coverage"))
      .mockResolvedValueOnce(jsonResponse(coverage));
    vi.stubGlobal("fetch", fetchMock);
    renderImportApp();
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("项目已创建");
    expect(alert).toHaveTextContent("无需重新导入 ZIP");
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "重试覆盖分析" }));

    expect(await screen.findByLabelText("覆盖统计")).toHaveTextContent(
      "资源格式 34",
    );
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      `/api/projects/${projectId}/coverage`,
    );
  });

  it("拒绝非 ZIP 文件名并将可读错误关联到文件输入", async () => {
    renderImportApp();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("项目名称"), "测试项目");
    const fileInput = screen.getByLabelText("ZIP 资源包");
    const button = screen.getByRole("button", { name: "导入并分析" });

    await user.upload(
      fileInput,
      new File(["not a zip"], "pack.jar", { type: "application/zip" }),
    );

    expect(button).toBeDisabled();
    expect(fileInput).toHaveAttribute("aria-invalid", "true");
    expect(fileInput).toHaveAccessibleDescription(/扩展名为 \.zip/);
    expect(screen.getByText("请选择扩展名为 .zip 的资源包文件")).toBeVisible();

    await user.upload(
      fileInput,
      new File(["zip"], "VALID-PACK.ZIP", { type: "application/zip" }),
    );

    expect(button).toBeEnabled();
    expect(fileInput).toHaveAttribute("aria-invalid", "false");
    expect(
      screen.queryByText("请选择扩展名为 .zip 的资源包文件"),
    ).not.toBeInTheDocument();
  });

  it("信息不完整时禁用提交，导入期间禁用表单并标记忙碌", async () => {
    let resolveImport!: (response: Response) => void;
    const pendingImport = new Promise<Response>((resolve) => {
      resolveImport = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockReturnValueOnce(pendingImport)
        .mockResolvedValueOnce(jsonResponse(coverage)),
    );
    renderImportApp();
    const form = screen.getByRole("form", { name: "资源包导入" });
    const button = screen.getByRole("button", { name: "导入并分析" });

    expect(button).toBeDisabled();
    const user = await completeForm();
    expect(button).toBeEnabled();
    await user.click(button);

    await waitFor(() => expect(form).toHaveAttribute("aria-busy", "true"));
    expect(button).toBeDisabled();
    expect(screen.getByLabelText("项目名称")).toBeDisabled();
    expect(screen.getByLabelText("ZIP 资源包")).toBeDisabled();

    resolveImport(jsonResponse(manifest, 201));
    expect(await screen.findByLabelText("覆盖统计")).toHaveTextContent(
      "资源格式 34",
    );
  });

  it("项目名称按 Unicode code point 与后端一致计数", async () => {
    mockSuccessfulImport();
    renderImportApp();
    const input = screen.getByLabelText("项目名称");
    const fileInput = screen.getByLabelText("ZIP 资源包");
    const button = screen.getByRole("button", { name: "导入并分析" });
    const user = userEvent.setup();

    expect(input).toHaveAttribute("maxLength", "256");
    fireEvent.change(input, { target: { value: "😀".repeat(128) } });
    await user.upload(
      fileInput,
      new File(["zip"], "pack.zip", { type: "application/zip" }),
    );
    expect(button).toBeEnabled();

    fireEvent.change(input, { target: { value: "😀".repeat(129) } });
    expect(button).toBeDisabled();

    fireEvent.change(input, { target: { value: "a".repeat(128) } });
    expect(button).toBeEnabled();
    fireEvent.change(input, { target: { value: "a".repeat(129) } });
    expect(button).toBeDisabled();
  });

  it.each([
    ["非规范 UUID", { ...manifest, project_id: projectId.toUpperCase() }],
    ["错误 SHA-256", { ...manifest, source_sha256: "not-a-hash" }],
    ["不可解析时间", { ...manifest, created_at: "not-a-time" }],
    ["无时区时间", { ...manifest, created_at: "2026-07-21T10:00:00" }],
    ["非 RFC3339 时间", { ...manifest, created_at: "2026-07-21 10:00:00Z" }],
    ["小数资源格式", { ...manifest, java_pack_format: 34.5 }],
    ["负数资源格式", { ...manifest, java_pack_format: -1 }],
  ])("拒绝%s的成功项目响应", async (_label, invalidManifest) => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(invalidManifest, 201));
    vi.stubGlobal("fetch", fetchMock);
    renderImportApp();
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "本地服务返回了无法识别的响应",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["负数覆盖计数", { ...coverage, covered_count: -1 }],
    ["小数缺失计数", { ...coverage, missing_count: 1.5 }],
  ])("拒绝%s的成功覆盖响应", async (_label, invalidCoverage) => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse(manifest, 201))
        .mockResolvedValueOnce(jsonResponse(invalidCoverage)),
    );
    renderImportApp();
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "本地服务返回了无法识别的响应",
    );
  });

  it("新的导入失败时保留上一次的覆盖摘要", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse(manifest, 201))
        .mockResolvedValueOnce(jsonResponse(coverage))
        .mockResolvedValueOnce(
          jsonResponse(
            {
              code: "UNSAFE_PACK_PATH",
              stage: "importing",
              user_message: "不安全的资源包路径",
              recommended_actions: [],
              technical_details: null,
            },
            400,
          ),
        ),
    );
    renderImportApp();
    const user = await completeForm();
    const button = screen.getByRole("button", { name: "导入并分析" });
    await user.click(button);
    expect(await screen.findByLabelText("覆盖统计")).toHaveTextContent(
      "资源格式 34",
    );

    const nameInput = screen.getByLabelText("项目名称");
    await user.clear(nameInput);
    await user.type(nameInput, "第二个项目");
    await user.upload(
      screen.getByLabelText("ZIP 资源包"),
      new File(["unsafe"], "unsafe.zip", { type: "application/zip" }),
    );
    await user.click(button);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "不安全的资源包路径",
    );
    expect(screen.getByLabelText("覆盖统计")).toHaveTextContent("资源格式 34");
    expect(screen.getByText("Deepslate")).toBeInTheDocument();
  });
});

describe("项目恢复与只读任务历史", () => {
  const jobId = "0f6fb74b-5d0f-46b0-bf03-2fb41aa83694";
  const projectSummary = {
    project_id: projectId,
    project_name: "恢复项目",
    edition: "java",
    java_pack_format: 34,
    catalog_id: "java-dev-format-34",
    created_at: "2026-07-21T10:00:00+08:00",
    updated_at: "2026-07-29T11:00:00+08:00",
  };
  const jobSummary = {
    job_id: jobId,
    project_id: projectId,
    retry_of_job_id: null,
    target_semantic_id: "minecraft:deepslate",
    target_display_name: "Deepslate",
    resolution: 16,
    parallelism: 1,
    status: "queued",
    revision: 0,
    candidate_statuses: ["pending", "pending", "pending", "pending"],
    created_at: "2026-07-29T10:00:00+08:00",
    updated_at: "2026-07-29T10:00:00+08:00",
  };
  const jobDetail = {
    request: {
      schema_version: 1,
      job_id: jobId,
      project_id: projectId,
      retry_of_job_id: null,
      catalog_id: "java-dev-format-34",
      target_semantic_id: "minecraft:deepslate",
      target_display_name: "Deepslate",
      target_relative_path: "assets/minecraft/textures/block/deepslate.png",
      prompt: "cold stone",
      resolution: 16,
      parallelism: 1,
      style_references: ["assets/minecraft/textures/block/stone.png"],
      structure_reference: null,
      seeds: [11, 22, 33, 44],
      created_at: "2026-07-29T10:00:00+08:00",
    },
    state: {
      schema_version: 1,
      job_id: jobId,
      project_id: projectId,
      revision: 0,
      status: "queued",
      candidates: [11, 22, 33, 44].map((seed, candidate_index) => ({
        candidate_index,
        seed,
        status: "pending",
        failure: null,
        started_at: null,
        finished_at: null,
      })),
      failure: null,
      created_at: "2026-07-29T10:00:00+08:00",
      updated_at: "2026-07-29T10:00:00+08:00",
      started_at: null,
      finished_at: null,
    },
  };

  function installRestorationFetch({
    recovery = recoveryReport,
    failCoverageOnce = false,
    failImport = false,
    listedJob = jobSummary,
    detailedJob = jobDetail,
  }: {
    readonly recovery?: unknown;
    readonly failCoverageOnce?: boolean;
    readonly failImport?: boolean;
    readonly listedJob?: unknown;
    readonly detailedJob?: unknown;
  } = {}) {
    let coverageAttempts = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/projects") {
        return Promise.resolve(jsonResponse([projectSummary]));
      }
      if (url === "/api/system/recovery") {
        return Promise.resolve(jsonResponse(recovery));
      }
      if (url === "/api/projects/import" && failImport) {
        return Promise.resolve(
          jsonResponse(
            {
              code: "UNSAFE_PACK_PATH",
              stage: "importing",
              user_message: "新资源包不安全",
              recommended_actions: ["修复 ZIP 后重试"],
              technical_details: null,
            },
            400,
          ),
        );
      }
      if (url === `/api/projects/${projectId}`) {
        return Promise.resolve(jsonResponse(manifest));
      }
      if (url === `/api/projects/${projectId}/coverage`) {
        coverageAttempts += 1;
        if (failCoverageOnce && coverageAttempts === 1) {
          return Promise.reject(new TypeError("coverage offline"));
        }
        return Promise.resolve(jsonResponse(coverage));
      }
      if (url === `/api/projects/${projectId}/jobs`) {
        return Promise.resolve(jsonResponse([listedJob]));
      }
      if (url === `/api/projects/${projectId}/jobs/${jobId}`) {
        return Promise.resolve(jsonResponse(detailedJob));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("启动时列出已有项目，选择后加载覆盖与任务历史", async () => {
    const fetchMock = installRestorationFetch();
    render(<App />);

    const projectButton = await screen.findByRole("button", { name: /恢复项目/ });
    expect(screen.queryByLabelText("覆盖统计")).not.toBeInTheDocument();

    await userEvent.setup().click(projectButton);

    expect(await screen.findByLabelText("覆盖统计")).toHaveTextContent(
      "资源格式 34",
    );
    expect(screen.getByRole("article", { name: /Deepslate/ })).toHaveTextContent(
      "候选 4",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/projects/${projectId}/jobs`,
      undefined,
    );
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/projects/${projectId}/jobs/${jobId}`,
      undefined,
    );
  });

  it("恢复问题显示为警告且不隐藏有效项目", async () => {
    installRestorationFetch({
      recovery: {
        ...recoveryReport,
        project_count: 1,
        job_count: 1,
        issues: [
          {
            project_id: projectId,
            job_id: jobId,
            code: "CORRUPT_JOB_RECORD",
            user_message: "一个任务记录损坏，已隔离",
          },
        ],
      },
    });
    render(<App />);

    expect(await screen.findByRole("button", { name: /恢复项目/ })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("一个任务记录损坏，已隔离");
  });

  it("项目请求失败时保留选择并提供只重试当前项目的操作", async () => {
    installRestorationFetch({ failCoverageOnce: true });
    render(<App />);
    const user = userEvent.setup();
    const projectButton = await screen.findByRole("button", { name: /恢复项目/ });

    await user.click(projectButton);

    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接到本地服务");
    expect(projectButton).toHaveAttribute("aria-current", "true");

    await user.click(screen.getByRole("button", { name: "重试当前项目" }));

    expect(await screen.findByLabelText("覆盖统计")).toHaveTextContent(
      "资源格式 34",
    );
    expect(projectButton).toHaveAttribute("aria-current", "true");
  });

  it("新导入失败时保留原项目选择、覆盖和任务历史", async () => {
    installRestorationFetch({ failImport: true });
    render(<App />);
    const user = userEvent.setup();
    const projectButton = await screen.findByRole("button", { name: /恢复项目/ });
    await user.click(projectButton);
    expect(await screen.findByRole("article", { name: /Deepslate/ })).toBeVisible();
    await completeForm("失败的新项目", "unsafe.zip");

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("新资源包不安全");
    expect(projectButton).toHaveAttribute("aria-current", "true");
    expect(screen.getByLabelText("覆盖统计")).toHaveTextContent("资源格式 34");
    expect(screen.getByRole("article", { name: /Deepslate/ })).toBeVisible();
  });

  it("列表与详情竞态时用详情快照展示可变任务状态", async () => {
    const interruptedFailure = {
      code: "JOB_INTERRUPTED",
      stage: "recovery",
      user_message: "任务在启动恢复时被中断",
      recommended_actions: ["重新提交任务"],
      technical_details: null,
      log_reference: null,
    };
    installRestorationFetch({
      detailedJob: {
        ...jobDetail,
        state: {
          ...jobDetail.state,
          revision: 1,
          status: "failed",
          candidates: [11, 22, 33, 44].map((seed, candidate_index) => ({
            candidate_index,
            seed,
            status: "canceled",
            failure: null,
            started_at: null,
            finished_at: "2026-07-29T10:05:00+08:00",
          })),
          failure: interruptedFailure,
          updated_at: "2026-07-29T10:05:00+08:00",
          started_at: "2026-07-29T10:00:00+08:00",
          finished_at: "2026-07-29T10:05:00+08:00",
        },
      },
    });
    render(<App />);
    await userEvent.setup().click(
      await screen.findByRole("button", { name: /恢复项目/ }),
    );

    const job = await screen.findByRole("article", { name: /Deepslate/ });
    expect(job).toHaveTextContent("失败");
    expect(job).toHaveTextContent("候选 4：已取消 4");
    expect(job).not.toHaveTextContent("排队中");
    expect(job).not.toHaveTextContent("待处理 4");
    expect(job).toHaveTextContent("应用重启时此任务仍在运行");
  });

  it("任务摘要创建时间与请求一致时允许状态记录使用独立创建时间", async () => {
    installRestorationFetch({
      detailedJob: {
        ...jobDetail,
        state: {
          ...jobDetail.state,
          created_at: "2026-07-29T10:01:00+08:00",
          updated_at: "2026-07-29T10:01:00+08:00",
        },
      },
    });
    render(<App />);

    await userEvent.setup().click(
      await screen.findByRole("button", { name: /恢复项目/ }),
    );

    expect(
      await screen.findByRole("article", { name: /Deepslate/ }),
    ).toBeVisible();
    expect(screen.queryByText("当前项目读取失败")).not.toBeInTheDocument();
  });

  it("任务摘要与请求不可变字段冲突时显示无效 API 响应", async () => {
    installRestorationFetch({
      listedJob: {
        ...jobSummary,
        created_at: "2026-07-29T09:59:00+08:00",
        updated_at: "2026-07-29T09:59:00+08:00",
      },
    });
    render(<App />);

    await userEvent.setup().click(
      await screen.findByRole("button", { name: /恢复项目/ }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("当前项目读取失败");
    expect(alert).toHaveTextContent("本地服务返回了无法识别的响应");
    expect(alert).not.toHaveTextContent("导入时发生未知错误");
  });

  it("旧项目面板响应不能覆盖刚成功导入的新项目", async () => {
    const oldManifest = deferred<Response>();
    const oldCoverage = deferred<Response>();
    const oldJobs = deferred<Response>();
    const newProjectId = "dce1d8fa-3e28-48f5-81b2-1776371b7832";
    const newManifest = {
      ...manifest,
      project_id: newProjectId,
      project_name: "新导入项目",
      source_sha256: "b".repeat(64),
      created_at: "2026-07-29T13:00:00+08:00",
      updated_at: "2026-07-29T13:00:00+08:00",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/projects") {
          return Promise.resolve(jsonResponse([projectSummary]));
        }
        if (url === "/api/system/recovery") {
          return Promise.resolve(jsonResponse(recoveryReport));
        }
        if (url === `/api/projects/${projectId}`) {
          return oldManifest.promise;
        }
        if (url === `/api/projects/${projectId}/coverage`) {
          return oldCoverage.promise;
        }
        if (url === `/api/projects/${projectId}/jobs`) {
          return oldJobs.promise;
        }
        if (url === "/api/projects/import") {
          return Promise.resolve(jsonResponse(newManifest, 201));
        }
        if (url === `/api/projects/${newProjectId}/coverage`) {
          return Promise.resolve(jsonResponse(coverage));
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /恢复项目/ }));
    await completeForm("新导入项目", "new.zip");

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    const newButton = await screen.findByRole("button", { name: /新导入项目/ });
    expect(newButton).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("heading", { name: "新导入项目" })).toBeVisible();

    await act(async () => {
      oldManifest.resolve(jsonResponse(manifest));
      oldCoverage.resolve(jsonResponse(coverage));
      oldJobs.resolve(jsonResponse([]));
      await Promise.all([
        oldManifest.promise,
        oldCoverage.promise,
        oldJobs.promise,
      ]);
    });

    expect(newButton).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("heading", { name: "新导入项目" })).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "测试项目" }),
    ).not.toBeInTheDocument();
  });

  it("导入覆盖失败迟到时不能把错误附着到后来选择的项目", async () => {
    const importedCoverage = deferred<Response>();
    const newProjectId = "dce1d8fa-3e28-48f5-81b2-1776371b7832";
    const newManifest = {
      ...manifest,
      project_id: newProjectId,
      project_name: "新导入项目",
      source_sha256: "b".repeat(64),
      created_at: "2026-07-29T13:00:00+08:00",
      updated_at: "2026-07-29T13:00:00+08:00",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/projects") {
          return Promise.resolve(jsonResponse([projectSummary]));
        }
        if (url === "/api/system/recovery") {
          return Promise.resolve(jsonResponse(recoveryReport));
        }
        if (url === "/api/projects/import") {
          return Promise.resolve(jsonResponse(newManifest, 201));
        }
        if (url === `/api/projects/${newProjectId}/coverage`) {
          return importedCoverage.promise;
        }
        if (url === `/api/projects/${projectId}`) {
          return Promise.resolve(jsonResponse(manifest));
        }
        if (url === `/api/projects/${projectId}/coverage`) {
          return Promise.resolve(jsonResponse(coverage));
        }
        if (url === `/api/projects/${projectId}/jobs`) {
          return Promise.resolve(jsonResponse([jobSummary]));
        }
        if (url === `/api/projects/${projectId}/jobs/${jobId}`) {
          return Promise.resolve(jsonResponse(jobDetail));
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    render(<App />);
    const user = await completeForm("新导入项目", "new.zip");
    await user.click(screen.getByRole("button", { name: "导入并分析" }));
    await user.click(await screen.findByRole("button", { name: /恢复项目/ }));
    expect(
      await screen.findByRole("article", { name: /Deepslate/ }),
    ).toBeVisible();

    await act(async () => {
      importedCoverage.reject(new TypeError("stale imported coverage"));
      await expect(importedCoverage.promise).rejects.toThrow(
        "stale imported coverage",
      );
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /恢复项目/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("覆盖重试失败迟到时不能把错误附着到后来选择的项目", async () => {
    const retriedCoverage = deferred<Response>();
    const newProjectId = "dce1d8fa-3e28-48f5-81b2-1776371b7832";
    const newManifest = {
      ...manifest,
      project_id: newProjectId,
      project_name: "新导入项目",
      source_sha256: "b".repeat(64),
      created_at: "2026-07-29T13:00:00+08:00",
      updated_at: "2026-07-29T13:00:00+08:00",
    };
    let importedCoverageAttempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/projects") {
          return Promise.resolve(jsonResponse([projectSummary]));
        }
        if (url === "/api/system/recovery") {
          return Promise.resolve(jsonResponse(recoveryReport));
        }
        if (url === "/api/projects/import") {
          return Promise.resolve(jsonResponse(newManifest, 201));
        }
        if (url === `/api/projects/${newProjectId}/coverage`) {
          importedCoverageAttempts += 1;
          return importedCoverageAttempts === 1
            ? Promise.reject(new TypeError("initial coverage failure"))
            : retriedCoverage.promise;
        }
        if (url === `/api/projects/${projectId}`) {
          return Promise.resolve(jsonResponse(manifest));
        }
        if (url === `/api/projects/${projectId}/coverage`) {
          return Promise.resolve(jsonResponse(coverage));
        }
        if (url === `/api/projects/${projectId}/jobs`) {
          return Promise.resolve(jsonResponse([jobSummary]));
        }
        if (url === `/api/projects/${projectId}/jobs/${jobId}`) {
          return Promise.resolve(jsonResponse(jobDetail));
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    render(<App />);
    const user = await completeForm("新导入项目", "new.zip");
    await user.click(screen.getByRole("button", { name: "导入并分析" }));
    await user.click(
      await screen.findByRole("button", { name: "重试覆盖分析" }),
    );
    await user.click(screen.getByRole("button", { name: /恢复项目/ }));
    expect(
      await screen.findByRole("article", { name: /Deepslate/ }),
    ).toBeVisible();

    await act(async () => {
      retriedCoverage.reject(new TypeError("stale retry failure"));
      await expect(retriedCoverage.promise).rejects.toThrow(
        "stale retry failure",
      );
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /恢复项目/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("迟到的启动项目列表与新导入项目合并且不清除选择", async () => {
    const initialProjects = deferred<Response>();
    const newProjectId = "dce1d8fa-3e28-48f5-81b2-1776371b7832";
    const newManifest = {
      ...manifest,
      project_id: newProjectId,
      project_name: "新导入项目",
      source_sha256: "b".repeat(64),
      created_at: "2026-07-29T13:00:00+08:00",
      updated_at: "2026-07-29T13:00:00+08:00",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/projects") {
          return initialProjects.promise;
        }
        if (url === "/api/system/recovery") {
          return Promise.resolve(jsonResponse(recoveryReport));
        }
        if (url === "/api/projects/import") {
          return Promise.resolve(jsonResponse(newManifest, 201));
        }
        if (url === `/api/projects/${newProjectId}/coverage`) {
          return Promise.resolve(jsonResponse(coverage));
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    render(<App />);
    const user = await completeForm("新导入项目", "new.zip");

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    const newButton = await screen.findByRole("button", { name: /新导入项目/ });
    expect(newButton).toHaveAttribute("aria-current", "true");

    await act(async () => {
      initialProjects.resolve(jsonResponse([projectSummary]));
      await initialProjects.promise;
    });

    expect(screen.getByRole("button", { name: /恢复项目/ })).toBeVisible();
    expect(newButton).toBeInTheDocument();
    expect(newButton).toHaveAttribute("aria-current", "true");
  });

  it("迟到的启动项目列表错误不能隐藏新导入项目", async () => {
    const initialProjects = deferred<Response>();
    const newProjectId = "dce1d8fa-3e28-48f5-81b2-1776371b7832";
    const newManifest = {
      ...manifest,
      project_id: newProjectId,
      project_name: "新导入项目",
      source_sha256: "b".repeat(64),
      created_at: "2026-07-29T13:00:00+08:00",
      updated_at: "2026-07-29T13:00:00+08:00",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/projects") {
          return initialProjects.promise;
        }
        if (url === "/api/system/recovery") {
          return Promise.resolve(jsonResponse(recoveryReport));
        }
        if (url === "/api/projects/import") {
          return Promise.resolve(jsonResponse(newManifest, 201));
        }
        if (url === `/api/projects/${newProjectId}/coverage`) {
          return Promise.resolve(jsonResponse(coverage));
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    render(<App />);
    const user = await completeForm("新导入项目", "new.zip");
    await user.click(screen.getByRole("button", { name: "导入并分析" }));
    const newButton = await screen.findByRole("button", { name: /新导入项目/ });

    await act(async () => {
      initialProjects.reject(new TypeError("stale project list failure"));
      await expect(initialProjects.promise).rejects.toThrow(
        "stale project list failure",
      );
    });

    expect(newButton).toBeInTheDocument();
    expect(newButton).toHaveAttribute("aria-current", "true");
    expect(screen.queryByText("项目列表读取失败")).not.toBeInTheDocument();
  });

  it("组件卸载后导入响应不得继续请求覆盖或提交状态", async () => {
    const imported = deferred<Response>();
    const newProjectId = "dce1d8fa-3e28-48f5-81b2-1776371b7832";
    const newManifest = {
      ...manifest,
      project_id: newProjectId,
      project_name: "卸载中的项目",
      source_sha256: "b".repeat(64),
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/projects") {
        return Promise.resolve(jsonResponse([]));
      }
      if (url === "/api/system/recovery") {
        return Promise.resolve(jsonResponse(recoveryReport));
      }
      if (url === "/api/projects/import") {
        return imported.promise;
      }
      if (url === `/api/projects/${newProjectId}/coverage`) {
        return Promise.resolve(jsonResponse(coverage));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<App />);
    const user = await completeForm("卸载中的项目", "new.zip");
    await user.click(screen.getByRole("button", { name: "导入并分析" }));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/projects/import",
      expect.objectContaining({ method: "POST" }),
    );

    view.unmount();
    await act(async () => {
      imported.resolve(jsonResponse(newManifest, 201));
      await imported.promise;
      await Promise.resolve();
    });

    expect(fetchMock).not.toHaveBeenCalledWith(
      `/api/projects/${newProjectId}/coverage`,
      undefined,
    );
  });

  it("组件卸载后面板响应不得继续请求任务详情", async () => {
    const loadedManifest = deferred<Response>();
    const loadedCoverage = deferred<Response>();
    const loadedJobs = deferred<Response>();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/projects") {
        return Promise.resolve(jsonResponse([projectSummary]));
      }
      if (url === "/api/system/recovery") {
        return Promise.resolve(jsonResponse(recoveryReport));
      }
      if (url === `/api/projects/${projectId}`) {
        return loadedManifest.promise;
      }
      if (url === `/api/projects/${projectId}/coverage`) {
        return loadedCoverage.promise;
      }
      if (url === `/api/projects/${projectId}/jobs`) {
        return loadedJobs.promise;
      }
      if (url === `/api/projects/${projectId}/jobs/${jobId}`) {
        return Promise.resolve(jsonResponse(jobDetail));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<App />);
    await userEvent.setup().click(
      await screen.findByRole("button", { name: /恢复项目/ }),
    );

    view.unmount();
    await act(async () => {
      loadedManifest.resolve(jsonResponse(manifest));
      loadedCoverage.resolve(jsonResponse(coverage));
      loadedJobs.resolve(jsonResponse([jobSummary]));
      await Promise.all([
        loadedManifest.promise,
        loadedCoverage.promise,
        loadedJobs.promise,
      ]);
      await Promise.resolve();
    });

    expect(fetchMock).not.toHaveBeenCalledWith(
      `/api/projects/${projectId}/jobs/${jobId}`,
      undefined,
    );
  });

  it("旧导入响应与 finally 不能覆盖选择或清除较新导入的 busy 状态", async () => {
    const importB = deferred<Response>();
    const importC = deferred<Response>();
    let projectBCoverageRequests = 0;
    const projectBId = "dce1d8fa-3e28-48f5-81b2-1776371b7832";
    const projectCId = "ee54efc6-25e7-43ab-8833-54d6c21988ba";
    const manifestB = {
      ...manifest,
      project_id: projectBId,
      project_name: "重叠导入 B",
      source_sha256: "b".repeat(64),
    };
    const manifestC = {
      ...manifest,
      project_id: projectCId,
      project_name: "较新导入 C",
      source_sha256: "c".repeat(64),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/projects") {
          return Promise.resolve(jsonResponse([projectSummary]));
        }
        if (url === "/api/system/recovery") {
          return Promise.resolve(jsonResponse(recoveryReport));
        }
        if (url === "/api/projects/import") {
          const name = (init?.body as FormData).get("project_name");
          return name === "重叠导入 B" ? importB.promise : importC.promise;
        }
        if (
          url === `/api/projects/${projectBId}/coverage`
        ) {
          projectBCoverageRequests += 1;
          return Promise.resolve(jsonResponse(coverage));
        }
        if (url === `/api/projects/${projectCId}/coverage`) {
          return Promise.resolve(jsonResponse(coverage));
        }
        if (url === `/api/projects/${projectId}`) {
          return Promise.resolve(jsonResponse(manifest));
        }
        if (url === `/api/projects/${projectId}/coverage`) {
          return Promise.resolve(jsonResponse(coverage));
        }
        if (url === `/api/projects/${projectId}/jobs`) {
          return Promise.resolve(jsonResponse([]));
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    render(<App />);
    const user = await completeForm("重叠导入 B", "b.zip");
    await user.click(screen.getByRole("button", { name: "导入并分析" }));
    await user.click(await screen.findByRole("button", { name: /恢复项目/ }));
    await user.clear(screen.getByLabelText("项目名称"));
    await user.type(screen.getByLabelText("项目名称"), "较新导入 C");
    await user.upload(
      screen.getByLabelText("ZIP 资源包"),
      new File(["c"], "c.zip", { type: "application/zip" }),
    );
    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    await act(async () => {
      importB.resolve(jsonResponse(manifestB, 201));
      await importB.promise;
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      screen.getByRole("button", { name: "正在导入并分析…" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: /恢复项目/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /重叠导入 B/ }),
    ).not.toHaveAttribute("aria-current", "true");
    expect(projectBCoverageRequests).toBe(0);

    await act(async () => {
      importC.resolve(jsonResponse(manifestC, 201));
      await importC.promise;
    });

    expect(
      await screen.findByRole("button", { name: /较新导入 C/ }),
    ).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: /重叠导入 B/ })).not.toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("旧导入响应与 finally 不能打断较新项目的覆盖重试", async () => {
    const staleImport = deferred<Response>();
    const retriedCoverage = deferred<Response>();
    const staleProjectId = "dce1d8fa-3e28-48f5-81b2-1776371b7832";
    const retryProjectId = "ee54efc6-25e7-43ab-8833-54d6c21988ba";
    const staleManifest = {
      ...manifest,
      project_id: staleProjectId,
      project_name: "迟到导入",
      source_sha256: "b".repeat(64),
    };
    const retryManifest = {
      ...manifest,
      project_id: retryProjectId,
      project_name: "重试项目",
      source_sha256: "c".repeat(64),
    };
    let retryCoverageAttempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/projects") {
          return Promise.resolve(jsonResponse([projectSummary]));
        }
        if (url === "/api/system/recovery") {
          return Promise.resolve(jsonResponse(recoveryReport));
        }
        if (url === "/api/projects/import") {
          const name = (init?.body as FormData).get("project_name");
          return name === "迟到导入"
            ? staleImport.promise
            : Promise.resolve(jsonResponse(retryManifest, 201));
        }
        if (url === `/api/projects/${retryProjectId}/coverage`) {
          retryCoverageAttempts += 1;
          return retryCoverageAttempts === 1
            ? Promise.reject(new TypeError("initial coverage failure"))
            : retriedCoverage.promise;
        }
        if (url === `/api/projects/${staleProjectId}/coverage`) {
          return Promise.resolve(jsonResponse(coverage));
        }
        if (url === `/api/projects/${projectId}`) {
          return Promise.resolve(jsonResponse(manifest));
        }
        if (url === `/api/projects/${projectId}/coverage`) {
          return Promise.resolve(jsonResponse(coverage));
        }
        if (url === `/api/projects/${projectId}/jobs`) {
          return Promise.resolve(jsonResponse([]));
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    render(<App />);
    const user = await completeForm("迟到导入", "stale.zip");
    await user.click(screen.getByRole("button", { name: "导入并分析" }));
    await user.click(await screen.findByRole("button", { name: /恢复项目/ }));
    await user.clear(screen.getByLabelText("项目名称"));
    await user.type(screen.getByLabelText("项目名称"), "重试项目");
    await user.upload(
      screen.getByLabelText("ZIP 资源包"),
      new File(["retry"], "retry.zip", { type: "application/zip" }),
    );
    await user.click(screen.getByRole("button", { name: "导入并分析" }));
    await user.click(
      await screen.findByRole("button", { name: "重试覆盖分析" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("无法连接到本地服务");

    await act(async () => {
      staleImport.resolve(jsonResponse(staleManifest, 201));
      await staleImport.promise;
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      screen.getByRole("button", { name: "正在重试覆盖分析…" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: /重试项目/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("无法连接到本地服务");
    expect(
      screen.getByRole("button", { name: /迟到导入/ }),
    ).not.toHaveAttribute("aria-current", "true");

    await act(async () => {
      retriedCoverage.resolve(jsonResponse(coverage));
      await retriedCoverage.promise;
    });

    expect(await screen.findByLabelText("覆盖统计")).toBeVisible();
    expect(screen.getByRole("button", { name: /迟到导入/ })).not.toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("使用产品中立的项目面板标题", () => {
    installRestorationFetch();
    render(<App />);

    expect(screen.getByText("AIMCTextureGen / 项目面板")).toBeInTheDocument();
    expect(screen.queryByText(/Phase 1/)).not.toBeInTheDocument();
  });
});
