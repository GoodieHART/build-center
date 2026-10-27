"""Android builder — Modal function to build Android apps via Gradle/Expo/Flutter.

Implements the ``BuilderBase`` ABC and registers as ``"android"``.

The builder auto-detects the project type (Flutter, Expo/React Native, or
plain Gradle) after cloning and dispatches to the matching pipeline, so a
single entry point can build any of the supported project kinds.
"""

import glob as glob_module
import logging
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from builders import BuilderBase, register
from builders.project_type import (
    PROJECT_TYPE_EXPO_RN,
    PROJECT_TYPE_FLUTTER,
    PROJECT_TYPE_GRADLE,
    PROJECT_TYPE_UNKNOWN,
    detect_project_type,
)
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

DEFAULT_FLUTTER_VERSION = "stable"


@register("android")
class AndroidBuilder(BuilderBase):
    """Build Android apps from a Git repository.

    Supports three project kinds (auto-detected after clone):

    * **Flutter** (``pubspec.yaml`` with ``flutter:`` + ``sdk: flutter``)
    * **Expo / React Native** (``package.json`` with an ``expo`` dependency)
    * **Plain Gradle** (``gradlew`` at repo root or under ``android/``)

    Build variants (Flutter only):

    * ``base``  — standard ``flutter build apk`` (dynamic features stay
      on-demand, small APK).
    * ``full``  — bundles every dynamic-feature module into one monolithic
      APK (removes the ``dynamicFeatures.addAll(...)`` block at build time).
    """

    name = "android"

    # ------------------------------------------------------------------
    # Provisioning
    # ------------------------------------------------------------------

    def provision(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Ensure the build environment is ready (Android SDK + JDK 17 + Node).

        Flutter itself is provided by the container image; this method only
        handles the Android SDK components requested by *config*.
        """
        provisioned: List[str] = []

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

        try:
            self._install_jdk17()
            provisioned.append("jdk17")
        except Exception as exc:
            logger.warning("JDK 17 install failed (non-fatal): %s", exc)

        try:
            self._install_nodejs()
            provisioned.append("nodejs")
        except Exception as exc:
            logger.warning("Node.js install failed (non-fatal): %s", exc)

        logger.info("Provisioning complete: %s", provisioned)
        return {"provisioned": provisioned}

    # ------------------------------------------------------------------
    # Build pipeline (dispatches by project type)
    # ------------------------------------------------------------------

    def build(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the full Android build pipeline.

        Clones the repository, detects the project type, and dispatches to
        the matching pipeline (Expo/RN, Flutter, or plain Gradle).
        """
        repo_url: str = config.get("repo_url", "")
        branch: str = config.get("branch", "main")
        access_token: Optional[str] = config.get("access_token")
        volume: Any = config.get("volume")

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

        # 3. Detect project type
        project_type, build_root = detect_project_type(repo_dir)
        print(f"  Detected project type: {project_type}")
        logger.info("Project type: %s (build root: %s)", project_type, build_root)

        if project_type == PROJECT_TYPE_UNKNOWN:
            return failed(
                "detect",
                "Could not detect a supported project type (Flutter, Expo/RN, "
                "or Gradle) in the cloned repository.",
                exit_code=1,
            )

        # 4. Dispatch
        if project_type == PROJECT_TYPE_FLUTTER:
            return self._build_flutter(config, build_id, repo_dir, build_root, artifacts_dir, volume)
        if project_type == PROJECT_TYPE_EXPO_RN:
            return self._build_expo_rn(config, build_id, repo_dir, build_root, artifacts_dir, volume)
        return self._build_gradle(config, build_id, repo_dir, build_root, artifacts_dir, volume)

    # ------------------------------------------------------------------
    # Expo / React Native pipeline (original behaviour)
    # ------------------------------------------------------------------

    def _build_expo_rn(self, config, build_id, repo_dir, build_root, artifacts_dir, volume):
        # 1. npm install
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
        except subprocess.TimeoutExpired:
            return failed("npm_install", "npm install timed out (300s)", exit_code=124)
        except subprocess.CalledProcessError as exc:
            return handle_subprocess_error(exc, "npm_install")

        # 2. expo prebuild (non-fatal)
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
        except subprocess.TimeoutExpired:
            return failed("expo_prebuild", "expo prebuild timed out (300s)", exit_code=124)
        except subprocess.CalledProcessError as exc:
            logger.warning("expo prebuild failed (non-fatal): %s", exc)

        android_dir = os.path.join(repo_dir, "android")
        return self._gradle_build_and_collect(
            config, build_id, repo_dir, android_dir, artifacts_dir, volume,
            stage_prefix="expo",
        )

    # ------------------------------------------------------------------
    # Plain Gradle pipeline
    # ------------------------------------------------------------------

    def _build_gradle(self, config, build_id, repo_dir, build_root, artifacts_dir, volume):
        # build_root is the dir holding gradlew
        return self._gradle_build_and_collect(
            config, build_id, repo_dir, build_root, artifacts_dir, volume,
            stage_prefix="gradle",
        )

    # ------------------------------------------------------------------
    # Flutter pipeline
    # ------------------------------------------------------------------

    def _build_flutter(self, config, build_id, repo_dir, build_root, artifacts_dir, volume):
        build_flavor = config.get("build_flavor", "release")
        flutter_version = config.get("flutter_version") or DEFAULT_FLUTTER_VERSION
        build_variant = (config.get("build_variant") or "base").lower()

        sdk_path = sdk_cache_path(VOLUME_ROOT).rstrip("/")

        # 1. Ensure requested Flutter version
        self._ensure_flutter_version(flutter_version)

        # 2. flutter pub get
        try:
            print("  Running flutter pub get...")
            env = dict(os.environ)
            env["ANDROID_HOME"] = sdk_path
            env["ANDROID_SDK_ROOT"] = sdk_path
            env["GRADLE_OPTS"] = "-Dorg.gradle.parallel=true -Dorg.gradle.workers.max=8"
            rc, tail = self._run_streamed(
                ["flutter", "pub", "get"],
                cwd=repo_dir,
                env=env,
                timeout=300,
                label="flutter pub get",
            )
            if rc != 0:
                print("  flutter pub get FAILED:\n" + tail)
                return failed("flutter_pub_get", "flutter pub get failed: " + tail[-500:], exit_code=rc)
            print("  flutter pub get complete")
        except subprocess.TimeoutExpired:
            return failed("flutter_pub_get", "flutter pub get timed out (300s)", exit_code=124)

        android_dir = os.path.join(repo_dir, "android")

        # 3. Debug signing for release builds without a keystore
        if build_flavor == "release":
            self._setup_debug_signing(android_dir)

        # 4. Write local.properties (sdk.dir)
        local_props_path = os.path.join(android_dir, "local.properties")
        try:
            ensure_dirs(android_dir)
            with open(local_props_path, "w") as f:
                f.write(f"sdk.dir={sdk_path}\n")
            logger.info("local.properties written: sdk.dir=%s", sdk_path)
            print(f"  SDK path: {sdk_path} (exists={os.path.isdir(sdk_path)})")
            if os.path.isdir(sdk_path):
                print("  SDK contents:", os.listdir(sdk_path))
        except OSError as exc:
            return error("local_properties", f"Cannot write local.properties: {exc}")

        env = dict(os.environ)
        env["ANDROID_HOME"] = sdk_path
        env["ANDROID_SDK_ROOT"] = sdk_path
        env["GRADLE_OPTS"] = "-Dorg.gradle.parallel=true -Dorg.gradle.workers.max=8"

        build_mode_flag = "--release" if build_flavor == "release" else "--debug"

        # 4b. Enable Gradle parallelism for faster builds
        self._enable_gradle_parallelism(android_dir)

        if build_variant == "full":
            # 5. Full variant: build an AAB, then use bundletool to produce a
            #    single universal APK containing every dynamic-feature module.
            cmd = ["flutter", "build", "appbundle", build_mode_flag]
            try:
                print(f"  Running flutter build appbundle ({build_flavor}, variant=full)...")
                rc, tail = self._run_streamed(
                    cmd,
                    cwd=repo_dir,
                    env=env,
                    timeout=1800,
                    label="flutter build appbundle",
                )
                if rc != 0:
                    print("  flutter build appbundle FAILED:\n" + tail)
                    return failed("flutter_build", "flutter build appbundle failed: " + tail[-800:], exit_code=rc)
                print("  flutter build appbundle complete")
            except subprocess.TimeoutExpired:
                return failed("flutter_build", "flutter build appbundle timed out (1800s)", exit_code=124)

            aab_pattern = os.path.join(repo_dir, "build", "app", "outputs", "bundle", "release", "*.aab")
            aab_files = sorted(glob_module.glob(aab_pattern))
            if not aab_files:
                return failed("verify_aab", f"No AAB found after build at {aab_pattern}", exit_code=1)

            # bundletool -> universal APK
            aab = aab_files[-1]
            apks_out = os.path.join(artifacts_dir, "full.apks")
            try:
                print("  Running bundletool build-apks (universal)...")
                subprocess.run(
                    ["java", "-jar", "/usr/local/bin/bundletool.jar", "build-apks", "--mode=universal",
                     f"--bundle={aab}", f"--output={apks_out}"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    env=env,
                )
                print("  bundletool complete")
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
                logger.warning("bundletool failed (%s); falling back to AAB artifact", exc)
                apks_out = None

            apk_files = []
            if apks_out and os.path.isfile(apks_out):
                universal = os.path.join(artifacts_dir, "app-release-universal.apk")
                try:
                    subprocess.run(
                        ["unzip", "-p", apks_out, "universal.apk"],
                        stdout=open(universal, "wb"),
                        check=True,
                        timeout=120,
                    )
                    if os.path.isfile(universal) and os.path.getsize(universal) > 0:
                        apk_files = [universal]
                except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
                    logger.warning("Could not extract universal.apk: %s", exc)

            # Fallback: if bundletool/extraction failed, ship the AAB.
            if not apk_files:
                for aab in aab_files:
                    dest = os.path.join(artifacts_dir, os.path.basename(aab))
                    shutil.copy2(aab, dest)
                    apk_files.append(dest) if False else None
                # Report AAB as the artifact instead.
                artifact_paths_aab = []
                for aab in aab_files:
                    dest = os.path.join(artifacts_dir, os.path.basename(aab))
                    shutil.copy2(aab, dest)
                    artifact_paths_aab.append(dest)
                if volume is not None:
                    try:
                        commit_volume(volume)
                    except Exception as exc:
                        logger.warning("Volume commit failed: %s", exc)
                aab_basename = os.path.basename(aab_files[-1])
                volume_path = f"/runs/{build_id}/artifacts/{aab_basename}"
                download_cmd = f"modal volume get build-center-cache {volume_path} ."
                return success(
                    message=f"Build {build_id} completed (AAB; bundletool unavailable)",
                    artifacts=artifact_paths_aab,
                    download_cmd=download_cmd,
                )
        else:
            # 5b. Base variant: standard flutter build apk
            cmd = ["flutter", "build", "apk", build_mode_flag]
            if config.get("split_per_abi"):
                cmd.append("--split-per-abi")
            try:
                print(f"  Running flutter build apk ({build_flavor}, variant=base)...")
                rc, tail = self._run_streamed(
                    cmd,
                    cwd=repo_dir,
                    env=env,
                    timeout=1800,
                    label="flutter build apk",
                )
                if rc != 0:
                    print("  flutter build FAILED:\n" + tail)
                    return failed("flutter_build", "flutter build failed: " + tail[-800:], exit_code=rc)
                print("  flutter build complete")
            except subprocess.TimeoutExpired:
                return failed("flutter_build", "flutter build timed out (1800s)", exit_code=124)

            apk_pattern = os.path.join(repo_dir, "build", "app", "outputs", "flutter-apk", "*.apk")
            apk_files = sorted(glob_module.glob(apk_pattern))
            if not apk_files:
                return failed(
                    "verify_apk",
                    f"No APK found after build at {apk_pattern}",
                    exit_code=1,
                )

        # 7. Collect artifacts (base variant apk files are already in apk_files)
        if build_variant == "base":
            pass  # apk_files already populated
        # For full variant, apk_files populated by bundletool extraction above.

        if not apk_files:
            return failed("verify_apk", "No APK artifacts produced", exit_code=1)

        artifact_paths: List[str] = []
        for apk in apk_files:
            if not os.path.isfile(apk):
                continue
            dest = os.path.join(artifacts_dir, os.path.basename(apk))
            if os.path.abspath(apk) != os.path.abspath(dest):
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

        variant_note = " (full/monolithic)" if build_variant == "full" else ""
        return success(
            message=f"Build {build_id} completed successfully{variant_note}",
            artifacts=artifact_paths,
            download_cmd=download_cmd,
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

        variant_note = " (full/monolithic)" if build_variant == "full" else ""
        return success(
            message=f"Build {build_id} completed successfully{variant_note}",
            artifacts=artifact_paths,
            download_cmd=download_cmd,
        )

    # ------------------------------------------------------------------
    # Shared Gradle build + artifact collection
    # ------------------------------------------------------------------

    def _gradle_build_and_collect(self, config, build_id, repo_dir, android_dir, artifacts_dir, volume, stage_prefix="gradle"):
        # Write local.properties
        local_props_path = os.path.join(android_dir, "local.properties")
        try:
            ensure_dirs(android_dir)
            with open(local_props_path, "w") as f:
                f.write(f"sdk.dir={sdk_path}\n")
            logger.info("local.properties written: sdk.dir=%s", sdk_path)
            print(f"  SDK path: {sdk_path} (exists={os.path.isdir(sdk_path)})")
            if os.path.isdir(sdk_path):
                print("  SDK contents:", os.listdir(sdk_path))
        except OSError as exc:
            return error("local_properties", f"Cannot write local.properties: {exc}")

        # Enable Gradle parallelism for faster builds
        self._enable_gradle_parallelism(android_dir)

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
            return failed("gradle_build", "Gradle build timed out (1200s)", exit_code=124)
        except subprocess.CalledProcessError as exc:
            return handle_subprocess_error(exc, "gradle_build")

        apk_pattern = os.path.join(
            android_dir, "app", "build", "outputs", "apk", "release", "*.apk",
        )
        apk_files = sorted(glob_module.glob(apk_pattern))
        if not apk_files:
            return failed(
                "verify_apk",
                f"No APK found after build at {apk_pattern}",
                exit_code=1,
            )

        artifact_paths: List[str] = []
        for apk in apk_files:
            dest = os.path.join(artifacts_dir, os.path.basename(apk))
            shutil.copy2(apk, dest)
            artifact_paths.append(dest)
            logger.info("Artifact saved: %s", dest)

        if volume is not None:
            try:
                commit_volume(volume)
            except Exception as exc:
                logger.warning("Volume commit failed: %s", exc)

        apk_basename = os.path.basename(apk_files[-1])
        volume_path = f"/runs/{build_id}/artifacts/{apk_basename}"
        download_cmd = f"modal volume get build-center-cache {volume_path} ."

        return success(
            message=f"Build {build_id} completed successfully",
            artifacts=artifact_paths,
            download_cmd=download_cmd,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _remove_dynamic_features(gradle_path: str) -> None:
        """Remove the ``dynamicFeatures.addAll(...)`` block from a Gradle file.

        Used by the ``full`` variant to bundle every dynamic-feature module
        into the base APK. Only touches the cloned copy (build-time only).
        """
        if not os.path.isfile(gradle_path):
            return
        try:
            with open(gradle_path, "r", encoding="utf-8") as fh:
                content = fh.read()
            # Match the whole dynamicFeatures.addAll( setOf( ... ) ) block.
            # The outer closing paren sits at 4-space indent on its own line,
            # so anchor on that to avoid stopping at the inner setOf() close.
            new_content = re.sub(
                r"\n\n    dynamicFeatures\.addAll\(.*?^    \)\s*\n",
                "\n\n",
                content,
                count=1,
                flags=re.DOTALL | re.MULTILINE,
            )
            # Fallback: single-line form
            new_content = re.sub(
                r"dynamicFeatures\.addAll\([^)]*\)\s*\n",
                "",
                new_content,
            )
            if new_content != content:
                with open(gradle_path, "w", encoding="utf-8") as fh:
                    fh.write(new_content)
                logger.info("Removed dynamicFeatures block from %s", gradle_path)
        except OSError as exc:
            logger.warning("Could not edit %s: %s", gradle_path, exc)

    def _setup_debug_signing(self, android_dir: str) -> None:
        """Generate a debug keystore + key.properties for release builds.

        roxum-ide's release signingConfig reads key.properties; if it is
        missing the build fails. We create a debug keystore so release builds
        succeed without a real signing key (suitable for testing only).
        """
        app_dir = os.path.join(android_dir, "app")
        ensure_dirs(app_dir)
        key_props = os.path.join(android_dir, "key.properties")
        if os.path.isfile(key_props):
            return
        # Gradle resolves storeFile relative to the app module dir (android/app/),
        # so place the keystore there.
        keystore = os.path.join(app_dir, "debug.keystore")
        if not os.path.isfile(keystore):
            try:
                subprocess.run(
                    [
                        "keytool", "-genkey", "-v",
                        "-keystore", keystore,
                        "-storepass", "android",
                        "-alias", "androiddebugkey",
                        "-keypass", "android",
                        "-keyalg", "RSA",
                        "-keysize", "2048",
                        "-validity", "10000",
                        "-dname", "CN=Android Debug,O=Android,C=US",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                logger.info("Generated debug keystore at %s", keystore)
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
                logger.warning("Could not generate debug keystore: %s", exc)
                return
        try:
            with open(key_props, "w") as fh:
                fh.write("storePassword=android\n")
                fh.write("keyPassword=android\n")
                fh.write("keyAlias=androiddebugkey\n")
                fh.write("storeFile=debug.keystore\n")
            logger.info("Wrote debug key.properties at %s", key_props)
        except OSError as exc:
            logger.warning("Could not write key.properties: %s", exc)

    @staticmethod
    def _enable_gradle_parallelism(android_dir: str) -> None:
        """Ensure Gradle runs in parallel with extra workers."""
        props_path = os.path.join(android_dir, "gradle.properties")
        existing = ""
        if os.path.isfile(props_path):
            try:
                with open(props_path, "r", encoding="utf-8") as fh:
                    existing = fh.read()
            except OSError:
                existing = ""

        settings = {
            "org.gradle.parallel": "true",
            "org.gradle.workers.max": "8",
            "org.gradle.caching": "true",
            "org.gradle.daemon": "true",
            "org.gradle.configureondemand": "true",
        }
        lines = existing.splitlines() if existing else []
        present = {ln.split("=", 1)[0].strip() for ln in lines if "=" in ln}
        changed = False
        for key, val in settings.items():
            if key not in present:
                lines.append(f"{key}={val}")
                changed = True
        if changed or not existing:
            try:
                with open(props_path, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
                logger.info("Gradle parallelism settings written to %s", props_path)
            except OSError as exc:
                logger.warning("Could not write gradle.properties: %s", exc)

    @staticmethod
    def _run_streamed(cmd, cwd, env, timeout, label="build"):
        import subprocess as _sp
        import threading

        proc = _sp.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=_sp.PIPE,
            stderr=_sp.STDOUT,
            text=True,
            bufsize=1,
        )
        buf = []
        def reader():
            try:
                for line in proc.stdout:
                    print(line, end="", flush=True)
                    buf.append(line)
                    if len(buf) > 2000:
                        del buf[:500]
            except Exception:
                pass
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        try:
            proc.wait(timeout=timeout)
        except _sp.TimeoutExpired:
            proc.kill()
            t.join(timeout=5)
            raise
        t.join(timeout=5)
        combined = "".join(buf)
        tail = combined[-4000:]
        return proc.returncode, tail

    def _ensure_flutter_version(self, version: Optional[str]) -> None:
        """Switch the installed Flutter to *version* if requested.

        Accepts a git tag/branch (e.g. ``3.24.5``) or ``stable``/``latest``
        (which leaves the image's default Flutter in place).
        """
        if not version or version.lower() in ("stable", "latest", "master", ""):
            return
        flutter_root = os.environ.get("FLUTTER_ROOT", "/opt/flutter")
        if not os.path.isdir(flutter_root):
            logger.warning("Flutter root %s not found; skipping version switch", flutter_root)
            return
        try:
            print(f"  Switching Flutter to {version}...")
            subprocess.run(
                ["git", "-C", flutter_root, "fetch", "--tags", "--depth=1", "origin", version],
                check=True,
                capture_output=True,
                timeout=300,
            )
            subprocess.run(
                ["git", "-C", flutter_root, "checkout", version],
                check=True,
                capture_output=True,
                timeout=120,
            )
            subprocess.run(
                ["flutter", "precache", "--android"],
                check=False,
                capture_output=True,
                timeout=600,
                cwd=flutter_root,
            )
            print(f"  Flutter switched to {version}")
            logger.info("Switched Flutter to %s", version)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning("Could not switch Flutter to %s: %s (continuing with installed)", version, exc)

    @staticmethod
    def _java_version() -> Optional[str]:
        try:
            result = subprocess.run(
                ["java", "-version"],
                check=True,
                capture_output=True,
                timeout=30,
            )
            stderr = result.stderr.decode()
            for line in stderr.splitlines():
                if 'version "' in line:
                    raw = line.split('"')[1]
                    return raw.split(".")[0]
            return None
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    @staticmethod
    def _install_jdk17() -> None:
        version = AndroidBuilder._java_version()
        if version == "17":
            logger.info("JDK 17 already available.")
            return
        logger.info("JDK 17 not found (current: %s). Installing…", version)
        try:
            subprocess.run(["apt-get", "update", "-qq"], check=True, capture_output=True, timeout=60)
            subprocess.run(["apt-get", "install", "-y", "openjdk-17-jdk"], check=True, capture_output=True, timeout=120)
            logger.info("JDK 17 installed via apt.")
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("Failed to install JDK 17 via apt") from exc

    @staticmethod
    def _install_nodejs() -> None:
        try:
            result = subprocess.run(["node", "--version"], check=True, capture_output=True, timeout=30)
            logger.info("Node.js already available: %s", result.stdout.decode().strip())
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
        logger.info("Node.js not found. Installing via NodeSource…")
        try:
            subprocess.run(["curl", "-fsSL", "https://deb.nodesource.com/setup_20.x", "-o", "/tmp/nodesetup.sh"], check=True, capture_output=True, timeout=60)
            subprocess.run(["bash", "/tmp/nodesetup.sh"], check=True, capture_output=True, timeout=60)
            subprocess.run(["apt-get", "install", "-y", "nodejs"], check=True, capture_output=True, timeout=120)
            logger.info("Node.js installed via NodeSource + apt.")
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("Failed to install Node.js via NodeSource") from exc
