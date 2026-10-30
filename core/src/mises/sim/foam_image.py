"""The pinned OpenFOAM container image and its detection (numpy-free: the system check imports
this without loading the solver stack).

Image choice (researched 2026-10-30, S2 report):
- OpenCFD's official runtime image `opencfd/openfoam-run` (openfoam.com, ESI). Tag 2512 is a
  multi-arch manifest with a native linux/arm64 image, so it runs without emulation on an
  Apple-silicon Mac (Docker Hub API,
  https://hub.docker.com/v2/repositories/opencfd/openfoam-run/tags/2512: images amd64
  sha256:c00f5e69..., arm64 sha256:bb2add96... (176 MB), manifest list digest below).
- Its entrypoint is /openfoam/run, which sources the OpenFOAM environment and runs the
  command it is given.
- Pinned by digest so a re-pushed tag can never change what runs; `docker pull` of this exact
  reference is the one install command on the allowlist (system_check.INSTALLERS).

Detection is read-only: `docker image inspect` of the pinned reference. Nothing here pulls or
starts a container.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

OPENFOAM_REPO = "opencfd/openfoam-run"
OPENFOAM_TAG = "2512"
OPENFOAM_DIGEST = "sha256:707421ab7d1e5201d86723945a6649490e292a0c46ed03d36982449e6b3b7243"
OPENFOAM_IMAGE = f"{OPENFOAM_REPO}@{OPENFOAM_DIGEST}"
OPENFOAM_VERSION = f"v{OPENFOAM_TAG}"
OPENFOAM_ARCHES = ("amd64", "arm64")      # native arm64: no emulation on Apple silicon
ORBSTACK_URL = "https://orbstack.dev"

DOCKER_CANDIDATES = ["/usr/local/bin/docker", "/opt/homebrew/bin/docker",
                     str(Path("~/.orbstack/bin/docker").expanduser())]


def _exe(p: str | None) -> str | None:
    return p if p and os.path.isfile(p) and os.access(p, os.X_OK) else None


def find_container_runtime() -> str | None:
    """The docker CLI (Docker Desktop or OrbStack's), or None."""
    return _exe(shutil.which("docker")) or next((c for c in DOCKER_CANDIDATES if _exe(c)), None)


def docker_env(docker: str) -> dict[str, str]:
    """A minimal environment for the docker CLI: its own dir on PATH (credential helpers sit
    next to it) and the real HOME (the CLI reads its context, e.g. OrbStack's, from
    ~/.docker)."""
    return {"PATH": f"{os.path.dirname(docker)}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
            "HOME": os.path.expanduser("~")}


def image_status(docker: str) -> str:
    """"present" | "absent" | "daemon_down" for the pinned image (read-only inspect)."""
    try:
        r = subprocess.run([docker, "image", "inspect", "--format", "{{.Id}}", OPENFOAM_IMAGE],
                           capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL, env=docker_env(docker))
    except (OSError, subprocess.SubprocessError):
        return "daemon_down"
    if r.returncode == 0:
        return "present"
    err = (r.stderr or "").lower()
    if "no such image" in err or "no such object" in err:
        return "absent"
    return "daemon_down"
