import { rmSync } from "node:fs";

export async function cleanupFaviconArtifact({ buildDirectory, server }) {
  try {
    if (server) {
      await new Promise((resolveClosed, rejectClosed) => {
        server.close((error) => {
          if (error) {
            rejectClosed(error);
            return;
          }
          resolveClosed();
        });
      });
    }
  } finally {
    rmSync(buildDirectory, { force: true, recursive: true });
  }
}
