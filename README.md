# NOM 1 Android Port

Android porting workspace for **NOM / NOM 1** (Living Mobile / GDC J2ME build).

This repository contains only the Android porting glue, configuration, and patches.
The original game JAR is intentionally **not committed**. Put a legally obtained copy at
`game/nom1.jar` before running the preparation script.

## Target

- Samsung Galaxy S8 and newer Galaxy devices
- Android 7.0 / API 24 or newer
- Portrait, aspect-ratio-preserving fullscreen rendering
- No on-screen virtual keypad
- Touch controls:
  - Tap -> J2ME `5` / OK / FIRE
  - Swipe up -> J2ME `2`
  - Swipe down -> J2ME `8`
  - Swipe left -> J2ME `4`
  - Swipe right -> J2ME `6`

## Runtime strategy

The port uses the open-source **J2ME Loader** runtime as a compatibility layer rather than
rewriting the obfuscated 2007 MIDP game from scratch. J2ME Loader is Apache-2.0 licensed and
supports J2ME applications on Android.

The setup pins the engine to the J2ME Loader 1.8.2 release and applies NOM-specific patches:

1. Android minimum SDK is raised to API 24.
2. A dedicated launcher installs the bundled local `nom1.jar` into the private emulator area.
3. A default 176x208 portrait profile is created and scaled to fit the Android display.
4. The virtual keypad is disabled.
5. Canvas touch handling is replaced with NOM tap/swipe controls.
6. The app launches NOM directly instead of showing the normal emulator library UI.

## Setup on Windows

Requirements:

- Git
- Python 3.10+
- Android Studio with Android SDK
- JDK required by the pinned Android Gradle Plugin

Clone this repository, then place your game file here:

```text
Nom1-Porting/
  game/
    nom1.jar
```

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Then open the generated `engine` directory in Android Studio.

Build the debug APK with:

```powershell
cd engine
.\gradlew.bat :app:assembleOpenDebug
```

The APK will be under `engine/app/build/outputs/apk/open/debug/`.

### If Android Studio reports a missing `engine/keystore.properties`

Pull the latest port scripts and run setup again. The current patch makes the upstream
release keystore optional for Gradle Sync and debug builds:

```powershell
git pull
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

You do **not** need to create a release keystore to build or run `:app:assembleOpenDebug`.
A keystore is only needed later when you want a signed release APK.

## Known source JAR used for initial analysis

The uploaded reference build identified itself as:

- MIDlet-Name: `NOM (by GDC)`
- MIDlet-Version: `1.0.25`
- MIDlet-Vendor: `Living Mobile`
- MIDP: `1.0`
- CLDC: `1.0`
- Main class: `Nom1`

The preparation script validates these fields but does not require an identical filename.

## Upstream

Runtime base: https://github.com/nikita36078/J2ME-Loader

J2ME Loader is licensed under Apache License 2.0. This repository's own glue code can be
licensed separately, but upstream notices must be retained in the generated engine checkout.
