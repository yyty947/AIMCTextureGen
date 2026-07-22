import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const projectId = "6fda5078-1246-4cac-91e8-541808da14f4";

const manifest = {
  schema_version: 1,
  project_id: projectId,
  project_name: "测试项目",
  edition: "java",
  java_pack_format: 34,
  supported_formats: [34, 35],
  catalog_id: "java-dev-format-34",
  source_sha256: "a".repeat(64),
  created_at: "2026-07-21T10:00:00+08:00",
  updated_at: "2026-07-21T10:00:00+08:00",
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
  it("导入 ZIP 后显示资源格式和覆盖统计", async () => {
    mockSuccessfulImport();
    render(<App />);
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    expect(await screen.findByText("资源格式 34")).toBeInTheDocument();
    expect(screen.getByText("已覆盖 1")).toBeInTheDocument();
    expect(screen.getByText("未覆盖 1")).toBeInTheDocument();
    expect(screen.getByText("Deepslate")).toBeInTheDocument();
  });

  it("显示开发目录警告、缺失路径和未知文件数量", async () => {
    mockSuccessfulImport();
    render(<App />);
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
    render(<App />);
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
    render(<App />);
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(expectedMessage);
  });

  it("有效 JSON 的项目清单形状错误时显示无法识别的响应", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ ...manifest, project_id: 42 }, 201),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
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
    render(<App />);
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("本地服务返回了无法识别的响应");
    expect(alert).toHaveTextContent("项目已创建");
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "重试覆盖分析" }));

    expect(await screen.findByText("资源格式 34")).toBeInTheDocument();
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
    render(<App />);
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("项目已创建");
    expect(alert).toHaveTextContent("无需重新导入 ZIP");
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "重试覆盖分析" }));

    expect(await screen.findByText("资源格式 34")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      `/api/projects/${projectId}/coverage`,
    );
  });

  it("拒绝非 ZIP 文件名并将可读错误关联到文件输入", async () => {
    render(<App />);
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
    render(<App />);
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
    expect(await screen.findByText("资源格式 34")).toBeInTheDocument();
  });

  it("项目名称按 Unicode code point 与后端一致计数", async () => {
    mockSuccessfulImport();
    render(<App />);
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
    render(<App />);
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
    render(<App />);
    const user = await completeForm();

    await user.click(screen.getByRole("button", { name: "导入并分析" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "本地服务返回了无法识别的响应",
    );
  });

  it("新的导入失败时不保留上一次的覆盖摘要", async () => {
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
    render(<App />);
    const user = await completeForm();
    const button = screen.getByRole("button", { name: "导入并分析" });
    await user.click(button);
    expect(await screen.findByText("资源格式 34")).toBeInTheDocument();

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
    expect(screen.queryByText("资源格式 34")).not.toBeInTheDocument();
    expect(screen.queryByText("Deepslate")).not.toBeInTheDocument();
  });
});
