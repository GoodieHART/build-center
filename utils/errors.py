"""
Structured result types and error handling helpers for the Build Center.

This module defines the **BuildResult** contract — a plain-dict protocol that
every Modal ``@app.function()`` returns so that callers (the wizard,
orchestrators, etc.) can inspect outcomes uniformly without catching raw
exceptions across remote boundaries.

Status ladder
-------------
- ``"success"`` — Build completed successfully (exit_code is 0 or absent).
- ``"failed"`` — Build ran but failed (non-zero exit_code, partial artifacts).
- ``"error"`` — System or infrastructure error (exception, timeout, network).

Usage
-----
>>> from utils.errors import success, failed, error, is_success
>>> result = success("APK ready", artifacts=["app.apk"], download_cmd="modal volume get ...")
>>> is_success(result)
True
"""

from typing import List, Optional, TypedDict, Union


# ---------------------------------------------------------------------------
# BuildResult — TypedDict for static type checking
# ---------------------------------------------------------------------------

class BuildResult(TypedDict):
    """Shape of every result dict returned by Modal builder/provisioner functions.

    Attributes:
        status: One of ``"success"``, ``"failed"``, ``"error"``.
        message: Human-readable summary.
        stage: The pipeline stage that produced this result (e.g. ``"gradle"``).
        exit_code: Process exit code (``None`` for system errors).
        artifacts: List of artifact paths or identifiers.
        download_cmd: Optional shell command to retrieve artifacts.
    """

    status: str
    message: str
    stage: str
    exit_code: Optional[int]
    artifacts: List[str]
    download_cmd: Optional[str]


# ---------------------------------------------------------------------------
# Factory functions — return plain dicts (JSON-serialisable for Modal)
# ---------------------------------------------------------------------------


def success(
    message: str,
    artifacts: Optional[List[str]] = None,
    download_cmd: Optional[str] = None,
) -> BuildResult:
    """Return a ``"success"`` result dict.

    Args:
        message: Human-readable success description.
        artifacts: List of produced artifact paths (default: ``[]``).
        download_cmd: Optional shell command to download artifacts.

    Returns:
        A plain dict conforming to the ``BuildResult`` shape.
    """
    return {
        "status": "success",
        "message": message,
        "stage": "",
        "exit_code": None,
        "artifacts": artifacts if artifacts is not None else [],
        "download_cmd": download_cmd,
    }


def failed(
    stage: str,
    message: str,
    exit_code: int,
) -> BuildResult:
    """Return a ``"failed"`` result dict (build ran but exited non-zero).

    Args:
        stage: The pipeline stage that failed (e.g. ``"gradle"``).
        message: Human-readable failure description.
        exit_code: Non-zero process exit code.

    Returns:
        A plain dict conforming to the ``BuildResult`` shape.
    """
    return {
        "status": "failed",
        "message": message,
        "stage": stage,
        "exit_code": exit_code,
        "artifacts": [],
        "download_cmd": None,
    }


def error(
    stage: str,
    message: str,
) -> BuildResult:
    """Return an ``"error"`` result dict (system / infrastructure error).

    Args:
        stage: The pipeline stage that encountered the error.
        message: Human-readable error description.

    Returns:
        A plain dict conforming to the ``BuildResult`` shape.
    """
    return {
        "status": "error",
        "message": message,
        "stage": stage,
        "exit_code": None,
        "artifacts": [],
        "download_cmd": None,
    }


# ---------------------------------------------------------------------------
# BuildError — exception that wraps a BuildResult for exception-to-result
#              conversion in try/except blocks.
# ---------------------------------------------------------------------------


class BuildError(Exception):
    """Exception that carries a ``BuildResult`` dict.

    Use in catch blocks where a ``BuildResult``-shaped error dict is needed::

        try:
            ...
        except BuildError as exc:
            return exc.result
    """

    def __init__(self, result: BuildResult) -> None:
        self.result = result
        super().__init__(result["message"])


# ---------------------------------------------------------------------------
# Subprocess error conversion
# ---------------------------------------------------------------------------


def handle_subprocess_error(
    e: "Exception",
    stage: str,
) -> BuildResult:
    """Convert a ``subprocess.CalledProcessError`` to an error result dict.

    Handles both ``CalledProcessError`` (has ``.returncode``, ``.output``,
    ``.stderr``) and generic exceptions.

    Args:
        e: The caught exception.
        stage: The pipeline stage where the error occurred.

    Returns:
        An ``"error"`` result dict with extracted details.
    """
    # Prefer subprocess.CalledProcessError details when available.
    import subprocess

    if isinstance(e, subprocess.CalledProcessError):
        # Build a message from available error fields.
        parts: List[str] = [str(e)]
        if e.stderr:
            decoded = e.stderr.decode("utf-8", errors="replace").strip()
            if decoded:
                parts.append(decoded)
        if e.output:
            decoded = e.output.decode("utf-8", errors="replace").strip()
            if decoded:
                parts.append(decoded)
        msg = " | ".join(parts)
        return {
            "status": "error",
            "message": msg,
            "stage": stage,
            "exit_code": e.returncode,
            "artifacts": [],
            "download_cmd": None,
        }

    # Fallback for any other exception type.
    return {
        "status": "error",
        "message": f"{type(e).__name__}: {e}",
        "stage": stage,
        "exit_code": None,
        "artifacts": [],
        "download_cmd": None,
    }


# ---------------------------------------------------------------------------
# Type-narrowing helpers
# ---------------------------------------------------------------------------


def is_success(result: BuildResult) -> bool:
    """Check whether *result* has ``status == \"success\"``.

    Args:
        result: A ``BuildResult`` dict.

    Returns:
        ``True`` if status is ``"success"``.
    """
    return result.get("status") == "success"


def is_failure(result: BuildResult) -> bool:
    """Check whether *result* has a non-success status (``\"failed\"`` or ``\"error\"``).

    Args:
        result: A ``BuildResult`` dict.

    Returns:
        ``True`` if status is not ``"success"``.
    """
    return result.get("status") != "success"
