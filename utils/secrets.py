"""Secret management utilities for Modal-based build operations.

Provides helpers to look up and validate Modal Secrets before dispatching
builds, so the user gets a clear error message (plus creation command)
instead of a cryptic cloud-side failure.
"""

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUILD_CENTER_SECRET_NAME = "build-center-android"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_secret(secret_name: str) -> object:
    """Return ``modal.Secret.from_name(secret_name)``.

    Catches ``modal.exception.NotFoundError`` (and general exceptions) and
    prints a user-friendly message including the ``modal secret create``
    command needed to create the missing secret.

    Args:
        secret_name: The Modal Secret name to look up (kebab-case convention).

    Returns:
        A ``modal.Secret`` instance on success, or ``None`` if the secret
        could not be found / an error occurred.
    """
    try:
        import modal

        return modal.Secret.from_name(secret_name)
    except Exception:
        logger.exception(
            "Secret '%s' could not be resolved. "
            "Create it with:\n  modal secret create %s access_token=YOUR_GITHUB_TOKEN",
            secret_name,
            secret_name,
        )
        return None


def validate_secret_exists(secret_name: str) -> bool:
    """Check whether *secret_name* exists in the Modal workspace.

    Returns ``True`` if the secret resolves successfully, ``False``
    otherwise.  Never raises — all exceptions are caught internally.

    Args:
        secret_name: The Modal Secret name to validate.

    Returns:
        ``True`` if the secret exists, ``False`` otherwise.
    """
    result = get_secret(secret_name)
    return result is not None
