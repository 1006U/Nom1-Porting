#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import sys
import zipfile
from pathlib import Path


EXPECTED_MAIN_CLASS = "Nom1"
EXPECTED_VERSION = "1.0.25"
EXPECTED_VENDOR = "Living Mobile"


def read_manifest(jar_path: Path) -> dict[str, str]:
    with zipfile.ZipFile(jar_path) as zf:
        raw = zf.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace")

    # JAR manifests support continuation lines beginning with one space.
    unfolded: list[str] = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        if line.startswith(" ") and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)

    result: dict[str, str] = {}
    for line in unfolded:
        if ": " in line:
            key, value = line.split(": ", 1)
            result[key.strip()] = value.strip()
    return result


def validate_jar(jar_path: Path) -> dict[str, str]:
    if not jar_path.is_file():
        raise FileNotFoundError(f"NOM JAR not found: {jar_path}")

    manifest = read_manifest(jar_path)
    midlet_1 = manifest.get("MIDlet-1", "")
    main_class = midlet_1.rsplit(",", 1)[-1].strip() if midlet_1 else ""

    errors: list[str] = []
    if main_class != EXPECTED_MAIN_CLASS:
        errors.append(f"MIDlet main class is {main_class!r}, expected {EXPECTED_MAIN_CLASS!r}")
    if manifest.get("MIDlet-Vendor") != EXPECTED_VENDOR:
        errors.append(
            f"MIDlet vendor is {manifest.get('MIDlet-Vendor')!r}, expected {EXPECTED_VENDOR!r}"
        )
    if errors:
        raise RuntimeError("The supplied JAR does not look like the analyzed NOM 1 build:\n- "
                           + "\n- ".join(errors))

    version = manifest.get("MIDlet-Version")
    if version != EXPECTED_VERSION:
        print(
            f"warning: JAR version is {version!r}; the reference port was made for "
            f"{EXPECTED_VERSION!r}",
            file=sys.stderr,
        )

    return manifest


def replace_once(path: Path, old: str, new: str, description: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"Could not apply {description}: expected text not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_build_files(engine: Path) -> None:
    root_gradle = engine / "build.gradle"
    app_gradle = engine / "app" / "build.gradle"

    text = root_gradle.read_text(encoding="utf-8")
    text2 = re.sub(r"MIN_SDK\s*=\s*\d+", "MIN_SDK = 24", text, count=1)
    if text2 == text and "MIN_SDK = 24" not in text:
        raise RuntimeError("Could not set Android minimum SDK to 24")
    root_gradle.write_text(text2, encoding="utf-8")

    open_start = """        open {
            buildConfigField 'boolean', 'FULL_EMULATOR', 'true'
"""
    open_replacement = """        open {
            applicationId "com.u1006.nom1"
            versionName "1.0.25-port1"
            resValue 'string', 'app_name', 'NOM 1'
            buildConfigField 'boolean', 'FULL_EMULATOR', 'true'
"""
    replace_once(app_gradle, open_start, open_replacement, "NOM open flavor configuration")


def patch_installer(engine: Path) -> None:
    path = engine / "app" / "src" / "main" / "java" / "ru" / "woesss" / "j2me" / "installer" / "AppInstaller.java"
    old = """\tDescriptor getNewDescriptor() {
\t\treturn newDesc;
\t}
"""
    new = """\tAppItem getCurrentApp() {
\t\treturn currentApp;
\t}

\tDescriptor getNewDescriptor() {
\t\treturn newDesc;
\t}
"""
    replace_once(path, old, new, "AppInstaller current app accessor")


def patch_canvas(engine: Path) -> None:
    path = engine / "app" / "src" / "main" / "java" / "javax" / "microedition" / "lcdui" / "Canvas.java"

    fields_old = """\tprivate class ViewCallbacks implements View.OnTouchListener, SurfaceHolder.Callback, View.OnKeyListener {
\t\tprivate final View mView;
\t\tOverlayView overlayView;

\t\tpublic ViewCallbacks(View view) {
"""
    fields_new = """\tprivate class ViewCallbacks implements View.OnTouchListener, SurfaceHolder.Callback, View.OnKeyListener {
\t\tprivate final View mView;
\t\tOverlayView overlayView;

\t\tprivate float nomGestureStartX;
\t\tprivate float nomGestureStartY;
\t\tprivate boolean nomGestureActive;

\t\tprivate boolean handleNomGesture(MotionEvent event) {
\t\t\tint action = event.getActionMasked();

\t\t\tif (action == MotionEvent.ACTION_DOWN && event.getPointerCount() == 1) {
\t\t\t\tnomGestureStartX = event.getX();
\t\t\t\tnomGestureStartY = event.getY();
\t\t\t\tnomGestureActive = true;
\t\t\t\treturn true;
\t\t\t}

\t\t\tif (!nomGestureActive) {
\t\t\t\treturn false;
\t\t\t}

\t\t\tif (action == MotionEvent.ACTION_MOVE) {
\t\t\t\treturn true;
\t\t\t}

\t\t\tif (action == MotionEvent.ACTION_POINTER_DOWN) {
\t\t\t\tnomGestureActive = false;
\t\t\t\treturn false;
\t\t\t}

\t\t\tif (action == MotionEvent.ACTION_CANCEL) {
\t\t\t\tnomGestureActive = false;
\t\t\t\treturn true;
\t\t\t}

\t\t\tif (action != MotionEvent.ACTION_UP) {
\t\t\t\treturn true;
\t\t\t}

\t\t\tfloat dx = event.getX() - nomGestureStartX;
\t\t\tfloat dy = event.getY() - nomGestureStartY;
\t\t\tnomGestureActive = false;

\t\t\tfloat density = mView.getResources().getDisplayMetrics().density;
\t\t\tfloat swipeThreshold = 32.0f * density;
\t\t\tint keyCode;

\t\t\tif (Math.hypot(dx, dy) < swipeThreshold) {
\t\t\t\tkeyCode = KEY_NUM5;
\t\t\t} else if (Math.abs(dx) > Math.abs(dy)) {
\t\t\t\tkeyCode = dx < 0 ? KEY_NUM4 : KEY_NUM6;
\t\t\t} else {
\t\t\t\tkeyCode = dy < 0 ? KEY_NUM2 : KEY_NUM8;
\t\t\t}

\t\t\tpostKeyPressed(keyCode);
\t\t\tpostKeyReleased(keyCode);
\t\t\treturn true;
\t\t}

\t\tpublic ViewCallbacks(View view) {
"""
    replace_once(path, fields_old, fields_new, "NOM gesture fields")

    touch_old = """\t\tpublic boolean onTouch(View v, MotionEvent event) {
\t\t\tswitch (event.getActionMasked()) {
"""
    touch_new = """\t\tpublic boolean onTouch(View v, MotionEvent event) {
\t\t\tif (handleNomGesture(event)) {
\t\t\t\treturn true;
\t\t\t}

\t\t\tswitch (event.getActionMasked()) {
"""
    replace_once(path, touch_old, touch_new, "NOM gesture dispatch")


def patch_manifest(engine: Path) -> None:
    path = engine / "app" / "src" / "main" / "AndroidManifest.xml"
    text = path.read_text(encoding="utf-8")

    launcher_filter = """            <intent-filter>
                <action android:name="android.intent.action.MAIN" />

                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
"""
    if launcher_filter in text:
        text = text.replace(launcher_filter, "", 1)

    launcher_activity = """        <activity
            android:name="ru.woesss.j2me.installer.Nom1LauncherActivity"
            android:exported="true"
            android:screenOrientation="portrait"
            android:theme="@style/AppTheme.NoActionBar">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
"""
    main_activity_marker = """        <activity
            android:name=".MainActivity"
"""
    if launcher_activity not in text:
        if main_activity_marker not in text:
            raise RuntimeError("Could not find MainActivity in AndroidManifest.xml")
        text = text.replace(main_activity_marker, launcher_activity + main_activity_marker, 1)

    micro_old = """        <activity
            android:name="javax.microedition.shell.MicroActivity"
            android:exported="false"
            android:theme="@style/AppTheme.NoActionBar"
"""
    micro_new = """        <activity
            android:name="javax.microedition.shell.MicroActivity"
            android:exported="false"
            android:screenOrientation="portrait"
            android:theme="@style/AppTheme.NoActionBar"
"""
    if micro_new not in text:
        if micro_old not in text:
            raise RuntimeError("Could not patch MicroActivity portrait orientation")
        text = text.replace(micro_old, micro_new, 1)

    path.write_text(text, encoding="utf-8")


def copy_overlay(root: Path, engine: Path, jar_path: Path) -> None:
    launcher_src = root / "overlay" / "Nom1LauncherActivity.java"
    launcher_dst = (
        engine
        / "app"
        / "src"
        / "open"
        / "java"
        / "ru"
        / "woesss"
        / "j2me"
        / "installer"
        / "Nom1LauncherActivity.java"
    )
    launcher_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(launcher_src, launcher_dst)

    jar_dst = engine / "app" / "src" / "open" / "assets" / "nom1" / "nom1.jar"
    jar_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(jar_path, jar_dst)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare J2ME Loader as the NOM 1 Android port")
    parser.add_argument("--engine", default="engine", help="J2ME Loader checkout")
    parser.add_argument("--jar", default="game/nom1.jar", help="Local NOM 1 JAR")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    engine = (root / args.engine).resolve() if not Path(args.engine).is_absolute() else Path(args.engine)
    jar_path = (root / args.jar).resolve() if not Path(args.jar).is_absolute() else Path(args.jar)

    manifest = validate_jar(jar_path)

    required = [
        engine / "build.gradle",
        engine / "app" / "build.gradle",
        engine / "app" / "src" / "main" / "AndroidManifest.xml",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise RuntimeError(
            "J2ME Loader checkout is incomplete. Missing:\n- " + "\n- ".join(missing)
        )

    copy_overlay(root, engine, jar_path)
    patch_build_files(engine)
    patch_installer(engine)
    patch_canvas(engine)
    patch_manifest(engine)

    print("Prepared NOM 1 Android port")
    print(f"  MIDlet: {manifest.get('MIDlet-Name')}")
    print(f"  version: {manifest.get('MIDlet-Version')}")
    print(f"  engine: {engine}")
    print("  minSdk: 24")
    print("  controls: tap=5, swipes=2/4/6/8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
