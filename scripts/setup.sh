#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

JAR="${1:-game/nom1.jar}"
ENGINE="${2:-engine}"
UPSTREAM="https://github.com/nikita36078/J2ME-Loader.git"
TAG="1.8.2"

if [[ ! -f "$JAR" ]]; then
  echo "NOM JAR not found: $JAR" >&2
  echo "Place your legally obtained game at game/nom1.jar." >&2
  exit 1
fi

if [[ ! -d "$ENGINE/.git" ]]; then
  git clone --depth 1 --branch "$TAG" "$UPSTREAM" "$ENGINE"
else
  echo "Refreshing upstream files patched by NOM..."
  git -C "$ENGINE" checkout -- \
    build.gradle \
    app/build.gradle \
    app/src/main/AndroidManifest.xml \
    app/src/main/java/ru/woesss/j2me/installer/AppInstaller.java \
    app/src/main/java/javax/microedition/lcdui/Canvas.java
fi

if ! python3 -c "import PIL" >/dev/null 2>&1; then
  echo "Installing Pillow for Korean bitmap font generation..."
  python3 -m pip install --user Pillow
fi

PATCHED_JAR="game/generated/nom1-ko.jar"
echo "Creating Korean NOM 1 JAR..."
python3 tools/patch_korean.py --input "$JAR" --output "$PATCHED_JAR"

python3 tools/prepare_engine.py --engine "$ENGINE" --jar "$PATCHED_JAR"

echo
echo "NOM 1 port workspace is ready."
echo "Build with:"
echo "  cd $ENGINE && ./gradlew :app:assembleOpenDebug"
