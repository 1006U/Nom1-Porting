#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path

from generate_korean_fonts import generate_fonts

STATIC_FILES = ("lang.txt", "text__ko.txt")
FONT_FILES = (
    "sfont.png", "sfont.bin",
    "mfont.png", "mfont.bin",
    "lfont.png", "lfont.bin",
)
PATCH_FILES = STATIC_FILES + FONT_FILES


def patch_jar(source: Path, output: Path, patch_dir: Path, font: str | None = None) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"Source JAR not found: {source}")

    for name in STATIC_FILES:
        if not (patch_dir / name).is_file():
            raise FileNotFoundError(f"Missing Korean patch file: {patch_dir / name}")

    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="nom1-ko-") as tmp_name:
        tmp = Path(tmp_name)
        fonts = tmp / "fonts"
        generate_fonts(source, patch_dir / "text__ko.txt", fonts, font)

        patch_bytes = {
            "lang.txt": (patch_dir / "lang.txt").read_bytes(),
            "text__ko.txt": (patch_dir / "text__ko.txt").read_bytes(),
        }
        for name in FONT_FILES:
            patch_bytes[name] = (fonts / name).read_bytes()

        temp_output = tmp / "nom1-ko.jar"
        with zipfile.ZipFile(source, "r") as zin, zipfile.ZipFile(
            temp_output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as zout:
            replacements = set(PATCH_FILES)
            for info in zin.infolist():
                if info.filename in replacements:
                    continue
                data = zin.read(info.filename)
                new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                new_info.compress_type = zipfile.ZIP_DEFLATED
                new_info.external_attr = info.external_attr
                new_info.comment = info.comment
                new_info.extra = info.extra
                zout.writestr(new_info, data)

            for name in PATCH_FILES:
                zout.writestr(name, patch_bytes[name])

        shutil.copy2(temp_output, output)

    with zipfile.ZipFile(output, "r") as zf:
        names = set(zf.namelist())
        for name in PATCH_FILES:
            if name not in names:
                raise RuntimeError(f"Patched JAR is missing {name}")
        lang = zf.read("lang.txt").decode("utf-8")
        if "ko,한국어" not in lang:
            raise RuntimeError("Korean language entry was not written")
        ko = zf.read("text__ko.txt").decode("utf-8-sig")
        # Git on Windows commonly checks text files out with CRLF line endings.
        # Normalize all newline variants before validating resource sections so
        # a correct Korean patch does not fail only because of core.autocrlf.
        ko_normalized = ko.replace("\r\n", "\n").replace("\r", "\n")
        if "[menu]" not in ko_normalized or "새 게임" not in ko_normalized:
            raise RuntimeError("Korean translation validation failed")
        if "[softkey]\n확인\n뒤로\n일시정지" not in ko_normalized:
            raise RuntimeError("Korean softkey labels are missing")

    print(f"Korean NOM 1 JAR ready: {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the NOM 1 Korean language/font patch")
    parser.add_argument("--input", default="game/nom1.jar", help="Original NOM 1 JAR")
    parser.add_argument("--output", default="game/generated/nom1-ko.jar", help="Patched JAR")
    parser.add_argument("--patch-dir", default="patch/ko", help="Korean text resource directory")
    parser.add_argument("--font", default=None, help="Optional Korean TTF/TTC path")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    source = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    output = Path(args.output) if Path(args.output).is_absolute() else root / args.output
    patch_dir = Path(args.patch_dir) if Path(args.patch_dir).is_absolute() else root / args.patch_dir
    patch_jar(source.resolve(), output.resolve(), patch_dir.resolve(), args.font)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
