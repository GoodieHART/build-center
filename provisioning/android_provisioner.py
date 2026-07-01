"""Android provisioner module — SDK installation and license acceptance.

Wraps Google's ``sdkmanager`` CLI for provisioning Android SDK components
inside Modal containers.  Provides idempotent, caching-aware installs
and a registry-compatible ``provision(config)`` interface.
"""

import logging
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

from utils.volume import VOLUME_ROOT, ensure_dirs, sdk_cache_path
from . import register

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CMDLINE_TOOLS_URL = (
    "https://dl.google.com/android/repository/"
    "commandlinetools-linux-13114758_latest.zip"
)

_CACHE_TTL = 300  # seconds (5 minutes)


# ---------------------------------------------------------------------------
# Provisioner
# ---------------------------------------------------------------------------


@register("android")
class AndroidProvisioner:
    """Android SDK provisioner — manages SDK components via ``sdkmanager``.

    Can be used as a standalone utility::

        ap = AndroidProvisioner()
        ap.ensure_sdkmanager()
        platforms = ap.fetch_available_platforms()
        ap.provision_sdk("platforms;android-35")

    Or through the provisioner registry::

        from provisioning import get_provisioner
        Cls = get_provisioner("android")
        ap = Cls()
        ap.provision({"sdk_platform": "35", "build_tools": "35.0.0"})
    """

    name = "android"

    def __init__(self) -> None:
        # Per-method result caches (monotonic clock)
        self._platforms_cache: Optional[List[str]] = None
        self._platforms_cache_time: float = 0.0
        self._build_tools_cache: Optional[List[str]] = None
        self._build_tools_cache_time: float = 0.0
        self._ndk_cache: Optional[List[str]] = None
        self._ndk_cache_time: float = 0.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cmdline_tools_dir() -> str:
        """Return the path to the cmdline-tools/latest directory."""
        return os.path.join(sdk_cache_path(VOLUME_ROOT), "cmdline-tools", "latest")

    @staticmethod
    def _sdkmanager_path() -> str:
        """Return the path to the ``sdkmanager`` binary."""
        return os.path.join(
            sdk_cache_path(VOLUME_ROOT), "cmdline-tools", "latest", "bin", "sdkmanager"
        )

    def _accept_licenses(self) -> None:
        """Accept all pending SDK licenses using ``yes | sdkmanager --licenses``."""
        cache_path = sdk_cache_path(VOLUME_ROOT)
        sdkmanager = self._sdkmanager_path()

        yes_proc = subprocess.Popen(["yes"], stdout=subprocess.PIPE)
        try:
            subprocess.run(
                [sdkmanager, "--licenses", f"--sdk_root={cache_path}"],
                stdin=yes_proc.stdout,
                capture_output=True,
                text=True,
                timeout=120,
            )
        finally:
            yes_proc.terminate()
            yes_proc.wait()

    def _run_sdkmanager(self, *args: str) -> subprocess.CompletedProcess:
        """Invoke ``sdkmanager`` with *args* and ``--sdk_root={cache_path}``.

        Automatically ensures ``sdkmanager`` is downloaded first.
        """
        sdkmanager = self._sdkmanager_path()
        cache_path = sdk_cache_path(VOLUME_ROOT)
        cmd = [sdkmanager, *args, f"--sdk_root={cache_path}"]
        return subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )

    @staticmethod
    def _parse_sdkmanager_output(output: str, prefix: str) -> List[str]:
        """Parse ``sdkmanager --list`` text for entries starting with *prefix*.

        Handles both the compact format (``prefix... | version | ...``)
        and the detailed format (``prefix...`` on its own line).
        """
        entries: set = set()
        for line in output.splitlines():
            part = line.split("|")[0].strip()
            if part.startswith(prefix):
                entries.add(part)
        return sorted(entries)

    @staticmethod
    def _cache_fresh(cache_time: float) -> bool:
        """Return ``True`` when the cached value is still within TTL."""
        return (time.monotonic() - cache_time) < _CACHE_TTL

    # ------------------------------------------------------------------
    # Public API — infrastructure
    # ------------------------------------------------------------------

    def ensure_sdkmanager(self) -> str:
        """Download cmdline-tools to the SDK cache if not already present.

        Returns the absolute path to the ``sdkmanager`` binary.
        Idempotent — subsequent calls are no-ops once the binary exists.
        """
        sdkmanager = self._sdkmanager_path()
        if os.path.exists(sdkmanager):
            return sdkmanager

        cache_path = sdk_cache_path(VOLUME_ROOT)
        ensure_dirs(cache_path)

        zip_path = os.path.join(cache_path, "cmdline-tools.zip")
        logger.info("Downloading cmdline-tools from %s …", CMDLINE_TOOLS_URL)
        subprocess.run(
            ["curl", "-fLo", zip_path, CMDLINE_TOOLS_URL],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )

        cmdline_tools_dir = os.path.join(cache_path, "cmdline-tools")
        ensure_dirs(cmdline_tools_dir)

        logger.info("Extracting cmdline-tools …")
        subprocess.run(
            ["unzip", "-q", zip_path, "-d", cmdline_tools_dir],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        os.remove(zip_path)

        # The zip contains a single ``cmdline-tools/`` directory.  Rename it
        # to ``latest/`` so the SDK layout is:
        #   {cache}/cmdline-tools/latest/bin/sdkmanager
        extracted = os.path.join(cmdline_tools_dir, "cmdline-tools")
        latest = os.path.join(cmdline_tools_dir, "latest")
        if os.path.exists(extracted):
            if os.path.exists(latest):
                shutil.rmtree(latest)
            os.rename(extracted, latest)

        # Ensure the binary is executable
        os.chmod(sdkmanager, 0o755)

        return sdkmanager

    # ------------------------------------------------------------------
    # Public API — version discovery (cached)
    # ------------------------------------------------------------------

    def fetch_available_platforms(self) -> List[str]:
        """Query ``sdkmanager --list`` for all ``platforms;android-*`` packages.

        Results are cached for 5 minutes.
        """
        if self._cache_fresh(self._platforms_cache_time):
            return self._platforms_cache  # type: ignore[return-value]

        result = self._run_sdkmanager("--list", "--channel=0")
        platforms = self._parse_sdkmanager_output(result.stdout, "platforms;android-")

        self._platforms_cache = platforms
        self._platforms_cache_time = time.monotonic()
        return platforms

    def fetch_available_build_tools(self) -> List[str]:
        """Query ``sdkmanager --list`` for all ``build-tools;*`` packages.

        Results are cached for 5 minutes.
        """
        if self._cache_fresh(self._build_tools_cache_time):
            return self._build_tools_cache  # type: ignore[return-value]

        result = self._run_sdkmanager("--list", "--channel=0")
        tools = self._parse_sdkmanager_output(result.stdout, "build-tools;")

        self._build_tools_cache = tools
        self._build_tools_cache_time = time.monotonic()
        return tools

    def fetch_available_ndk(self) -> List[str]:
        """Query ``sdkmanager --list`` for all ``ndk;*`` packages.

        Results are cached for 5 minutes.
        """
        if self._cache_fresh(self._ndk_cache_time):
            return self._ndk_cache  # type: ignore[return-value]

        result = self._run_sdkmanager("--list", "--channel=0")
        ndk_versions = self._parse_sdkmanager_output(result.stdout, "ndk;")

        self._ndk_cache = ndk_versions
        self._ndk_cache_time = time.monotonic()
        return ndk_versions

    # ------------------------------------------------------------------
    # Public API — install
    # ------------------------------------------------------------------

    def provision_sdk(self, version_string: str) -> str:
        """Install a single SDK package identified by *version_string*.

        Steps:
        1. Check local cache — if the install directory already exists, skip.
        2. Run ``sdkmanager --install "{version_string}"``.
        3. Accept all SDK licenses.

        Returns the absolute install path for the component.
        """
        cache_path = sdk_cache_path(VOLUME_ROOT)

        # Map SDK path separator to filesystem separator.
        # e.g.  "platforms;android-35" → "platforms/android-35"
        component_path = version_string.replace(";", os.sep)
        install_path = os.path.join(cache_path, component_path)

        if os.path.exists(install_path):
            logger.info(
                "SDK component already cached at %s — skipping install", install_path
            )
            return install_path

        ensure_dirs(cache_path)
        logger.info("Installing SDK component %s …", version_string)
        self._run_sdkmanager("--install", version_string)

        # Accept licenses so that subsequent builds don't hang on prompts.
        self._accept_licenses()

        return install_path

    # ------------------------------------------------------------------
    # Registry interface  (matches ``BuilderBase.provision`` signature)
    # ------------------------------------------------------------------

    def provision(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Provision SDK components specified in *config*.

        Supported config keys:
            - ``sdk_platform`` (int | str) — API level, e.g. ``35``
            - ``build_tools`` (str) — Build-tools version, e.g. ``"35.0.0"``
            - ``ndk_version`` (str) — NDK version, e.g. ``"27.0.12077973"``

        Returns a dict mapping component names to their install paths,
        or ``None`` when *config* is empty.
        """
        self.ensure_sdkmanager()

        provisioned: Dict[str, str] = {}

        if "sdk_platform" in config:
            version = f"platforms;android-{config['sdk_platform']}"
            path = self.provision_sdk(version)
            provisioned["platform"] = path

        if "build_tools" in config:
            version = f"build-tools;{config['build_tools']}"
            path = self.provision_sdk(version)
            provisioned["build_tools"] = path

        if "ndk_version" in config:
            version = f"ndk;{config['ndk_version']}"
            path = self.provision_sdk(version)
            provisioned["ndk"] = path

        return provisioned if provisioned else None
