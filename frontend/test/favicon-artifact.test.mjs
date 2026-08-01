// @vitest-environment node

import { createServer } from "node:http";
import { readFileSync, rmSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, extname, join, normalize, resolve, sep } from "node:path";
import { spawnSync } from "node:child_process";

import { afterAll, beforeAll, expect, it } from "vitest";

import { cleanupFaviconArtifact } from "./favicon-artifact-support.mjs";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const buildDirectory = join(frontendRoot, "dist-favicon-artifact-test");
const viteCli = join(frontendRoot, "node_modules", "vite", "bin", "vite.js");
let server;
let baseUrl;

beforeAll(async () => {
  rmSync(buildDirectory, { force: true, recursive: true });
  const build = spawnSync(process.execPath, [viteCli, "build", "--outDir", buildDirectory], {
    cwd: frontendRoot,
    encoding: "utf8",
  });
  expect(build.status, build.stderr || build.stdout).toBe(0);

  server = createServer((request, response) => {
    const requestPath = new URL(request.url ?? "/", "http://localhost").pathname;
    const relativePath = requestPath === "/" ? "index.html" : requestPath.slice(1);
    const artifactPath = normalize(join(buildDirectory, relativePath));
    if (!artifactPath.startsWith(`${buildDirectory}${sep}`) || !statSync(artifactPath, { throwIfNoEntry: false })?.isFile()) {
      response.writeHead(404).end();
      return;
    }
    const contentType = extname(artifactPath) === ".svg" ? "image/svg+xml" : "text/html";
    response.writeHead(200, { "content-type": contentType }).end(readFileSync(artifactPath));
  });
  await new Promise((resolveListening) => server.listen(0, "127.0.0.1", resolveListening));
  const address = server.address();
  baseUrl = `http://127.0.0.1:${address.port}`;
});

afterAll(async () => {
  await cleanupFaviconArtifact({ buildDirectory, server });
});

it("serves the favicon declared by the built application", async () => {
  const page = await fetch(`${baseUrl}/`);
  const html = await page.text();
  const iconTag = html.match(/<link\b[^>]*\brel=["']icon["'][^>]*>/i)?.[0];
  const href = iconTag?.match(/\bhref=["']([^"']+)["']/i)?.[1];

  expect(page.status).toBe(200);
  expect(href).toBeTruthy();

  const icon = await fetch(new URL(href, baseUrl));
  expect(icon.status).toBe(200);
  expect(icon.headers.get("content-type")).toContain("image/svg+xml");
  expect((await icon.text()).length).toBeGreaterThan(0);
});
