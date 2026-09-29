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
    fields_new = """\tprivate class ViewCallbacks implements View.OnTouchListener, SurfaceHolder.Callback, View.OnKeyListener {
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

\t\t\tint barHeight = Math.round(TypedValue.applyDimension(
\t\t\t\t\tTypedValue.COMPLEX_UNIT_DIP,
\t\t\t\t\t52,
\t\t\t\t\tmView.getResources().getDisplayMetrics()));

\t\t\tnomButtonBar = new LinearLayout(mView.getContext());
\t\t\tnomButtonBar.setOrientation(LinearLayout.HORIZONTAL);
\t\t\tnomButtonBar.setGravity(Gravity.CENTER);

\t\t\tnomLeftButton = new Button(mView.getContext());
\t\t\tnomRightButton = new Button(mView.getContext());
\t\t\tnomLeftButton.setAllCaps(false);
\t\t\tnomRightButton.setAllCaps(false);
\t\t\tnomLeftButton.setTextSize(16);
\t\t\tnomRightButton.setTextSize(16);

\t\t\tnomButtonBar.addView(nomLeftButton,
\t\t\t\t\tnew LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1));
\t\t\tnomButtonBar.addView(nomRightButton,
\t\t\t\t\tnew LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1));

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
    # The launcher artwork is stored as vector path data instead of a binary
    # PNG/JPG. This keeps Git transport text-only and avoids broken image/base64
    # assets while preserving the important NOM lettering and silhouette.
    res_dir = engine / "app" / "src" / "main" / "res"
    drawable_dir = res_dir / "drawable"
    drawable_dir.mkdir(parents=True, exist_ok=True)

    black_path = "M120,168 L124,167 L125,171 L123,172 Z M130,157 L131,158 L130,162 L121,162 L117,166 L117,169 L123,175 L119,178 L119,181 L116,184 L113,184 L110,180 L107,180 L106,182 L104,181 L104,158 L105,157 Z M99,152 L99,201 L104,201 L104,188 L109,183 L115,189 L118,189 L125,181 L127,183 L130,183 L131,185 L134,185 L139,191 L142,191 L144,189 L144,186 L142,188 L139,188 L138,184 L130,176 L131,175 L136,175 L137,173 L142,173 L142,170 L139,170 L138,172 L133,172 L132,171 L132,166 L136,163 L136,158 L137,157 L150,157 L150,152 Z M180,138 L175,138 L175,221 L174,222 L81,222 L81,226 L82,227 L180,227 Z M180,104 L175,104 L175,119 L174,120 L81,120 L81,125 L180,125 Z M81,86 L81,91 L112,91 L112,86 Z M180,70 L175,70 L175,85 L174,86 L137,86 L137,97 L142,97 L142,92 L143,91 L180,91 Z M31,48 L32,51 L39,54 L38,56 L35,56 L34,58 L31,58 L31,61 L36,61 L37,59 L40,59 L41,57 L44,57 L44,52 L41,52 L40,50 L37,50 L36,48 Z M31,42 L31,45 L44,45 L44,42 Z M44,28 L41,28 L41,35 L40,36 L32,36 L31,39 L44,39 Z M99,24 L99,63 L104,63 L104,24 Z M27,18 L23,22 L23,26 L24,27 L24,22 L27,19 L32,19 L35,22 L35,25 L33,26 L30,22 L27,24 L29,27 L31,25 L33,26 L32,30 L27,30 L27,31 L32,31 L34,29 L34,26 L38,25 L38,24 L32,18 Z M2,2 L3,1 L242,1 L243,2 L243,241 L242,242 L3,242 L2,241 Z M0,0 L0,243 L245,243 L245,0 Z"
    white_path = "M98,152 L99,151 L150,151 L152,157 L152,201 L150,203 L106,203 L105,202 L104,203 L99,202 L98,201 Z M173,133 L171,132 L170,133 L164,133 L163,132 L159,133 L77,133 L76,134 L76,220 L80,221 L81,220 L82,221 L173,221 L174,220 L174,139 L172,137 Z M32,132 L38,135 L43,132 L36,133 L33,131 Z M32,122 L32,129 L33,129 L33,122 Z M39,120 L39,122 L42,124 L42,129 L43,129 L43,123 Z M34,103 L32,107 L33,108 L32,109 L33,115 L34,116 L43,116 L41,114 L37,115 L33,113 L34,105 L35,104 L38,105 L43,103 L38,102 Z M43,90 L41,88 L37,90 Z M114,87 L115,86 L116,87 L115,88 Z M32,87 L32,92 L33,93 L32,96 L36,99 L42,98 L41,96 L36,97 L33,95 L33,92 L35,91 Z M43,83 L38,82 L34,83 L32,85 L43,84 Z M42,66 L42,71 L43,71 L43,66 Z M32,66 L32,76 L35,79 L39,78 L40,79 L43,76 L43,73 L39,78 L34,76 L32,74 L33,73 L33,66 Z M76,19 L77,23 L76,24 L76,79 L77,80 L76,83 L77,84 L81,83 L82,85 L113,85 L114,86 L113,90 L115,93 L115,96 L113,99 L76,99 L77,103 L76,104 L76,117 L80,119 L174,119 L174,104 L172,102 L173,100 L171,98 L170,99 L144,99 L135,97 L136,96 L136,85 L137,84 L138,85 L173,85 L174,84 L174,71 L172,68 L172,65 L171,64 L169,65 L167,64 L99,64 L98,63 L98,24 L96,22 L97,21 L96,19 Z"

    vector_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp"
    android:height="108dp"
    android:viewportWidth="246"
    android:viewportHeight="244">

    <path
        android:fillColor="#FFA000"
        android:pathData="M0,0 L246,0 L246,244 L0,244 Z" />

    <!-- Uniformly scale the complete original artwork into the launcher safe
         zone. Nothing is cropped or stretched; only orange margin is added. -->
    <group
        android:pivotX="123"
        android:pivotY="122"
        android:scaleX="0.72"
        android:scaleY="0.72">

        <path
            android:fillColor="#000000"
            android:fillType="evenOdd"
            android:pathData="{{black_path}}" />

        <path
            android:fillColor="#FFFFFF"
            android:fillType="evenOdd"
            android:pathData="{{white_path}}" />
    </group>
</vector>
"""
    (drawable_dir / "nom1_icon.xml").write_text(vector_xml, encoding="utf-8")

    # Remove J2ME Loader's adaptive icon so Samsung/Android cannot substitute
    # the original JL foreground/background artwork.
    adaptive_icon = res_dir / "mipmap-anydpi-v26" / "ic_launcher.xml"
    if adaptive_icon.exists():
        adaptive_icon.unlink()

    for folder in (
        "mipmap-mdpi",
        "mipmap-hdpi",
        "mipmap-xhdpi",
        "mipmap-xxhdpi",
        "mipmap-xxxhdpi",
    ):
        foreground = res_dir / folder / "ic_launcher_foreground.png"
        if foreground.exists():
            foreground.unlink()

    manifest = engine / "app" / "src" / "main" / "AndroidManifest.xml"
    text = manifest.read_text(encoding="utf-8")
    text = re.sub(
        r'android:icon="[^"]+"',
        'android:icon="@drawable/nom1_icon"',
        text,
        count=1,
    )
    text = re.sub(
        r'android:roundIcon="[^"]+"',
        'android:roundIcon="@drawable/nom1_icon"',
        text,
        count=1,
    )
    text = re.sub(
        r'android:label="[^"]+"',
        'android:label="놈1"',
        text,
        count=1,
    )

    if 'android:icon="@drawable/nom1_icon"' not in text:
        raise RuntimeError("Could not set NOM launcher icon in AndroidManifest.xml")
    if 'android:roundIcon="@drawable/nom1_icon"' not in text:
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
