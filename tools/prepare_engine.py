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
\t\t\tboolean paused = getNomStaticBoolean("q", false);

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

\t\tprivate int nomGameplayTouchKey(float x, float y) {
\t\t\tfloat vx = convertPointerX(x);
\t\t\tfloat vy = convertPointerY(y);
\t\t\tfloat nx = vx / Math.max(1.0f, width);
\t\t\tfloat ny = vy / Math.max(1.0f, height);

\t\t\t// Center is the action/OK area. Everywhere else acts as a
\t\t\t// direction pad, chosen by the dominant axis from screen center.
\t\t\tif (nx >= 0.34f && nx <= 0.66f && ny >= 0.34f && ny <= 0.66f) {
\t\t\t\treturn KEY_NUM5;
\t\t\t}

\t\t\tfloat dx = nx - 0.5f;
\t\t\tfloat dy = ny - 0.5f;
\t\t\tif (Math.abs(dx) > Math.abs(dy)) {
\t\t\t\treturn dx < 0 ? KEY_NUM4 : KEY_NUM6;
\t\t\t}
\t\t\treturn dy < 0 ? KEY_NUM2 : KEY_NUM8;
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
\t\t\t\tif (nomTouchY > onY + onHeight) {
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

\t\t\tif (nomTouchY >= onY && nomTouchY <= onY + onHeight) {
\t\t\t\tif (tryNomDirectMenuTap(nomTouchX, nomTouchY)) {
\t\t\t\t\treturn true;
\t\t\t\t}
\t\t\t\tfireNomKey(nomGameplayTouchKey(nomTouchX, nomTouchY));
\t\t\t\treturn true;
\t\t\t}

\t\t\treturn true;
\t\t}

\t\tpublic ViewCallbacks(View view) {
"""
    replace_once(path, fields_old, fields_new, "NOM direct-touch controls")

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
    icon_src = root / "branding" / "app_icon.png"
    if not icon_src.is_file():
        return

    icon_dst = (
        engine
        / "app"
        / "src"
        / "main"
        / "res"
        / "drawable-nodpi"
        / "nom1_icon.png"
    )
    icon_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_src, icon_dst)

    manifest = engine / "app" / "src" / "main" / "AndroidManifest.xml"
    text = manifest.read_text(encoding="utf-8")
    text = re.sub(
        r'android:icon="@mipmap/ic_launcher"',
        'android:icon="@drawable/nom1_icon"',
        text,
        count=1,
    )
    text = re.sub(
        r'android:roundIcon="@mipmap/ic_launcher"',
        'android:roundIcon="@drawable/nom1_icon"',
        text,
        count=1,
    )
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
    print("  controls: direct menu taps, gameplay touch zones, bottom-left=-6, bottom-right=-7")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
