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

    with zipfile.ZipFile(jar_path) as zf:
        native_3d_refs: list[str] = []
        for name in zf.namelist():
            if not name.endswith(".class"):
                continue
            data = zf.read(name)
            if b"javax/microedition/m3g" in data:
                native_3d_refs.append(f"{name}: javax.microedition.m3g")
            if b"com/mascotcapsule/micro3d" in data:
                native_3d_refs.append(f"{name}: com.mascotcapsule.micro3d")

        if native_3d_refs:
            raise RuntimeError(
                "This NOM JAR uses native 3D APIs, but the current Android port "
                "intentionally disables J2ME Loader's M3G/Micro3D native build:\n- "
                + "\n- ".join(native_3d_refs[:10])
            )

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
            resValue 'string', 'app_name', '놈1'
            buildConfigField 'boolean', 'FULL_EMULATOR', 'true'
"""
    replace_once(app_gradle, open_start, open_replacement, "NOM open flavor configuration")

    # J2ME Loader forces every non-midlet debug build to use "JL-Debug",
    # overriding the NOM product flavor name. Replace that debug-only label
    # with the final Android app name.
    debug_name_old = """    applicationVariants.all {
        if (buildType.name == 'debug' && flavorName != 'midlet') {
            resValue 'string', 'app_name', 'JL-Debug'
        }
"""
    debug_name_new = """    applicationVariants.all {
        if (buildType.name == 'debug' && flavorName != 'midlet') {
            resValue 'string', 'app_name', '놈1'
        }
"""
    replace_once(
        app_gradle,
        debug_name_old,
        debug_name_new,
        "NOM debug application label",
    )

    # Upstream J2ME Loader expects a release keystore even while Gradle is only
    # configuring/syncing a debug build. NOM development must work without any
    # private signing material, so only load/apply the release keystore when it
    # actually exists (or when the upstream Bitrise environment supplies one).
    signing_else_old = """            } else {
                Properties keystoreProps = new Properties()
                keystoreProps.load(new FileInputStream(rootProject.file("keystore.properties")))
"""
    signing_else_new = """            } else if (rootProject.file("keystore.properties").exists()) {
                Properties keystoreProps = new Properties()
                keystoreProps.load(new FileInputStream(rootProject.file("keystore.properties")))
"""
    replace_once(
        app_gradle,
        signing_else_old,
        signing_else_new,
        "optional release keystore loading",
    )

    release_old = """        release {
            minifyEnabled true
            shrinkResources true
            signingConfig signingConfigs.release
        }
"""
    release_new = """        release {
            minifyEnabled true
            shrinkResources true
            if (System.getenv()['BITRISE_IO'] || rootProject.file("keystore.properties").exists()) {
                signingConfig signingConfigs.release
            }
        }
"""
    replace_once(
        app_gradle,
        release_old,
        release_new,
        "optional release signing configuration",
    )

    # NOM 1 is a 2D MIDP title and the analyzed JAR contains no references to
    # javax.microedition.m3g or Mascot Capsule Micro3D. Upstream J2ME Loader
    # still configures those native libraries for every build, which adds an
    # unnecessary NDK failure point on Windows. Remove the native build block
    # for this dedicated NOM port.
    app_text = app_gradle.read_text(encoding="utf-8")
    native_block = """    externalNativeBuild {
        ndkBuild {
            path 'src/main/cpp/Android.mk'
        }
    }

"""
    if native_block in app_text:
        app_text = app_text.replace(native_block, "", 1)
        app_gradle.write_text(app_text, encoding="utf-8")
    elif "externalNativeBuild" in app_text:
        raise RuntimeError(
            "J2ME Loader externalNativeBuild block changed; refusing to remove it blindly"
        )


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

    import_old = """import android.widget.LinearLayout;
import android.widget.PopupWindow;
"""
    import_new = """import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.PopupWindow;
"""
    replace_once(path, import_old, import_new, "NOM bottom button imports")

    fields_old = """\tprivate class ViewCallbacks implements View.OnTouchListener, SurfaceHolder.Callback, View.OnKeyListener {
\t\tprivate final View mView;
\t\tOverlayView overlayView;

\t\tpublic ViewCallbacks(View view) {
"""
    fields_new = """\tprivate class ViewCallbacks implements View.OnTouchListener, SurfaceHolder.Callback, View.OnKeyListener, View.OnGenericMotionListener {
\t\tprivate final View mView;
\t\tOverlayView overlayView;

\t\tprivate float nomTouchX;
\t\tprivate float nomTouchY;
\t\tprivate boolean nomTouchActive;
\t\tprivate int nomSoftKey;

\t\tprivate void fireNomKey(int keyCode) {
\t\t\tpostKeyPressed(keyCode);
\t\t\tpostKeyReleased(keyCode);
\t\t}

\t\tprivate int nomGamepadXKey;
\t\tprivate int nomGamepadYKey;

\t\tprivate boolean isNomGamepadEvent(android.view.InputEvent event) {
\t\t\tint source = event.getSource();
\t\t\treturn (source & android.view.InputDevice.SOURCE_GAMEPAD)
\t\t\t\t\t== android.view.InputDevice.SOURCE_GAMEPAD
\t\t\t\t\t|| (source & android.view.InputDevice.SOURCE_JOYSTICK)
\t\t\t\t\t== android.view.InputDevice.SOURCE_JOYSTICK;
\t\t}

\t\tprivate int nomGamepadKey(int androidKeyCode) {
\t\t\tswitch (androidKeyCode) {
\t\t\t\tcase KeyEvent.KEYCODE_DPAD_UP:
\t\t\t\t\treturn KEY_NUM2;
\t\t\t\tcase KeyEvent.KEYCODE_DPAD_LEFT:
\t\t\t\t\treturn KEY_NUM4;
\t\t\t\tcase KeyEvent.KEYCODE_DPAD_RIGHT:
\t\t\t\t\treturn KEY_NUM6;
\t\t\t\tcase KeyEvent.KEYCODE_DPAD_DOWN:
\t\t\t\t\treturn KEY_NUM8;

\t\t\t\tcase KeyEvent.KEYCODE_BUTTON_A:
\t\t\t\tcase KeyEvent.KEYCODE_BUTTON_X:
\t\t\t\tcase KeyEvent.KEYCODE_BUTTON_Y:
\t\t\t\tcase KeyEvent.KEYCODE_BUTTON_R1:
\t\t\t\tcase KeyEvent.KEYCODE_BUTTON_R2:
\t\t\t\tcase KeyEvent.KEYCODE_BUTTON_THUMBL:
\t\t\t\tcase KeyEvent.KEYCODE_BUTTON_THUMBR:
\t\t\t\t\treturn KEY_NUM5;

\t\t\t\tcase KeyEvent.KEYCODE_BUTTON_B:
\t\t\t\tcase KeyEvent.KEYCODE_BUTTON_BACK:
\t\t\t\t\treturn KEY_SOFT_RIGHT;

\t\t\t\tcase KeyEvent.KEYCODE_BUTTON_START:
\t\t\t\tcase KeyEvent.KEYCODE_BUTTON_MODE:
\t\t\t\tcase KeyEvent.KEYCODE_BUTTON_SELECT:
\t\t\t\t\treturn KEY_SOFT_LEFT;
\t\t\t}
\t\t\treturn 0;
\t\t}

\t\tprivate boolean handleNomGamepadKey(int keyCode, KeyEvent event) {
\t\t\tif (!isNomGamepadEvent(event)) {
\t\t\t\treturn false;
\t\t\t}

\t\t\tint midpKey = nomGamepadKey(keyCode);
\t\t\tif (midpKey == 0) {
\t\t\t\treturn false;
\t\t\t}

\t\t\tif (event.getAction() == KeyEvent.ACTION_DOWN) {
\t\t\t\tif (event.getRepeatCount() == 0) {
\t\t\t\t\tpostKeyPressed(midpKey);
\t\t\t\t} else {
\t\t\t\t\tpostKeyRepeated(midpKey);
\t\t\t\t}
\t\t\t\treturn true;
\t\t\t}
\t\t\tif (event.getAction() == KeyEvent.ACTION_UP) {
\t\t\t\tpostKeyReleased(midpKey);
\t\t\t\treturn true;
\t\t\t}
\t\t\treturn false;
\t\t}

\t\tprivate void updateNomGamepadAxis(boolean horizontal, int nextKey) {
\t\t\tint previous = horizontal ? nomGamepadXKey : nomGamepadYKey;
\t\t\tif (previous == nextKey) {
\t\t\t\treturn;
\t\t\t}
\t\t\tif (previous != 0) {
\t\t\t\tpostKeyReleased(previous);
\t\t\t}
\t\t\tif (nextKey != 0) {
\t\t\t\tpostKeyPressed(nextKey);
\t\t\t}
\t\t\tif (horizontal) {
\t\t\t\tnomGamepadXKey = nextKey;
\t\t\t} else {
\t\t\t\tnomGamepadYKey = nextKey;
\t\t\t}
\t\t}

\t\t@Override
\t\tpublic boolean onGenericMotion(View v, MotionEvent event) {
\t\t\tif (!isNomGamepadEvent(event)
\t\t\t\t\t|| event.getAction() != MotionEvent.ACTION_MOVE) {
\t\t\t\treturn false;
\t\t\t}

\t\t\tfloat x = event.getAxisValue(MotionEvent.AXIS_HAT_X);
\t\t\tfloat y = event.getAxisValue(MotionEvent.AXIS_HAT_Y);
\t\t\tif (Math.abs(x) < 0.01f) {
\t\t\t\tx = event.getAxisValue(MotionEvent.AXIS_X);
\t\t\t}
\t\t\tif (Math.abs(y) < 0.01f) {
\t\t\t\ty = event.getAxisValue(MotionEvent.AXIS_Y);
\t\t\t}

\t\t\tfinal float deadZone = 0.45f;
\t\t\tint xKey = x <= -deadZone ? KEY_NUM4 : (x >= deadZone ? KEY_NUM6 : 0);
\t\t\tint yKey = y <= -deadZone ? KEY_NUM2 : (y >= deadZone ? KEY_NUM8 : 0);
\t\t\tupdateNomGamepadAxis(true, xKey);
\t\t\tupdateNomGamepadAxis(false, yKey);
\t\t\treturn true;
\t\t}

\t\tprivate java.lang.reflect.Field findNomField(String name, Class<?> type, boolean wantStatic) {
\t\t\ttry {
\t\t\t\tjava.lang.reflect.Field[] fields = Canvas.this.getClass().getDeclaredFields();
\t\t\t\tfor (java.lang.reflect.Field field : fields) {
\t\t\t\t\tif (!field.getName().equals(name) || field.getType() != type) {
\t\t\t\t\t\tcontinue;
\t\t\t\t\t}
\t\t\t\t\tboolean isStatic = java.lang.reflect.Modifier.isStatic(field.getModifiers());
\t\t\t\t\tif (isStatic != wantStatic) {
\t\t\t\t\t\tcontinue;
\t\t\t\t\t}
\t\t\t\t\tfield.setAccessible(true);
\t\t\t\t\treturn field;
\t\t\t\t}
\t\t\t} catch (Throwable ignored) {
\t\t\t}
\t\t\treturn null;
\t\t}

\t\tprivate int getNomStaticInt(String name, int fallback) {
\t\t\ttry {
\t\t\t\tjava.lang.reflect.Field field = findNomField(name, Integer.TYPE, true);
\t\t\t\treturn field == null ? fallback : field.getInt(null);
\t\t\t} catch (Throwable ignored) {
\t\t\t\treturn fallback;
\t\t\t}
\t\t}

\t\tprivate int getNomStaticByte(String name, int fallback) {
\t\t\ttry {
\t\t\t\tjava.lang.reflect.Field field = findNomField(name, Byte.TYPE, true);
\t\t\t\treturn field == null ? fallback : field.getByte(null);
\t\t\t} catch (Throwable ignored) {
\t\t\t\treturn fallback;
\t\t\t}
\t\t}

\t\tprivate boolean getNomStaticBoolean(String name, boolean fallback) {
\t\t\ttry {
\t\t\t\tjava.lang.reflect.Field field = findNomField(name, Boolean.TYPE, true);
\t\t\t\treturn field == null ? fallback : field.getBoolean(null);
\t\t\t} catch (Throwable ignored) {
\t\t\t\treturn fallback;
\t\t\t}
\t\t}

\t\tprivate String[] getNomStaticStrings(String name) {
\t\t\ttry {
\t\t\t\tjava.lang.reflect.Field field = findNomField(name, String[].class, true);
\t\t\t\treturn field == null ? null : (String[]) field.get(null);
\t\t\t} catch (Throwable ignored) {
\t\t\t\treturn null;
\t\t\t}
\t\t}

\t\tprivate boolean setNomStaticInt(String name, int value) {
\t\t\ttry {
\t\t\t\tjava.lang.reflect.Field field = findNomField(name, Integer.TYPE, true);
\t\t\t\tif (field == null) {
\t\t\t\t\treturn false;
\t\t\t\t}
\t\t\t\tfield.setInt(null, value);
\t\t\t\treturn true;
\t\t\t} catch (Throwable ignored) {
\t\t\t\treturn false;
\t\t\t}
\t\t}

\t\tprivate boolean setNomInstanceBoolean(String name, boolean value) {
\t\t\ttry {
\t\t\t\tjava.lang.reflect.Field field = findNomField(name, Boolean.TYPE, false);
\t\t\t\tif (field == null) {
\t\t\t\t\treturn false;
\t\t\t\t}
\t\t\t\tfield.setBoolean(Canvas.this, value);
\t\t\t\treturn true;
\t\t\t} catch (Throwable ignored) {
\t\t\t\treturn false;
\t\t\t}
\t\t}

\t\tprivate int invokeNomMenuY(String methodName, int index, boolean optionLayout) {
\t\t\ttry {
\t\t\t\tjava.lang.reflect.Method[] methods = Canvas.this.getClass().getDeclaredMethods();
\t\t\t\tfor (java.lang.reflect.Method method : methods) {
\t\t\t\t\tif (!method.getName().equals(methodName) || method.getReturnType() != Integer.TYPE) {
\t\t\t\t\t\tcontinue;
\t\t\t\t\t}
\t\t\t\t\tClass<?>[] params = method.getParameterTypes();
\t\t\t\t\tif (!optionLayout
\t\t\t\t\t\t\t&& params.length == 1
\t\t\t\t\t\t\t&& params[0] == Integer.TYPE) {
\t\t\t\t\t\tmethod.setAccessible(true);
\t\t\t\t\t\treturn (Integer) method.invoke(Canvas.this, index);
\t\t\t\t\t}
\t\t\t\t\tif (optionLayout
\t\t\t\t\t\t\t&& params.length == 2
\t\t\t\t\t\t\t&& params[0] == Integer.TYPE
\t\t\t\t\t\t\t&& params[1] == Boolean.TYPE) {
\t\t\t\t\t\tmethod.setAccessible(true);
\t\t\t\t\t\treturn (Integer) method.invoke(Canvas.this, index, true);
\t\t\t\t\t}
\t\t\t\t}
\t\t\t} catch (Throwable ignored) {
\t\t\t}
\t\t\treturn Integer.MIN_VALUE;
\t\t}

\t\tprivate int findNomTouchedRow(float screenY, int count, boolean optionLayout) {
\t\t\tif (count <= 0 || onHeight <= 0) {
\t\t\t\treturn -1;
\t\t\t}

\t\t\tint virtualY = Math.round(convertPointerY(screenY));
\t\t\tint best = -1;
\t\t\tint bestDistance = Integer.MAX_VALUE;
\t\t\tint[] centers = new int[count];

\t\t\tfor (int i = 0; i < count; i++) {
\t\t\t\tint center = invokeNomMenuY(optionLayout ? "c" : "e", i, optionLayout);
\t\t\t\tif (center == Integer.MIN_VALUE) {
\t\t\t\t\treturn -1;
\t\t\t\t}
\t\t\t\tcenters[i] = center;
\t\t\t\tint distance = Math.abs(virtualY - center);
\t\t\t\tif (distance < bestDistance) {
\t\t\t\t\tbestDistance = distance;
\t\t\t\t\tbest = i;
\t\t\t\t}
\t\t\t}

\t\t\tint tolerance = 14;
\t\t\tif (count > 1) {
\t\t\t\tint nearestGap = Integer.MAX_VALUE;
\t\t\t\tfor (int i = 1; i < count; i++) {
\t\t\t\t\tnearestGap = Math.min(nearestGap, Math.abs(centers[i] - centers[i - 1]));
\t\t\t\t}
\t\t\t\tif (nearestGap != Integer.MAX_VALUE) {
\t\t\t\t\ttolerance = Math.max(tolerance, nearestGap / 2 + 2);
\t\t\t\t}
\t\t\t}

\t\t\treturn bestDistance <= tolerance ? best : -1;
\t\t}

\t\tprivate boolean tryNomDirectMenuTap(float x, float y) {
\t\t\t// This reflection path is intentionally NOM-specific. It reads the
\t\t\t// obfuscated game's own menu state and its own menu Y-coordinate
\t\t\t// calculation, so tapping text selects exactly the item being drawn.
\t\t\tif (!"b".equals(Canvas.this.getClass().getName())) {
\t\t\t\treturn false;
\t\t\t}

\t\t\tint state = getNomStaticByte("c", -1);
\t\t\tint page = getNomStaticInt("J", 0);
\t\t\tint confirm = getNomStaticInt("K", 0);
\t\t\tboolean paused = getNomStaticBoolean("q", false);

\t\t\t// K=1: exit confirmation, K=2: return-to-main-menu confirmation.
\t\t\t// NOM draws YES on the left and NO on the right.
\t\t\tif (confirm != 0) {
\t\t\t\tfloat virtualY = convertPointerY(y);
\t\t\t\tif (virtualY >= height * 0.42f) {
\t\t\t\t\tboolean yes = x < mView.getWidth() / 2.0f;
\t\t\t\t\tif (setNomInstanceBoolean("k", yes)) {
\t\t\t\t\t\t// NOM confirmation dialogs commit YES/NO with soft-left (-6).
\t\t\t\t\t\tfireNomKey(KEY_SOFT_LEFT);
\t\t\t\t\t\treturn true;
\t\t\t\t\t}
\t\t\t\t}
\t\t\t\treturn true;
\t\t\t}

\t\t\tif ((state == 2 && page == 0) || paused) {
\t\t\t\tString[] items = getNomStaticStrings("a");
\t\t\t\tif (items == null || items.length == 0) {
\t\t\t\t\treturn false;
\t\t\t\t}
\t\t\t\tint row = findNomTouchedRow(y, items.length, false);
\t\t\t\tif (row < 0 || !setNomStaticInt("I", row)) {
\t\t\t\t\treturn false;
\t\t\t\t}
\t\t\t\tfireNomKey(KEY_NUM5);
\t\t\t\treturn true;
\t\t\t}

\t\t\t// Options screen: the original game stores the selected option in M
\t\t\t// and computes each row with c(index, true).
\t\t\tif (state == 2 && page == 1) {
\t\t\t\tint topSelection = getNomStaticInt("I", -1);
\t\t\t\tint optionMenuIndex = getNomStaticByte("i", -2);
\t\t\t\tif (topSelection == optionMenuIndex) {
\t\t\t\t\tString[] languages = getNomStaticStrings("g");
\t\t\t\t\tint rowCount = languages != null && languages.length >= 2 ? 5 : 4;
\t\t\t\t\tint row = findNomTouchedRow(y, rowCount, true);
\t\t\t\t\tif (row < 0 || !setNomStaticInt("M", row)) {
\t\t\t\t\t\treturn false;
\t\t\t\t\t}
\t\t\t\t\tfireNomKey(KEY_NUM5);
\t\t\t\t\treturn true;
\t\t\t\t}
\t\t\t}

\t\t\treturn false;
\t\t}

\t\tprivate boolean isNomGameplay() {
\t\t\treturn getNomStaticByte("c", -1) == 3
\t\t\t\t\t&& !getNomStaticBoolean("q", false)
\t\t\t\t\t&& getNomStaticInt("K", 0) == 0;
\t\t}

\t\tprivate int nomGameplayTouchKey(float x, float y) {
\t\t\t// NOM is a one-button game: every tap on the gameplay surface
\t\t\t// is the action/OK key. Pause lives in the native bottom bar.
\t\t\treturn KEY_NUM5;
\t\t}

\t\tprivate boolean handleNomTouch(MotionEvent event) {
\t\t\tint action = event.getActionMasked();

\t\t\tif (action == MotionEvent.ACTION_DOWN && event.getPointerCount() == 1) {
\t\t\t\tnomTouchX = event.getX();
\t\t\t\tnomTouchY = event.getY();
\t\t\t\tnomTouchActive = true;
\t\t\t\tnomSoftKey = 0;

\t\t\t\t// Preserve the original aspect ratio and turn the unused bottom
\t\t\t\t// letterbox into two large soft-key touch targets.
\t\t\t\tif (!isNomGameplay() && nomTouchY > onY + onHeight) {
\t\t\t\t\tnomSoftKey = nomTouchX < mView.getWidth() / 2.0f
\t\t\t\t\t\t\t? KEY_SOFT_LEFT : KEY_SOFT_RIGHT;
\t\t\t\t}
\t\t\t\treturn true;
\t\t\t}

\t\t\tif (action == MotionEvent.ACTION_POINTER_DOWN) {
\t\t\t\tnomTouchActive = false;
\t\t\t\tnomSoftKey = 0;
\t\t\t\treturn true;
\t\t\t}

\t\t\tif (action == MotionEvent.ACTION_CANCEL) {
\t\t\t\tnomTouchActive = false;
\t\t\t\tnomSoftKey = 0;
\t\t\t\treturn true;
\t\t\t}

\t\t\tif (action != MotionEvent.ACTION_UP || !nomTouchActive) {
\t\t\t\treturn true;
\t\t\t}

\t\t\tnomTouchActive = false;

\t\t\tif (nomSoftKey != 0) {
\t\t\t\tint keyCode = nomSoftKey;
\t\t\t\tnomSoftKey = 0;
\t\t\t\tfireNomKey(keyCode);
\t\t\t\treturn true;
\t\t\t}

\t\t\tif (nomTouchY >= onY && nomTouchY <= onY + onHeight
\t\t\t\t\t&& tryNomDirectMenuTap(nomTouchX, nomTouchY)) {
\t\t\t\treturn true;
\t\t\t}

\t\t\tif (isNomGameplay()) {
\t\t\t\tfireNomKey(nomGameplayTouchKey(nomTouchX, nomTouchY));
\t\t\t\treturn true;
\t\t\t}

\t\t\treturn true;
\t\t}

\t\tprivate LinearLayout nomButtonBar;
\t\tprivate Button nomLeftButton;
\t\tprivate Button nomRightButton;

\t\tprivate int nomDp(float value) {
\t\t\treturn Math.round(TypedValue.applyDimension(
\t\t\t\t\tTypedValue.COMPLEX_UNIT_DIP,
\t\t\t\t\tvalue,
\t\t\t\t\tmView.getResources().getDisplayMetrics()));
\t\t}

\t\tprivate android.graphics.drawable.GradientDrawable nomButtonShape(int color) {
\t\t\tandroid.graphics.drawable.GradientDrawable shape =
\t\t\t\t\tnew android.graphics.drawable.GradientDrawable();
\t\t\tshape.setShape(android.graphics.drawable.GradientDrawable.RECTANGLE);
\t\t\tshape.setColor(color);
\t\t\tshape.setStroke(nomDp(2), android.graphics.Color.BLACK);
\t\t\tshape.setCornerRadius(nomDp(3));
\t\t\treturn shape;
\t\t}

\t\tprivate android.graphics.drawable.StateListDrawable nomButtonBackground() {
\t\t\tandroid.graphics.drawable.StateListDrawable states =
\t\t\t\t\tnew android.graphics.drawable.StateListDrawable();
\t\t\tstates.addState(
\t\t\t\t\tnew int[] { android.R.attr.state_pressed },
\t\t\t\t\tnomButtonShape(android.graphics.Color.rgb(214, 132, 0)));
\t\t\tstates.addState(
\t\t\t\t\tnew int[] {},
\t\t\t\t\tnomButtonShape(android.graphics.Color.rgb(255, 166, 0)));
\t\t\treturn states;
\t\t}

\t\tprivate void styleNomButton(Button button) {
\t\t\tbutton.setAllCaps(false);
\t\t\tbutton.setTextColor(android.graphics.Color.BLACK);
\t\t\tbutton.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
\t\t\tbutton.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
\t\t\tbutton.setGravity(Gravity.CENTER);
\t\t\tbutton.setPadding(nomDp(8), 0, nomDp(8), 0);
\t\t\tbutton.setMinHeight(0);
\t\t\tbutton.setMinimumHeight(0);
\t\t\t// Keep controller focus on the game surface. These native buttons
\t\t\t// remain fully touch/clickable but never consume D-pad navigation.
\t\t\tbutton.setFocusable(false);
\t\t\tbutton.setFocusableInTouchMode(false);
\t\t\tbutton.setBackground(nomButtonBackground());
\t\t}

\t\tprivate final Runnable nomButtonUpdater = new Runnable() {
\t\t\t@Override
\t\t\tpublic void run() {
\t\t\t\tif (nomButtonBar == null || nomButtonBar.getParent() == null) {
\t\t\t\t\treturn;
\t\t\t\t}
\t\t\t\tupdateNomButtons();
\t\t\t\tnomButtonBar.postDelayed(this, 120);
\t\t\t}
\t\t};

\t\tprivate void attachNomButtonBar(LinearLayout parent) {
\t\t\tif (!"b".equals(Canvas.this.getClass().getName())) {
\t\t\t\treturn;
\t\t\t}

\t\t\tint barHeight = nomDp(58);

\t\t\tnomButtonBar = new LinearLayout(mView.getContext());
\t\t\tnomButtonBar.setOrientation(LinearLayout.HORIZONTAL);
\t\t\tnomButtonBar.setGravity(Gravity.CENTER);
\t\t\tnomButtonBar.setBackgroundColor(android.graphics.Color.rgb(12, 12, 12));
\t\t\tnomButtonBar.setPadding(nomDp(6), nomDp(6), nomDp(6), nomDp(6));

\t\t\tnomLeftButton = new Button(mView.getContext());
\t\t\tnomRightButton = new Button(mView.getContext());
\t\t\tstyleNomButton(nomLeftButton);
\t\t\tstyleNomButton(nomRightButton);

\t\t\tLinearLayout.LayoutParams leftParams =
\t\t\t\t\tnew LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1);
\t\t\tleftParams.setMargins(0, 0, nomDp(3), 0);
\t\t\tLinearLayout.LayoutParams rightParams =
\t\t\t\t\tnew LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1);
\t\t\trightParams.setMargins(nomDp(3), 0, 0, 0);

\t\t\tnomButtonBar.addView(nomLeftButton, leftParams);
\t\t\tnomButtonBar.addView(nomRightButton, rightParams);

\t\t\tnomLeftButton.setOnClickListener(v -> handleNomBottomButton(true));
\t\t\tnomRightButton.setOnClickListener(v -> handleNomBottomButton(false));

\t\t\tparent.addView(nomButtonBar,
\t\t\t\t\tnew LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, barHeight));

\t\t\tupdateNomButtons();
\t\t\tnomButtonBar.post(nomButtonUpdater);
\t\t}

\t\tprivate void setNomButtonLabels(String left, String right) {
\t\t\tif (nomLeftButton == null || nomRightButton == null) {
\t\t\t\treturn;
\t\t\t}
\t\t\tnomLeftButton.setAlpha(1.0f);
\t\t\tnomRightButton.setAlpha(1.0f);
\t\t\tnomLeftButton.setText(left == null ? "" : left);
\t\t\tnomLeftButton.setVisibility(left == null ? View.GONE : View.VISIBLE);
\t\t\tnomRightButton.setText(right == null ? "" : right);
\t\t\tnomRightButton.setVisibility(right == null ? View.GONE : View.VISIBLE);
\t\t}

\t\tprivate void updateNomButtons() {
\t\t\tint confirm = getNomStaticInt("K", 0);
\t\t\tint state = getNomStaticByte("c", -1);
\t\t\tint page = getNomStaticInt("J", 0);
\t\t\tboolean paused = getNomStaticBoolean("q", false);

\t\t\tif (confirm != 0) {
\t\t\t\tsetNomButtonLabels("예", "아니오");
\t\t\t} else if (state == 3 && !paused) {
\t\t\t\tsetNomButtonLabels("일시정지", "");
\t\t\t\t// The rest of the bottom bar stays visually empty but acts as
\t\t\t\t// a normal gameplay tap target.
\t\t\t\tnomRightButton.setAlpha(0.0f);
\t\t\t} else if (paused) {
\t\t\t\tsetNomButtonLabels("확인", "뒤로");
\t\t\t} else if (state == 2 && page == 0) {
\t\t\t\tsetNomButtonLabels("확인", "종료");
\t\t\t} else if (state == 2 && page == 1) {
\t\t\t\tint selected = getNomStaticInt("I", -1);
\t\t\t\tint optionMenuIndex = getNomStaticByte("i", -2);
\t\t\t\tsetNomButtonLabels(selected == optionMenuIndex ? "변경" : "확인", "뒤로");
\t\t\t} else {
\t\t\t\tsetNomButtonLabels("확인", "뒤로");
\t\t\t}
\t\t}

\t\tprivate void handleNomBottomButton(boolean left) {
\t\t\tint confirm = getNomStaticInt("K", 0);
\t\t\tif (confirm != 0) {
\t\t\t\tif (setNomInstanceBoolean("k", left)) {
\t\t\t\t\t// Execute the selected YES/NO value using NOM's soft-left key.
\t\t\t\t\tfireNomKey(KEY_SOFT_LEFT);
\t\t\t\t}
\t\t\t\treturn;
\t\t\t}

\t\t\tif (isNomGameplay()) {
\t\t\t\tif (left) {
\t\t\t\t\tfireNomKey(KEY_SOFT_LEFT);
\t\t\t\t} else {
\t\t\t\t\tfireNomKey(KEY_NUM5);
\t\t\t\t}
\t\t\t\treturn;
\t\t\t}

\t\t\tfireNomKey(left ? KEY_NUM5 : KEY_SOFT_RIGHT);
\t\t}

\t\tpublic ViewCallbacks(View view) {
"""
    replace_once(path, fields_old, fields_new, "NOM direct-touch controls")

    view_old = """\t\t\tViewCallbacks callback = new ViewCallbacks(innerView);
\t\t\tinnerView.getHolder().addCallback(callback);
\t\t\tinnerView.setOnTouchListener(callback);
\t\t\tinnerView.setOnKeyListener(callback);
\t\t\tinnerView.setOnGenericMotionListener(callback);
\t\t\tinnerView.setFocusableInTouchMode(true);
\t\t\tlayout.addView(innerView);
\t\t\tinnerView.requestFocus();
"""
    view_new = """\t\t\tViewCallbacks callback = new ViewCallbacks(innerView);
\t\t\tinnerView.getHolder().addCallback(callback);
\t\t\tinnerView.setOnTouchListener(callback);
\t\t\tinnerView.setOnKeyListener(callback);
\t\t\tinnerView.setFocusableInTouchMode(true);
\t\t\tinnerView.setLayoutParams(new LinearLayout.LayoutParams(
\t\t\t\t\tViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
\t\t\tlayout.addView(innerView);
\t\t\tcallback.attachNomButtonBar(layout);
\t\t\tinnerView.requestFocus();
"""
    replace_once(path, view_old, view_new, "NOM native bottom button bar")

    touch_old = """\t\tpublic boolean onTouch(View v, MotionEvent event) {
\t\t\tswitch (event.getActionMasked()) {
"""
    touch_new = """\t\tpublic boolean onTouch(View v, MotionEvent event) {
\t\t\tif (handleNomTouch(event)) {
\t\t\t\treturn true;
\t\t\t}

\t\t\tswitch (event.getActionMasked()) {
"""
    replace_once(path, touch_old, touch_new, "NOM direct-touch dispatch")

    key_old = """\t\tpublic boolean onKey(View v, int keyCode, KeyEvent event) {
\t\t\tswitch (event.getAction()) {
"""
    key_new = """\t\tpublic boolean onKey(View v, int keyCode, KeyEvent event) {
\t\t\tif (handleNomGamepadKey(keyCode, event)) {
\t\t\t\treturn true;
\t\t\t}

\t\t\tswitch (event.getAction()) {
"""
    replace_once(path, key_old, key_new, "NOM gamepad button mapping")


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
        / "main"
        / "java"
        / "ru"
        / "woesss"
        / "j2me"
        / "installer"
        / "Nom1LauncherActivity.java"
    )
    launcher_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(launcher_src, launcher_dst)

    jar_dst = engine / "app" / "src" / "main" / "assets" / "nom1" / "nom1.jar"
    jar_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(jar_path, jar_dst)

    # Clean up older generated locations so Android Studio cannot accidentally
    # run a flavor whose manifest points at a launcher class that is absent.
    old_launcher = (
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
    old_jar = engine / "app" / "src" / "open" / "assets" / "nom1" / "nom1.jar"
    if old_launcher.exists():
        old_launcher.unlink()
    if old_jar.exists():
        old_jar.unlink()



def patch_app_icon(root: Path, engine: Path) -> None:
    # This vector is traced directly from the supplied 246x244 NOM artwork.
    # Keeping it as XML avoids binary corruption in Git transport while
    # preserving the original NOM/GAMEVIL silhouette and lettering.
    res_dir = engine / "app" / "src" / "main" / "res"
    legacy_dir = res_dir / "mipmap-anydpi"
    adaptive_dir = res_dir / "mipmap-anydpi-v26"
    drawable_dir = res_dir / "drawable"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    adaptive_dir.mkdir(parents=True, exist_ok=True)
    drawable_dir.mkdir(parents=True, exist_ok=True)

    black_path = "M0,0h123v1h-123z M0,1h2v1h-2z M121,1h2v1h-2z M0,2h2v1h-2z M122,2h1v1h-1z M0,3h2v1h-2z M122,3h1v1h-1z M0,4h1v1h-1z M122,4h1v1h-1z M0,5h1v1h-1z M122,5h1v1h-1z M0,6h1v1h-1z M122,6h1v1h-1z M0,7h1v1h-1z M122,7h1v1h-1z M0,8h1v1h-1z M122,8h1v1h-1z M0,9h1v1h-1z M14,9h2v1h-2z M122,9h1v1h-1z M0,10h1v1h-1z M12,10h2v1h-2z M16,10h2v1h-2z M122,10h1v1h-1z M0,11h1v1h-1z M11,11h2v1h-2z M14,11h2v1h-2z M17,11h2v1h-2z M122,11h1v1h-1z M0,12h1v1h-1z M11,12h1v1h-1z M14,12h2v1h-2z M18,12h1v1h-1z M50,12h2v1h-2z M122,12h1v1h-1z M0,13h1v1h-1z M12,13h1v1h-1z M14,13h4v1h-4z M50,13h3v1h-3z M122,13h1v1h-1z M0,14h1v1h-1z M16,14h1v1h-1z M21,14h1v1h-1z M50,14h3v1h-3z M122,14h1v1h-1z M0,15h1v1h-1z M14,15h2v1h-2z M21,15h1v1h-1z M50,15h3v1h-3z M122,15h1v1h-1z M0,16h1v1h-1z M21,16h1v1h-1z M50,16h3v1h-3z M122,16h1v1h-1z M0,17h1v1h-1z M20,17h2v1h-2z M50,17h3v1h-3z M122,17h1v1h-1z M0,18h1v1h-1z M16,18h7v1h-7z M50,18h3v1h-3z M122,18h1v1h-1z M0,19h1v1h-1z M16,19h6v1h-6z M50,19h3v1h-3z M122,19h1v1h-1z M0,20h1v1h-1z M50,20h3v1h-3z M122,20h1v1h-1z M0,21h1v1h-1z M16,21h6v1h-6z M50,21h3v1h-3z M122,21h1v1h-1z M0,22h1v1h-1z M16,22h6v1h-6z M50,22h3v1h-3z M122,22h1v1h-1z M0,23h1v1h-1z M50,23h3v1h-3z M122,23h1v1h-1z M0,24h1v1h-1z M16,24h3v1h-3z M50,24h3v1h-3z M122,24h1v1h-1z M0,25h1v1h-1z M16,25h4v1h-4z M50,25h3v1h-3z M122,25h1v1h-1z M0,26h1v1h-1z M17,26h5v1h-5z M50,26h3v1h-3z M122,26h1v1h-1z M0,27h1v1h-1z M19,27h4v1h-4z M50,27h3v1h-3z M122,27h1v1h-1z M0,28h1v1h-1z M18,28h4v1h-4z M50,28h3v1h-3z M122,28h1v1h-1z M0,29h1v1h-1z M16,29h5v1h-5z M50,29h3v1h-3z M122,29h1v1h-1z M0,30h1v1h-1z M16,30h2v1h-2z M50,30h3v1h-3z M122,30h1v1h-1z M0,31h1v1h-1z M50,31h3v1h-3z M122,31h1v1h-1z M0,32h1v1h-1z M122,32h1v1h-1z M0,33h1v1h-1z M122,33h1v1h-1z M0,34h1v1h-1z M122,34h1v1h-1z M0,35h1v1h-1z M88,35h2v1h-2z M122,35h1v1h-1z M0,36h1v1h-1z M88,36h3v1h-3z M122,36h1v1h-1z M0,37h1v1h-1z M88,37h3v1h-3z M122,37h1v1h-1z M0,38h1v1h-1z M88,38h3v1h-3z M122,38h1v1h-1z M0,39h1v1h-1z M88,39h3v1h-3z M122,39h1v1h-1z M0,40h1v1h-1z M88,40h3v1h-3z M122,40h1v1h-1z M0,41h1v1h-1z M88,41h2v1h-2z M122,41h1v1h-1z M0,42h1v1h-1z M88,42h3v1h-3z M122,42h1v1h-1z M0,43h1v1h-1z M40,43h16v1h-16z M69,43h22v1h-22z M122,43h1v1h-1z M0,44h1v1h-1z M40,44h16v1h-16z M69,44h22v1h-22z M122,44h1v1h-1z M0,45h1v1h-1z M41,45h15v1h-15z M69,45h21v1h-21z M122,45h1v1h-1z M0,46h1v1h-1z M69,46h3v1h-3z M122,46h1v1h-1z M0,47h1v1h-1z M69,47h2v1h-2z M122,47h1v1h-1z M0,48h1v1h-1z M69,48h3v1h-3z M122,48h1v1h-1z M0,49h1v1h-1z M122,49h1v1h-1z M0,50h1v1h-1z M122,50h1v1h-1z M0,51h1v1h-1z M122,51h1v1h-1z M0,52h1v1h-1z M88,52h2v1h-2z M122,52h1v1h-1z M0,53h1v1h-1z M88,53h3v1h-3z M122,53h1v1h-1z M0,54h1v1h-1z M88,54h3v1h-3z M122,54h1v1h-1z M0,55h1v1h-1z M88,55h3v1h-3z M122,55h1v1h-1z M0,56h1v1h-1z M88,56h3v1h-3z M122,56h1v1h-1z M0,57h1v1h-1z M88,57h3v1h-3z M122,57h1v1h-1z M0,58h1v1h-1z M88,58h2v1h-2z M122,58h1v1h-1z M0,59h1v1h-1z M88,59h2v1h-2z M122,59h1v1h-1z M0,60h1v1h-1z M41,60h49v1h-49z M122,60h1v1h-1z M0,61h1v1h-1z M40,61h51v1h-51z M122,61h1v1h-1z M0,62h1v1h-1z M41,62h49v1h-49z M122,62h1v1h-1z M0,63h1v1h-1z M122,63h1v1h-1z M0,64h1v1h-1z M122,64h1v1h-1z M0,65h1v1h-1z M122,65h1v1h-1z M0,66h1v1h-1z M122,66h1v1h-1z M0,67h1v1h-1z M122,67h1v1h-1z M0,68h1v1h-1z M122,68h1v1h-1z M0,69h1v1h-1z M88,69h2v1h-2z M122,69h1v1h-1z M0,70h1v1h-1z M88,70h3v1h-3z M122,70h1v1h-1z M0,71h1v1h-1z M88,71h2v1h-2z M122,71h1v1h-1z M0,72h1v1h-1z M88,72h2v1h-2z M122,72h1v1h-1z M0,73h1v1h-1z M88,73h2v1h-2z M122,73h1v1h-1z M0,74h1v1h-1z M88,74h2v1h-2z M122,74h1v1h-1z M0,75h1v1h-1z M88,75h2v1h-2z M122,75h1v1h-1z M0,76h1v1h-1z M50,76h25v1h-25z M88,76h2v1h-2z M122,76h1v1h-1z M0,77h1v1h-1z M50,77h25v1h-25z M88,77h2v1h-2z M122,77h1v1h-1z M0,78h1v1h-1z M50,78h25v1h-25z M88,78h2v1h-2z M122,78h1v1h-1z M0,79h1v1h-1z M50,79h3v1h-3z M65,79h4v1h-4z M88,79h2v1h-2z M122,79h1v1h-1z M0,80h1v1h-1z M50,80h3v1h-3z M65,80h3v1h-3z M88,80h2v1h-2z M122,80h1v1h-1z M0,81h1v1h-1z M50,81h3v1h-3z M61,81h7v1h-7z M88,81h2v1h-2z M122,81h1v1h-1z M0,82h1v1h-1z M50,82h3v1h-3z M60,82h7v1h-7z M88,82h2v1h-2z M122,82h1v1h-1z M0,83h1v1h-1z M50,83h3v1h-3z M59,83h8v1h-8z M88,83h2v1h-2z M122,83h1v1h-1z M0,84h1v1h-1z M50,84h3v1h-3z M59,84h2v1h-2z M62,84h4v1h-4z M88,84h2v1h-2z M122,84h1v1h-1z M0,85h1v1h-1z M50,85h3v1h-3z M60,85h1v1h-1z M63,85h4v1h-4z M69,85h2v1h-2z M88,85h2v1h-2z M122,85h1v1h-1z M0,86h1v1h-1z M50,86h3v1h-3z M61,86h10v1h-10z M88,86h2v1h-2z M122,86h1v1h-1z M0,87h1v1h-1z M50,87h3v1h-3z M61,87h8v1h-8z M88,87h2v1h-2z M122,87h1v1h-1z M0,88h1v1h-1z M50,88h2v1h-2z M61,88h5v1h-5z M88,88h2v1h-2z M122,88h1v1h-1z M0,89h1v1h-1z M50,89h2v1h-2z M60,89h6v1h-6z M88,89h2v1h-2z M122,89h1v1h-1z M0,90h1v1h-1z M50,90h2v1h-2z M54,90h1v1h-1z M59,90h8v1h-8z M88,90h2v1h-2z M122,90h1v1h-1z M0,91h1v1h-1z M50,91h6v1h-6z M59,91h3v1h-3z M64,91h4v1h-4z M88,91h2v1h-2z M122,91h1v1h-1z M0,92h1v1h-1z M50,92h4v1h-4z M56,92h5v1h-5z M66,92h3v1h-3z M88,92h2v1h-2z M122,92h1v1h-1z M0,93h1v1h-1z M50,93h4v1h-4z M56,93h4v1h-4z M68,93h2v1h-2z M71,93h1v1h-1z M88,93h2v1h-2z M122,93h1v1h-1z M0,94h1v1h-1z M50,94h3v1h-3z M57,94h2v1h-2z M69,94h3v1h-3z M88,94h2v1h-2z M122,94h1v1h-1z M0,95h1v1h-1z M50,95h2v1h-2z M70,95h2v1h-2z M88,95h2v1h-2z M122,95h1v1h-1z M0,96h1v1h-1z M50,96h3v1h-3z M88,96h2v1h-2z M122,96h1v1h-1z M0,97h1v1h-1z M50,97h3v1h-3z M88,97h2v1h-2z M122,97h1v1h-1z M0,98h1v1h-1z M50,98h3v1h-3z M88,98h2v1h-2z M122,98h1v1h-1z M0,99h1v1h-1z M50,99h3v1h-3z M88,99h2v1h-2z M122,99h1v1h-1z M0,100h1v1h-1z M50,100h2v1h-2z M88,100h2v1h-2z M122,100h1v1h-1z M0,101h1v1h-1z M88,101h2v1h-2z M122,101h1v1h-1z M0,102h1v1h-1z M88,102h2v1h-2z M122,102h1v1h-1z M0,103h1v1h-1z M88,103h2v1h-2z M122,103h1v1h-1z M0,104h1v1h-1z M88,104h2v1h-2z M122,104h1v1h-1z M0,105h1v1h-1z M88,105h2v1h-2z M122,105h1v1h-1z M0,106h1v1h-1z M88,106h2v1h-2z M122,106h1v1h-1z M0,107h1v1h-1z M88,107h2v1h-2z M122,107h1v1h-1z M0,108h1v1h-1z M88,108h3v1h-3z M122,108h1v1h-1z M0,109h1v1h-1z M88,109h3v1h-3z M122,109h1v1h-1z M0,110h1v1h-1z M88,110h3v1h-3z M122,110h1v1h-1z M0,111h1v1h-1z M40,111h51v1h-51z M122,111h1v1h-1z M0,112h1v1h-1z M41,112h50v1h-50z M122,112h1v1h-1z M0,113h1v1h-1z M41,113h49v1h-49z M122,113h1v1h-1z M0,114h1v1h-1z M122,114h1v1h-1z M0,115h1v1h-1z M122,115h1v1h-1z M0,116h1v1h-1z M122,116h1v1h-1z M0,117h1v1h-1z M122,117h1v1h-1z M0,118h2v1h-2z M122,118h1v1h-1z M0,119h1v1h-1z M122,119h1v1h-1z M0,120h2v1h-2z M121,120h2v1h-2z M0,121h123v1h-123z"
    white_path = "M38,9h11v1h-11z M37,10h12v1h-12z M38,11h12v1h-12z M37,12h12v1h-12z M37,13h12v1h-12z M37,14h12v1h-12z M38,15h11v1h-11z M38,16h11v1h-11z M38,17h11v1h-11z M38,18h11v1h-11z M38,19h11v1h-11z M38,20h11v1h-11z M38,21h11v1h-11z M38,22h11v1h-11z M38,23h11v1h-11z M38,24h11v1h-11z M38,25h11v1h-11z M38,26h11v1h-11z M38,27h11v1h-11z M38,28h11v1h-11z M38,29h11v1h-11z M38,30h11v1h-11z M38,31h11v1h-11z M16,32h1v1h-1z M21,32h1v1h-1z M38,32h49v1h-49z M16,33h6v1h-6z M38,33h50v1h-50z M16,34h2v1h-2z M19,34h3v1h-3z M38,34h50v1h-50z M16,35h7v1h-7z M38,35h49v1h-49z M16,36h2v1h-2z M21,36h2v1h-2z M38,36h49v1h-49z M16,37h2v1h-2z M20,37h2v1h-2z M38,37h49v1h-49z M16,38h6v1h-6z M38,38h49v1h-49z M16,39h5v1h-5z M38,39h49v1h-49z M37,40h50v1h-50z M17,41h5v1h-5z M37,41h50v1h-50z M16,42h6v1h-6z M38,42h49v1h-49z M16,43h2v1h-2z M57,43h11v1h-11z M15,44h7v1h-7z M57,44h11v1h-11z M15,45h7v1h-7z M57,45h11v1h-11z M16,46h2v1h-2z M56,46h12v1h-12z M16,47h2v1h-2z M57,47h11v1h-11z M16,48h6v1h-6z M56,48h12v1h-12z M16,49h6v1h-6z M38,49h49v1h-49z M18,50h1v1h-1z M37,50h51v1h-51z M17,51h5v1h-5z M38,51h50v1h-50z M16,52h6v1h-6z M37,52h50v1h-50z M16,53h4v1h-4z M37,53h50v1h-50z M16,54h2v1h-2z M19,54h1v1h-1z M37,54h50v1h-50z M16,55h1v1h-1z M38,55h49v1h-49z M15,56h3v1h-3z M37,56h50v1h-50z M16,57h6v1h-6z M37,57h50v1h-50z M17,58h6v1h-6z M37,58h50v1h-50z M38,59h49v1h-49z M16,60h1v1h-1z M19,60h3v1h-3z M16,61h6v1h-6z M16,62h2v1h-2z M20,62h2v1h-2z M16,63h2v1h-2z M20,63h3v1h-3z M16,64h1v1h-1z M20,64h3v1h-3z M16,65h2v1h-2z M21,65h1v1h-1z M16,66h6v1h-6z M38,66h49v1h-49z M16,67h6v1h-6z M38,67h50v1h-50z M37,68h50v1h-50z M37,69h50v1h-50z M37,70h50v1h-50z M37,71h50v1h-50z M37,72h50v1h-50z M37,73h50v1h-50z M37,74h50v1h-50z M37,75h50v1h-50z M38,76h11v1h-11z M76,76h11v1h-11z M38,77h11v1h-11z M76,77h11v1h-11z M38,78h11v1h-11z M76,78h11v1h-11z M38,79h11v1h-11z M76,79h11v1h-11z M38,80h11v1h-11z M76,80h11v1h-11z M38,81h11v1h-11z M75,81h12v1h-12z M38,82h11v1h-11z M75,82h12v1h-12z M38,83h11v1h-11z M75,83h12v1h-12z M38,84h11v1h-11z M75,84h12v1h-12z M38,85h11v1h-11z M76,85h11v1h-11z M38,86h11v1h-11z M76,86h11v1h-11z M38,87h11v1h-11z M75,87h12v1h-12z M38,88h11v1h-11z M75,88h12v1h-12z M38,89h11v1h-11z M75,89h12v1h-12z M38,90h11v1h-11z M75,90h12v1h-12z M38,91h11v1h-11z M75,91h12v1h-12z M38,92h11v1h-11z M75,92h12v1h-12z M38,93h11v1h-11z M75,93h12v1h-12z M38,94h11v1h-11z M76,94h11v1h-11z M38,95h11v1h-11z M76,95h11v1h-11z M38,96h11v1h-11z M76,96h11v1h-11z M38,97h11v1h-11z M76,97h11v1h-11z M38,98h11v1h-11z M76,98h11v1h-11z M38,99h11v1h-11z M76,99h11v1h-11z M38,100h11v1h-11z M75,100h12v1h-12z M38,101h49v1h-49z M38,102h49v1h-49z M38,103h49v1h-49z M38,104h49v1h-49z M37,105h50v1h-50z M37,106h50v1h-50z M37,107h50v1h-50z M37,108h50v1h-50z M38,109h49v1h-49z M38,110h49v1h-49z"

    def vector_xml(scale: float) -> str:
        return f"""<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp"
    android:height="108dp"
    android:viewportWidth="123"
    android:viewportHeight="122">
    <path
        android:fillColor="#FEA000"
        android:pathData="M0,0h123v122h-123z" />
    <group
        android:pivotX="61.5"
        android:pivotY="61"
        android:scaleX="{scale}"
        android:scaleY="{scale}">
        <path
            android:fillColor="#000000"
            android:pathData="{black_path}" />
        <path
            android:fillColor="#FFFFFF"
            android:pathData="{white_path}" />
    </group>
</vector>
"""

    # Pre-Android 8: use a complete vector icon with generous orange padding.
    legacy_xml = vector_xml(0.76)
    (legacy_dir / "ic_launcher.xml").write_text(legacy_xml, encoding="utf-8")
    (legacy_dir / "ic_launcher_round.xml").write_text(legacy_xml, encoding="utf-8")

    # Android 8+: adaptive icon. The artwork foreground is intentionally
    # smaller than the mandatory safe zone so Samsung launchers cannot crop
    # the large "놈" character or vertical GAMEVIL mark.
    foreground_xml = vector_xml(0.62)
    (drawable_dir / "nom1_icon_foreground.xml").write_text(
        foreground_xml, encoding="utf-8"
    )
    (drawable_dir / "nom1_icon_background.xml").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android"
    android:shape="rectangle">
    <solid android:color="#FEA000" />
</shape>
""",
        encoding="utf-8",
    )

    adaptive_xml = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@drawable/nom1_icon_background" />
    <foreground android:drawable="@drawable/nom1_icon_foreground" />
</adaptive-icon>
"""
    (adaptive_dir / "ic_launcher.xml").write_text(
        adaptive_xml, encoding="utf-8"
    )
    (adaptive_dir / "ic_launcher_round.xml").write_text(
        adaptive_xml, encoding="utf-8"
    )

    # Remove every upstream J2ME Loader bitmap launcher asset so resource
    # resolution can never fall back to the Android/JL icon.
    for folder in (
        "mipmap-mdpi",
        "mipmap-hdpi",
        "mipmap-xhdpi",
        "mipmap-xxhdpi",
        "mipmap-xxxhdpi",
    ):
        target_dir = res_dir / folder
        for name in ("ic_launcher.png", "ic_launcher_foreground.png"):
            target = target_dir / name
            if target.exists():
                target.unlink()

    old_vector = drawable_dir / "nom1_icon.xml"
    if old_vector.exists():
        old_vector.unlink()

    manifest = engine / "app" / "src" / "main" / "AndroidManifest.xml"
    text = manifest.read_text(encoding="utf-8")
    text = re.sub(
        r'android:icon="[^"]+"',
        'android:icon="@mipmap/ic_launcher"',
        text,
        count=1,
    )
    text = re.sub(
        r'android:roundIcon="[^"]+"',
        'android:roundIcon="@mipmap/ic_launcher_round"',
        text,
        count=1,
    )
    text = re.sub(
        r'android:label="[^"]+"',
        'android:label="놈1"',
        text,
        count=1,
    )

    if 'android:icon="@mipmap/ic_launcher"' not in text:
        raise RuntimeError("Could not set NOM launcher icon")
    if 'android:roundIcon="@mipmap/ic_launcher_round"' not in text:
        raise RuntimeError("Could not set NOM round launcher icon")
    if 'android:label="놈1"' not in text:
        raise RuntimeError("Could not set NOM Android app label")

    manifest.write_text(text, encoding="utf-8")


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
    patch_app_icon(root, engine)

    print("Prepared NOM 1 Android port")
    print(f"  MIDlet: {manifest.get('MIDlet-Name')}")
    print(f"  version: {manifest.get('MIDlet-Version')}")
    print(f"  engine: {engine}")
    print("  minSdk: 24")
    print("  native 3D: disabled (NOM JAR does not use M3G/Micro3D)")
    print("  controls: direct menus, tap-anywhere gameplay action, dynamic bottom buttons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
