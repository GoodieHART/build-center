import modal

app = modal.App("gradle-jail")

android_image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("openjdk-17-jdk", "git","curl","unzip","build-essential","clang")
    .run_commands("curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
  && apt-get install -y nodejs")
    .run_commands(
        "mkdir -p ~/android-sdk/cmdline-tools",
        "curl -o ~/android-sdk/cmdline-tools.zip https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip",
        "unzip ~/android-sdk/cmdline-tools.zip -d ~/android-sdk/cmdline-tools",
        "rm ~/android-sdk/cmdline-tools.zip",
        "mv ~/android-sdk/cmdline-tools/cmdline-tools ~/android-sdk/cmdline-tools/latest"
    )
    .run_commands(
        "yes | ~/android-sdk/cmdline-tools/latest/bin/sdkmanager --licenses",
        "yes | ~/android-sdk/cmdline-tools/latest/bin/sdkmanager \"platform-tools\" \"platforms;android-31\" \"build-tools;31.0.0\""
    )

)

volume = modal.Volume.from_name("gradle-build-cache", create_if_missing=True)

@app.function(image = android_image, volumes = {"/build": volume}, timeout = 3600, cpu = 4, memory = 8192)
def build_android_app(repo_url, branch="main"):
    import os, subprocess
    
    os.chdir("/build")

    repo_name = os.path.splitext(os.path.basename(repo_url))[0]

    if os.path.exists(repo_name):
        os.chdir(repo_name)
        subprocess.run(["git", "pull"], check=True)
    else:
        subprocess.run(["git", "clone", "--depth=1", repo_url], check=True)
        os.chdir(repo_name)
    subprocess.run(["npm", "install"], check=True)

    subprocess.run(["npx", "expo", "prebuild"], check=True)
    
    sdk_path = os.path.expanduser("~/android-sdk")
    local_props_path = os.path.join("android", "local.properties")

    with open(local_props_path, "w") as f:
        f.write(f"sdk.dir={sdk_path}\n")

    subprocess.run(["./gradlew", "assembleRelease"], cwd="android", check=True)

    return {
        "status": "success",
        "message": "Build completed successfully",
        "artifacts": ["/build/" + os.path.basename(repo_url) + "/android/app/build/outputs/apk/release/"]
    }