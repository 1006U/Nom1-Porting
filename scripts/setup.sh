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
fi

python3 tools/prepare_engine.py --engine "$ENGINE" --jar "$JAR"

echo
echo "NOM 1 port workspace is ready."
echo "Build with:"
echo "  cd $ENGINE && ./gradlew :app:assembleOpenDebug"
