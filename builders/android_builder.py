"""Android builder — Modal function to build Android apps via Gradle/Expo.

Implements the ``BuilderBase`` ABC and registers as ``"android"``.
"""

import glob as glob_module
import logging
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from builders import BuilderBase, register
from provisioning.android_provisioner import AndroidProvisioner
from utils.errors import (
    error,
    failed,
    handle_subprocess_error,
    is_failure,
    is_success,
    success,
)
from utils.volume import (
    VOLUME_ROOT,
    build_run_path,
    commit_volume,
    ensure_dirs,
    generate_build_id,
    sdk_cache_path,
)

logger = logging.getLogger(__name__)


@register("android")
class AndroidBuilder(BuilderBase):
    """Build Android apps from a Git repository (Expo / React Native).

    **Provisioning** installs SDK components (platforms, build-tools, NDK)
    via ``AndroidProvisioner``, including JDK 17 and Node.js if they are
    missing from the execution environment.

    **Build pipeline**::

        1. Create unique run directory
        2. ``git clone --depth=1`` (with optional auth token injection)
        3. ``npm install``
        4. ``npx expo prebuild --non-interactive``
        5. Write ``android/local.properties`` with ``sdk.dir``
        6. ``./gradlew assembleRelease``
        7. Verify and collect APK artifacts
        8. Commit Modal Volume
    """

    name = "android"

    # ------------------------------------------------------------------
    # Provisioning
    # ------------------------------------------------------------------

    def provision(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Ensure the build environment is ready.

        Installs / verifies:

        * Android SDK components (platform, build-tools, NDK) via
          ``AndroidProvisioner.provision_sdk()``.
        * JDK 17 (install via apt if missing).
        * Node.js (install via nodesource + apt if missing).

        Args:
            config: May contain an ``"sdk_components"`` key mapping to a dict
                    with ``platforms``, ``build_tools``, and ``ndk`` keys.

        Returns:
            A metadata dict (``{"provisioned": [...]}``) on success, or an
            error result dict on failure.
        """
        provisioned: List[str] = []

        # ---- Android SDK ----
        components = config.get(
            "sdk_components",
            {
                "platforms": ["android-34"],
                "build_tools": ["34.0.0"],
                "ndk": None,
            },
        )
        try:
            prov = AndroidProvisioner()
            prov.provision_sdk(components)
            provisioned.append("android_sdk")
        except Exception as exc:
            return error("provision:android_sdk", str(exc))

        # ---- JDK 17 ----
        try:
            self._install_jdk17()
            provisioned.append("jdk17")
        except Exception as exc:
            logger.warning("JDK 17 install failed (non-fatal): %s", exc)

        # ---- Node.js ----
        try:
            self._install_nodejs()
            provisioned.append("nodejs")
        except Exception as exc:
            logger.warning("Node.js install failed (non-fatal): %s", exc)

        logger.info("Provisioning complete: %s", provisioned)
        return {"provisioned": provisioned}

    # ------------------------------------------------------------------
    # Build pipeline
    # ------------------------------------------------------------------

    def build(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the full Android build pipeline.

        Expected ``config`` keys:

        * **repo_url** (``str``) – Git repository URL (required).
        * **branch** (``str``) – Branch to clone (default ``"main"``).
        * **access_token** (``str``, optional) – Auth token for private repos.
        * **volume** (``modal.Volume``) – Modal Volume for artifact persistence.

        Returns:
            A result dict per the ``BuildResult`` protocol (see
            :mod:`utils.errors`): ``{"status": "...", "message": "...",
            "stage": "...", "exit_code": ..., "artifacts": [...],
            "download_cmd": "..."}``.
        """
        repo_url: str = config.get("repo_url", "")
        branch: str = config.get("branch", "main")
        access_token: Optional[str] = config.get("access_token")
        volume: Any = config.get("volume")

        # ---- Validate inputs ----
        if not repo_url:
            return failed("validate", "repo_url is required", exit_code=1)

        # 1. Create unique run directory
        build_id = generate_build_id()
        run_path = build_run_path(VOLUME_ROOT, build_id)
        repo_dir = os.path.join(run_path, "repo")
        artifacts_dir = os.path.join(run_path, "artifacts")
        ensure_dirs(repo_dir)
        ensure_dirs(artifacts_dir)
        logger.info("Build directory: %s", run_path)

        # 2. Clone repository
        clone_url = repo_url
        if access_token:
            clone_url = repo_url.replace("https://", f"https://{access_token}@")
        try:
            print(f"  Cloning {repo_url} (branch: {branch})...")
            subprocess.run(
                ["git", "clone", "--depth=1", "--branch", branch, clone_url, repo_dir],
                check=True,
                capture_output=False,
                timeout=300,
            )
            print("  Repository cloned")
            logger.info("Repository cloned into %s", repo_dir)
        except subprocess.TimeoutExpired:
            return failed("clone", "Git clone timed out (300s)", exit_code=124)
        except subprocess.CalledProcessError as exc:
            return handle_subprocess_error(exc, "clone")

        # 3. npm install
        try:
            print("  Installing npm dependencies...")
            subprocess.run(
                ["npm", "install"],
                check=True,
                capture_output=False,
                cwd=repo_dir,
                timeout=300,
            )
            print("  npm install complete")
            logger.info("npm install completed")
        except subprocess.TimeoutExpired:
            return failed("npm_install", "npm install timed out (300s)", exit_code=124)
        except subprocess.CalledProcessError as exc:
            return handle_subprocess_error(exc, "npm_install")

        # 4. npx expo prebuild (non-interactive, graceful on failure)
        try:
            print("  Running expo prebuild...")
            subprocess.run(
                ["npx", "expo", "prebuild", "--non-interactive"],
                check=True,
                capture_output=False,
                cwd=repo_dir,
                timeout=300,
            )
            print("  expo prebuild complete")
            logger.info("expo prebuild completed")
        except subprocess.TimeoutExpired:
            return failed(
                "expo_prebuild", "expo prebuild timed out (300s)", exit_code=124
            )
        except subprocess.CalledProcessError as exc:
            # Expo prebuild is optional — log the failure but continue.
            logger.warning("expo prebuild failed (non-fatal): %s", exc)

        # 5. Write local.properties
        sdk_path = sdk_cache_path(VOLUME_ROOT)
        android_dir = os.path.join(repo_dir, "android")
        local_props_path = os.path.join(android_dir, "local.properties")
        try:
            ensure_dirs(android_dir)
            with open(local_props_path, "w") as f:
                f.write(f"sdk.dir={sdk_path}\n")
            logger.info("local.properties written: sdk.dir=%s", sdk_path)
        except OSError as exc:
            return error("local_properties", f"Cannot write local.properties: {exc}")

        # 6. gradlew assembleRelease
        gradle_exe = os.path.join(android_dir, "gradlew")
        if not os.path.isfile(gradle_exe):
            return failed(
                "gradle_build",
                f"gradlew not found at {gradle_exe}",
                exit_code=1,
            )

        try:
            print("  Running Gradle build (this may take a while)...")
            subprocess.run(
                ["./gradlew", "assembleRelease"],
                check=True,
                capture_output=False,
                cwd=android_dir,
                timeout=1200,
            )
            print("  Gradle build complete")
            logger.info("Gradle assembleRelease completed")
        except subprocess.TimeoutExpired:
            return failed(
                "gradle_build", "Gradle build timed out (1200s)", exit_code=124
            )
        except subprocess.CalledProcessError as exc:
            return handle_subprocess_error(exc, "gradle_build")

        # 7. Verify APK exists and copy to artifacts
        apk_pattern = os.path.join(
            repo_dir,
            "android",
            "app",
            "build",
            "outputs",
            "apk",
            "release",
            "*.apk",
        )
        apk_files = sorted(glob_module.glob(apk_pattern))
        if not apk_files:
            return failed(
                "verify_apk",
                "No APK found after build at {apk_pattern}".format(
                    apk_pattern=apk_pattern
                ),
                exit_code=1,
            )

        artifact_paths: List[str] = []
        for apk in apk_files:
            dest = os.path.join(artifacts_dir, os.path.basename(apk))
            shutil.copy2(apk, dest)
            artifact_paths.append(dest)
            logger.info("Artifact saved: %s", dest)

        # 8. Commit volume
        if volume is not None:
            try:
                commit_volume(volume)
            except Exception as exc:
                logger.warning("Volume commit failed: %s", exc)

        # 9. Build download command
        apk_basename = os.path.basename(apk_files[-1])
        volume_path = f"/runs/{build_id}/artifacts/{apk_basename}"
        download_cmd = f"modal volume get build-center-cache {volume_path} ."

        return success(
            message=f"Build {build_id} completed successfully",
            artifacts=artifact_paths,
            download_cmd=download_cmd,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _java_version() -> Optional[str]:
        """Return the JDK major version (e.g. ``"17"``) or ``None``."""
        try:
            result = subprocess.run(
                ["java", "-version"],
                check=True,
                capture_output=True,
                timeout=30,
            )
            # java -version writes to stderr.
            stderr = result.stderr.decode()
            for line in stderr.splitlines():
                if 'version "' in line:
                    # e.g. openjdk version "17.0.9" 2023-10-17
                    raw = line.split('"')[1]
                    return raw.split(".")[0]
            return None
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    @staticmethod
    def _install_jdk17() -> None:
        """Ensure JDK 17 is available; install via apt if missing."""
        version = AndroidBuilder._java_version()
        if version == "17":
            logger.info("JDK 17 already available.")
            return

        logger.info("JDK 17 not found (current: %s). Installing…", version)
        try:
            subprocess.run(
                ["apt-get", "update", "-qq"],
                check=True,
                capture_output=True,
                timeout=60,
            )
            subprocess.run(
                ["apt-get", "install", "-y", "openjdk-17-jdk"],
                check=True,
                capture_output=True,
                timeout=120,
            )
            logger.info("JDK 17 installed via apt.")
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("Failed to install JDK 17 via apt") from exc

    @staticmethod
    def _install_nodejs() -> None:
        """Ensure Node.js is available; install via nodesource + apt if missing."""
        try:
            result = subprocess.run(
                ["node", "--version"],
                check=True,
                capture_output=True,
                timeout=30,
            )
            logger.info("Node.js already available: %s", result.stdout.decode().strip())
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

        logger.info("Node.js not found. Installing via NodeSource…")
        try:
            subprocess.run(
                [
                    "curl", "-fsSL",
                    "https://deb.nodesource.com/setup_20.x",
                    "-o", "/tmp/nodesetup.sh",
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            subprocess.run(
                ["bash", "/tmp/nodesetup.sh"],
                check=True,
                capture_output=True,
                timeout=60,
            )
            subprocess.run(
                ["apt-get", "install", "-y", "nodejs"],
                check=True,
                capture_output=True,
                timeout=120,
            )
            logger.info("Node.js installed via NodeSource + apt.")
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("Failed to install Node.js via NodeSource") from exc
