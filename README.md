# Build Center

A cloud-native Android build orchestrator built on [Modal](https://modal.com/). Offload heavy Gradle/Expo builds to Modal's serverless GPU/CPU infrastructure — no more waiting on slow local hardware.

Born from the frustration of building Android apps on a Celeron N3150, Build Center wraps the full build pipeline (SDK provisioning, repo cloning, dependency installation, Gradle compilation) into a single `modal run` command.

---

## Quick Start

```bash
modal run build_center.py
```

The interactive wizard will guide you through selecting a repository, SDK configuration, and build flavor before dispatching the build to a Modal cloud container.

---

## Prerequisites

- [Modal CLI](https://modal.com/docs/guide) installed and authenticated (`modal token set --token-id ... --token-secret ...`)
- Python 3.11+
- A GitHub access token with `repo` scope (for private repositories)

### Secret Setup

Build Center requires a Modal Secret to store your GitHub access token securely:

```bash
modal secret create build-center-android access_token=YOUR_GITHUB_TOKEN
```

The access token is used when cloning private repositories. For public repos, the token is optional (you can leave the prompt blank).

---

## Build Steps Walkthrough

When you run `modal run build_center.py`, the wizard walks you through:

1. **Build type** — Android (only option in MVP)
2. **Repository URL** — e.g., `https://github.com/expo/expo.git`
3. **Branch** — e.g., `main` or `sdk-50`
4. **Access token** — optional, for private repos
5. **SDK platform** — fetches available Android API levels from Google's `sdkmanager`
6. **Build-tools version** — fetches available build-tools versions
7. **NDK version** — fetches available NDK versions (optional — select "None" to skip)
8. **JDK version** — choose 11, 17, or 21
9. **Build flavor** — `debug` or `release`
10. **Summary & confirmation** — review your selections before dispatching

Once confirmed, the build runs entirely in Modal's cloud:

- Provisioning: SDK components (platforms, build-tools, NDK) are cached in a persistent volume for reuse
- Build pipeline: `git clone` → `npm install` → `npx expo prebuild` → `./gradlew assembleRelease`
- Artifacts: APK files are stored in the `build-center-cache` Modal Volume

### Downloading Artifacts

After a successful build, the output includes a download command. You can also retrieve artifacts manually:

```bash
modal volume get build-center-cache /builds/runs/<build-id>/artifacts/<filename> .
```

Or list all builds:

```bash
modal volume ls build-center-cache /builds/runs/
```

---

## Project Structure

```
├── build_center.py          # Modal app entry point — wires up the wizard, provisioning, and builder
├── builders/                # Builder registry + strategy implementations
│   ├── __init__.py          #   BuilderBase ABC, registry (register/get_builder/list_builders)
│   └── android_builder.py   #   AndroidBuilder — Git clone, npm install, Gradle build
├── provisioning/            # Provisioner registry + implementations
│   ├── __init__.py          #   Provisioner registry (register/get_provisioner/list_provisioners)
│   └── android_provisioner.py  #   AndroidProvisioner — sdkmanager, license acceptance, caching
├── utils/                   # Shared utilities
│   ├── __init__.py
│   ├── errors.py            #   BuildResult protocol, factory functions, error handling
│   ├── secrets.py           #   Modal Secret validation helpers
│   ├── volume.py            #   Volume path conventions, build ID generation
│   └── wizard.py            #   Interactive CLI wizard (prompts, selection, confirmation)
└── README.md                # ← You are here
```

---

## Architecture

Build Center uses a **registry pattern** for extensibility. New builder types (e.g., iOS, Flutter) can be added by registering a new class:

```python
from builders import register, BuilderBase

@register("ios")
class IOSBuilder(BuilderBase):
    name = "ios"
    def provision(self, config): ...
    def build(self, config): ...
```

Provisioners follow the same pattern via `provisioning.register()`.

The main `build_center.py` Modal app ties everything together:

1. The **wizard** (`utils.wizard`) collects user input interactively
2. The **secret validator** (`utils.secrets`) ensures `build-center-android` exists before dispatch
3. The **provisioner** (`AndroidProvisioner`) fetches/installs SDK components in a Modal container
4. The **builder** (`AndroidBuilder`) clones, installs dependencies, and runs Gradle
5. Results follow the **BuildResult** protocol (`utils.errors`) — a typed dict with status, message, artifacts, and download command

---

## Motivation

Your Celeron deserves a break. My N3150 was taking ages for even the simplest builds, so I thought — why not let Modal do the heavy lifting? And thus, GradleJail (later reincarnated as Build Center) was born.

---

## Development

### Running Verification

```bash
# Check all modules compile
for f in build_center.py utils/*.py builders/*.py provisioning/*.py; do
  python3 -m py_compile "$f" && echo "OK: $f"
done

# Verify all imports
python3 -c "
import build_center
from utils import volume, wizard, errors, secrets
from builders import BuilderBase, register, get_builder, list_builders
from provisioning import get_provisioner, list_provisioners
print('ALL IMPORTS OK')
"
```

### Notes

- `build_center.py` runs locally (`modal run`) — it only uses Modal for the cloud build function, not for deployment
- SDK components are cached in the `build-center-cache` Modal Volume and reused across builds
- The nested `build-center/` directory (old standalone project) has been migrated into the `builders/` and `provisioning/` packages
