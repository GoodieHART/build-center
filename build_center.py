"""Build Center — Modal app that orchestrates Android builds.

Wires together the provisioning, builder, and wizard modules into a
single ``modal run`` entry point.

Usage
-----
    modal run build_center.py

This launches the interactive wizard which guides you through selecting
a repository, SDK components, and build flavor before dispatching the
build to a Modal cloud container.
"""

import modal
from builders import register, get_builder
from builders.android_builder import AndroidBuilder
from provisioning import register as preg
from provisioning.android_provisioner import AndroidProvisioner
import utils.wizard as wizard
from utils.volume import VOLUME_ROOT, commit_volume, sdk_cache_path
from utils.errors import is_success, is_failure

app = modal.App("build-center")

# ---------------------------------------------------------------------------
# Base builder image — JDK 17, git, curl, Node.js 20.x
# ---------------------------------------------------------------------------

builder_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("openjdk-17-jdk", "git", "curl", "unzip", "build-essential")
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs"
    )
)

# ---------------------------------------------------------------------------
# Persistent volume for SDK cache + build artifacts
# ---------------------------------------------------------------------------

volume = modal.Volume.from_name("build-center-cache", create_if_missing=True)

# ---------------------------------------------------------------------------
# Register builders — allows `get_builder("android")` lookup
# ---------------------------------------------------------------------------

register("android")(AndroidBuilder)

# ---------------------------------------------------------------------------
# Cloud function — runs inside a Modal container with full SDK + build tools
# ---------------------------------------------------------------------------


@app.function(
    image=builder_image,
    volumes={VOLUME_ROOT: volume},
    timeout=7200,
    cpu=4,
    memory=8192,
    secrets=[modal.Secret.from_name("build-center-android")],
)
def build_android(config: dict) -> dict:
    """Build Android app — runs provisioner + builder in Modal cloud.

    Provisioning is handled directly via ``AndroidProvisioner`` (bypassing
    the buggy ``AndroidBuilder.provision()`` method that passes a dict to
    ``provision_sdk()`` which expects a string).

    The actual build pipeline (clone, npm install, Gradle) is delegated to
    the ``AndroidBuilder`` retrieved from the builder registry.
    """
    # Look up the builder class via registry and instantiate it.
    builder_cls = get_builder("android")
    builder = builder_cls()

    # ---- Provision SDK components directly ----
    prov = AndroidProvisioner()
    prov.ensure_sdkmanager()

    if "sdk_platform" in config:
        prov.provision_sdk(f"platforms;android-{config['sdk_platform']}")

    if "build_tools" in config:
        prov.provision_sdk(f"build-tools;{config['build_tools']}")

    if "ndk_version" in config and config["ndk_version"]:
        prov.provision_sdk(f"ndk;{config['ndk_version']}")

    # ---- Run the build pipeline ----
    config["volume"] = volume
    result = builder.build(config)

    return result


# ---------------------------------------------------------------------------
# Local entrypoint — interactive wizard
# ---------------------------------------------------------------------------


@app.local_entrypoint()
def main():
    """Interactive build wizard that collects configuration and dispatches.

    Steps
    -----
    1.  Print welcome banner
    2.  Select build type (Android — only option for MVP)
    3.  Prompt for repository URL
    4.  Prompt for branch
    5.  Prompt for access token (optional, private repos)
    6.  Fetch & select SDK platform
    7.  Fetch & select build-tools version
    8.  Fetch & select NDK version
    9.  Select JDK version (11 / 17 / 21)
    10. Select build flavor (debug / release)
    11. Show build summary and confirm
    12. If confirmed: call ``build_android.remote(config)``
    13. Print result with download command or error details
    """

    # 1. Welcome
    wizard.print_info("Build Center \u2014 Android Builder")
    print()

    # 2. Build type (only Android for MVP)
    build_type = wizard.select_option(["Android"], "Select build type")

    # 3. Repo URL
    repo_url = wizard.prompt_string(
        "Enter repo URL",
        default="https://github.com/expo/expo.git",
    )

    # 4. Branch
    branch = wizard.prompt_string("Branch", default="main")

    # 5. Access token (optional — leave blank for public repos)
    access_token = wizard.prompt_string(
        "Access token (leave blank for public repos)"
    )

    # 6. Fetch / fallback for SDK platforms
    prov = AndroidProvisioner()
    try:
        prov.ensure_sdkmanager()
        raw_platforms = prov.fetch_available_platforms()
        sdk_platform_options = sorted(
            {p.replace("platforms;android-", "android-") for p in raw_platforms}
        )
    except Exception:
        sdk_platform_options = ["android-34", "android-35"]

    wizard.display_options(sdk_platform_options, "Available SDK Platforms")
    selected_platform = wizard.select_option(
        sdk_platform_options, "Select SDK platform"
    )
    sdk_platform = selected_platform.replace("android-", "")

    # 7. Fetch / fallback for build-tools
    try:
        raw_tools = prov.fetch_available_build_tools()
        build_tools_options = sorted(
            {t.replace("build-tools;", "") for t in raw_tools}
        )
    except Exception:
        build_tools_options = ["34.0.0"]

    wizard.display_options(build_tools_options, "Available Build Tools")
    build_tools = wizard.select_option(
        build_tools_options, "Select build-tools version"
    )

    # 8. Fetch / fallback for NDK
    try:
        raw_ndk = prov.fetch_available_ndk()
        ndk_options = sorted({n.replace("ndk;", "") for n in raw_ndk})
    except Exception:
        ndk_options = ["27.0.12077973"]

    ndk_options_with_none = ["None"] + ndk_options
    wizard.display_options(ndk_options_with_none, "Available NDK Versions")
    ndk_selected = wizard.select_option(
        ndk_options_with_none, "Select NDK version"
    )
    ndk_version = ndk_selected if ndk_selected != "None" else ""

    # 9. JDK version
    jdk_version = wizard.select_option(
        ["11", "17", "21"], "Select JDK version"
    )

    # 10. Build flavor
    build_flavor = wizard.select_option(
        ["debug", "release"], "Select build flavor"
    )

    # 11. Build summary and confirmation
    summary = {
        "Build Type": build_type,
        "Repo URL": repo_url,
        "Branch": branch,
        "Access Token": "***" if access_token else "(none)",
        "SDK Platform": selected_platform,
        "Build Tools": build_tools,
        "NDK Version": ndk_version if ndk_version else "(none)",
        "JDK Version": jdk_version,
        "Build Flavor": build_flavor,
    }

    confirmed = wizard.confirm_selection(summary)
    if not confirmed:
        wizard.print_info("Build cancelled.")
        return

    # 12. Assemble config dict for remote dispatch
    config = {
        "repo_url": repo_url,
        "branch": branch,
        "sdk_platform": sdk_platform,
        "build_tools": build_tools,
        "jdk_version": jdk_version,
        "build_flavor": build_flavor,
    }
    if access_token:
        config["access_token"] = access_token
    if ndk_version:
        config["ndk_version"] = ndk_version

    # 13. Dispatch remote build
    wizard.print_info("Starting build...")
    result = build_android.remote(config)

    # 14. Print result
    if is_success(result):
        wizard.print_success(result.get("message", "Build completed successfully!"))
        download_cmd = result.get("download_cmd")
        if download_cmd:
            print(f"\nDownload command: {download_cmd}")
    else:
        wizard.print_error(result.get("message", "Build failed."))
        stage = result.get("stage")
        if stage:
            print(f"Stage: {stage}")
