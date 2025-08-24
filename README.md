GradleJail

GradleJail is a service that leverages Modal’s compute (CPU/GPU) environment to build Android apps faster. It was born out of my personal struggle building apps on a humble Celeron N3150, where juggling Gradle, SDKs, and dependencies felt like a full-time job.

Why GradleJail?

Offloads heavy Android builds to a cloud container.

Saves time and spares weak hardware from eternal Gradle suffering.

Demonstrates how to combine Expo/React Native builds with Modal’s serverless compute.

How It Works

You define your repo and branch.

GradleJail spins up a Modal container with all the necessary SDKs and tools.

It runs the build (expo prebuild + Gradle) and stores artifacts in a persistent volume.

Artifacts can be downloaded or pushed to a cloud bucket.

Motivation

Your Celeron deserves a break. My N3150 was taking ages for even the simplest builds, so I thought—why not let Modal do the heavy lifting? And thus, GradleJail was born.