DEFINE Stub "GradleJail"

DEFINE Image "android_image":
    START from debian-slim
    INSTALL openjdk-17, wget, unzip, git, curl
    INSTALL node + yarn + expo-cli
    INSTALL android sdk commandline-tools
    ACCEPT android sdk licenses
    INSTALL platform-tools + build-tools + platforms (Android 31+)

DEFINE Volume "build_volume" (persisted)
    USED for Gradle cache, Android SDK cache, output APKs

DEFINE Function "build_android(repo_url, branch='main')":
    MOUNT volume at /root/.gradle and /root/project

    IF /root/project/app DOES NOT EXIST:
        GIT clone repo_url at branch into /root/project/app
    ELSE:
        GIT pull latest changes into /root/project/app

    CHANGE DIRECTORY to /root/project/app

    RUN "npx expo prebuild"   # generates Android native project
    RUN "./gradlew assembleRelease"  # build APK

    STORE output files (APK/AAB) into volume
    PRINT "Build complete. Artifacts saved."

TRIGGER:
    From CLI:
        modal run GradleJail::build_android --repo_url=https://github.com/user/project.git
