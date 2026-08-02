import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import InferenceSetup from "./InferenceSetup";

const operationId = "6fda5078-1246-4cac-91e8-541808da14f4";
const digest = "a".repeat(64);
const sha = "b".repeat(64);
const timestamp = "2026-08-02T00:00:00Z";

const statusBody = {
  environment: {
    supported: true,
    platform: "windows",
    architecture: "x86_64",
    gpu_vendor: "nvidia",
    gpu_name: "RTX 4060",
    driver_version: "552.44",
    vram_bytes: 8589934592,
    disk_free_bytes: 1000000000000,
    blocking_issues: [],
  },
  runtime: { state: "missing", selected_version: null, error: null },
  profile: {
    profile_id: "sdxl-mapchip-ipadapter",
    profile_version: "1",
    support_state: "candidate_unverified",
    components: [
      { artifact_id: "checkpoint", state: "missing", installed_bytes: null },
    ],
    ready: false,
  },
  process: { state: "stopped", pid: null, version: null, errors: [] },
};

const planBody = {
  runtime_id: "comfyui-windows-nvidia",
  runtime_version: "0.29.2",
  profile_id: "sdxl-mapchip-ipadapter",
  profile_version: "1",
  plan_digest: digest,
  components: [
    {
      artifact_id: "checkpoint",
      source_url: "https://example.com/source",
      revision: "r1",
      byte_size: 1500000000,
      sha256: sha,
      destination: "models/checkpoints/x.safetensors",
      license_name: "Apache-2.0",
      license_source_url: "https://example.com/license",
      state: "missing",
    },
  ],
  total_download_bytes: 1500000000,
  temporary_headroom_bytes: 8000000000,
  required_free_bytes: 9500000000,
  disk_free_bytes: 1000000000000,
  can_install: true,
  blockers: [],
};

function operationBody(state: string, revision = 1) {
  return {
    operation_id: operationId,
    runtime_id: "comfyui-windows-nvidia",
    profile_id: "sdxl-mapchip-ipadapter",
    plan_digest: digest,
    accepted_component_ids: ["checkpoint"],
    state,
    revision,
    created_at: timestamp,
    updated_at: timestamp,
    error: null,
  };
}

function stubFetch(routes: Record<string, unknown>) {
  const mock = vi.fn().mockImplementation(
    async (input: RequestInfo | URL) => {
      const url = String(input);
      if (!(url in routes)) {
        throw new TypeError(`Unmocked URL ${url}`);
      }
      return new Response(JSON.stringify(routes[url]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  );
  vi.stubGlobal("fetch", mock);
  return mock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("InferenceSetup", () => {
  it("does not fetch until expanded and then shows host and totals", async () => {
    const fetchMock = stubFetch({
      "/api/system/inference": statusBody,
      "/api/system/inference/install-plan": planBody,
    });
    render(<InferenceSetup />);
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "展开推理环境" }));

    await waitFor(() => {
      expect(screen.getByText(/RTX 4060/)).toBeTruthy();
      expect(screen.getAllByText(/1\.40 GiB/).length).toBeGreaterThan(0);
      expect(screen.getByText(/下载总量 1\.50 GB/)).toBeTruthy();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/system/inference",
      undefined,
    );
  });

  it("requires every license acceptance before install", async () => {
    stubFetch({
      "/api/system/inference": statusBody,
      "/api/system/inference/install-plan": planBody,
    });
    render(<InferenceSetup />);
    fireEvent.click(screen.getByRole("button", { name: "展开推理环境" }));
    const install = await screen.findByRole("button", {
      name: "确认并开始安装",
    });
    expect(install.hasAttribute("disabled")).toBe(true);

    fireEvent.click(screen.getByRole("checkbox"));
    await waitFor(() => {
      expect(
        screen
          .getByRole("button", { name: "确认并开始安装" })
          .hasAttribute("disabled"),
      ).toBe(false);
    });
  });

  it("starts an installation and shows the operation state", async () => {
    const fetchMock = stubFetch({
      "/api/system/inference": statusBody,
      "/api/system/inference/install-plan": planBody,
      "/api/system/inference/installations": operationBody("planned"),
    });
    render(<InferenceSetup />);
    fireEvent.click(screen.getByRole("button", { name: "展开推理环境" }));
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.click(
      screen.getByRole("button", { name: "确认并开始安装" }),
    );

    await waitFor(() => {
      expect(screen.getByText(/安装操作 planned/)).toBeTruthy();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/system/inference/installations",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows unsupported host recommendations", async () => {
    stubFetch({
      "/api/system/inference": {
        ...statusBody,
        environment: {
          ...statusBody.environment,
          supported: false,
          blocking_issues: ["unsupported_os"],
        },
      },
      "/api/system/inference/install-plan": planBody,
    });
    render(<InferenceSetup />);
    fireEvent.click(screen.getByRole("button", { name: "展开推理环境" }));

    await waitFor(() => {
      expect(screen.getByText(/主机支持：否/)).toBeTruthy();
      expect(screen.getByText(/unsupported_os/)).toBeTruthy();
    });
  });

  it("starts and stops the owned runtime through dedicated buttons", async () => {
    const fetchMock = stubFetch({
      "/api/system/inference": statusBody,
      "/api/system/inference/install-plan": planBody,
      "/api/system/inference/comfyui/start": {
        state: "ready",
        pid: 1,
        version: "0.29.2",
        errors: [],
      },
      "/api/system/inference/comfyui/stop": {
        state: "stopped",
        pid: null,
        version: null,
        errors: [],
      },
    });
    render(<InferenceSetup />);
    fireEvent.click(screen.getByRole("button", { name: "展开推理环境" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "启动受管 ComfyUI" }),
    );
    await waitFor(() => {
      expect(screen.getByText(/受管进程：ready/)).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "停止受管 ComfyUI" }));
    await waitFor(() => {
      expect(screen.getByText(/受管进程：stopped/)).toBeTruthy();
    });
  });

  it("offers no generation button", async () => {
    stubFetch({
      "/api/system/inference": statusBody,
      "/api/system/inference/install-plan": planBody,
    });
    render(<InferenceSetup />);
    fireEvent.click(screen.getByRole("button", { name: "展开推理环境" }));
    await screen.findByText(/下载总量/);
    expect(screen.queryByRole("button", { name: /生成候选/ })).toBeNull();
  });

  it("stops polling after unmount", async () => {
    vi.useFakeTimers();
    const fetchMock = stubFetch({
      "/api/system/inference": statusBody,
      "/api/system/inference/install-plan": planBody,
    });
    const view = render(<InferenceSetup />);
    fireEvent.click(screen.getByRole("button", { name: "展开推理环境" }));
    await act(async () => {
      await Promise.resolve();
    });
    const callsBefore = fetchMock.mock.calls.length;
    expect(callsBefore).toBeGreaterThan(0);

    view.unmount();
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(fetchMock.mock.calls.length).toBe(callsBefore);
  });
});
