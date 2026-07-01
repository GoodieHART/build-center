import modal

app = modal.App("build-center")

builder_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("openjdk-17-jdk", "git", "curl", "unzip", "build-essential")
    .run_commands("curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs")
)

volume = modal.Volume.from_name("build-center-cache", create_if_missing=True)
