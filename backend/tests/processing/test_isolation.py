"""Roadmap exit gate: processing accepts no service or ComfyUI dependency.

Runs in a subprocess so sys.modules reflects ONLY what importing the
pipeline pulls in. Do not weaken the forbidden list to make new code pass;
keep inference and web frameworks out of the processing package instead.
"""

import subprocess
import sys

FORBIDDEN_PREFIXES = (
    "fastapi",
    "starlette",
    "uvicorn",
    "httpx",
    "aimctexturegen.api",
    "aimctexturegen.catalog",
    "aimctexturegen.core",
    "aimctexturegen.main",
    "aimctexturegen.packs",
    "aimctexturegen.projects",
    "torch",
    "comfy",
    "diffusers",
    "transformers",
    "numpy",
)

_PROBE = (
    "import sys\n"
    "import aimctexturegen.processing.pipeline\n"
    "prefixes = " + repr(FORBIDDEN_PREFIXES) + "\n"
    "loaded = sorted(name for name in sys.modules if name.startswith(prefixes))\n"
    "print('FORBIDDEN:' + ','.join(loaded) if loaded else 'CLEAN')\n"
    "raise SystemExit(1 if loaded else 0)\n"
)


def test_processing_pipeline_imports_no_service_or_inference_modules():
    result = subprocess.run(
        [sys.executable, "-B", "-c", _PROBE],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "CLEAN"
