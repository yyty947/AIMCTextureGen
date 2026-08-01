// @vitest-environment node

import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, expect, it } from "vitest";

import { cleanupFaviconArtifact } from "./favicon-artifact-support.mjs";

const temporaryDirectories = [];

function buildDirectory() {
  const directory = mkdtempSync(join(tmpdir(), "aimctexturegen-favicon-"));
  temporaryDirectories.push(directory);
  return join(directory, "artifact");
}

function populatedBuildDirectory() {
  const directory = buildDirectory();
  mkdirSync(directory);
  writeFileSync(join(directory, "index.html"), "artifact", "utf8");
  return directory;
}

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { force: true, recursive: true });
  }
});

it("removes the artifact directory when setup fails before a server starts", async () => {
  const artifactDirectory = populatedBuildDirectory();

  await cleanupFaviconArtifact({ buildDirectory: artifactDirectory });

  expect(existsSync(artifactDirectory)).toBe(false);
});

it("removes the artifact directory even when server shutdown reports an error", async () => {
  const artifactDirectory = populatedBuildDirectory();
  const shutdownError = new Error("server shutdown failed");
  const server = {
    close(callback) {
      callback(shutdownError);
    },
  };

  await expect(cleanupFaviconArtifact({ buildDirectory: artifactDirectory, server })).rejects.toBe(shutdownError);

  expect(existsSync(artifactDirectory)).toBe(false);
});
