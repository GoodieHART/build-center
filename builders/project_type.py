"""Project-type detection for the Android build pipeline.

Inspects a cloned repository and classifies it so the builder can pick
the right toolchain (Expo/RN, Flutter, or plain Gradle).
"""

import json
import os
from typing import Optional, Tuple


PROJECT_TYPE_FLUTTER = "flutter"
PROJECT_TYPE_EXPO_RN = "expo-rn"
PROJECT_TYPE_GRADLE = "gradle"
PROJECT_TYPE_UNKNOWN = "unknown"


def detect_project_type(repo_dir: str) -> Tuple[str, str]:
    """Detect the project type and return ``(type, build_root)``.

    ``build_root`` is the directory that contains the build entry point
    (``pubspec.yaml`` for Flutter, ``package.json`` for Expo/RN, or the
    directory holding ``gradlew`` for Gradle-native projects).

    Detection order (most specific first):

    1. ``pubspec.yaml`` containing a ``flutter:`` section  -> flutter
    2. ``package.json`` with an ``expo`` dependency          -> expo-rn
    3. ``gradlew`` at repo root or under ``android/``         -> gradle
    """
    # --- Flutter ---
    pubspec = os.path.join(repo_dir, "pubspec.yaml")
    if os.path.isfile(pubspec):
        try:
            with open(pubspec, "r", encoding="utf-8") as fh:
                content = fh.read()
            if "flutter:" in content and "sdk: flutter" in content:
                return PROJECT_TYPE_FLUTTER, repo_dir
            # A pubspec without the flutter sdk key is still Dart-only; treat
            # as unknown (no Android build entry point here).
        except OSError:
            pass

    # --- Expo / React Native ---
    package_json = os.path.join(repo_dir, "package.json")
    if os.path.isfile(package_json):
        try:
            with open(package_json, "r", encoding="utf-8") as fh:
                pkg = json.load(fh)
            deps = {}
            deps.update(pkg.get("dependencies", {}) or {})
            deps.update(pkg.get("devDependencies", {}) or {})
            if "expo" in deps:
                return PROJECT_TYPE_EXPO_RN, repo_dir
            # React Native without Expo still has android/gradlew
        except (OSError, ValueError):
            pass

    # --- Plain Gradle (Kotlin/Java/Compose) ---
    for candidate in (repo_dir, os.path.join(repo_dir, "android")):
        if os.path.isfile(os.path.join(candidate, "gradlew")):
            return PROJECT_TYPE_GRADLE, candidate

    # --- Fallback: Expo/RN repo without expo dep but with android/gradlew ---
    if os.path.isfile(os.path.join(repo_dir, "android", "gradlew")):
        return PROJECT_TYPE_GRADLE, os.path.join(repo_dir, "android")

    return PROJECT_TYPE_UNKNOWN, repo_dir


def is_flutter(repo_dir: str) -> bool:
    return detect_project_type(repo_dir)[0] == PROJECT_TYPE_FLUTTER
