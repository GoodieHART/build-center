"""
Volume path utilities and management helpers for the Build Center.

Path conventions:
  VOLUME_ROOT  /builds                          # Single source of truth
  Run dirs     /builds/runs/{build_id}/         # Per-build isolation
  SDK cache    /builds/.cache/sdk/android/      # Shared immutable SDKs
  Artifacts    /builds/runs/{build_id}/artifacts/{filename}  # Output APKs
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

VOLUME_ROOT = "/builds"


def generate_build_id() -> str:
    """Return a unique build identifier like ``build-20260615T120000-a1b2c3d4``."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    short_uid = uuid.uuid4().hex[:8]
    return f"build-{timestamp}-{short_uid}"


def build_run_path(volume_root: str = VOLUME_ROOT, build_id: str = "") -> str:
    """Return the run directory path for *build_id* under *volume_root*."""
    return os.path.join(volume_root, "runs", build_id, "")


def sdk_cache_path(volume_root: str = VOLUME_ROOT) -> str:
    """Return the shared SDK cache directory path under *volume_root*."""
    return os.path.join(volume_root, ".cache", "sdk", "android", "")


def artifact_path(
    volume_root: str = VOLUME_ROOT,
    build_id: str = "",
    filename: str = "",
) -> str:
    """Return the path for *filename* in the artifacts subdirectory of *build_id*."""
    return os.path.join(volume_root, "runs", build_id, "artifacts", filename)


def ensure_dirs(path: str) -> None:
    """Create *path* and all missing parent directories (no-op if they exist)."""
    os.makedirs(path, exist_ok=True)


def commit_volume(volume: object) -> None:
    """Commit a Modal Volume and log the operation.

    Args:
        volume: A ``modal.Volume`` instance.
    """
    logger.info("Committing volume…")
    volume.commit()
