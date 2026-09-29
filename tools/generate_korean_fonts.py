#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import os
import struct
import zipfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:
    raise SystemExit(
        "Pillow is required for the Korean font patch. Install it with: "
        "python -m pip install Pillow"
    ) from exc

FONT_SPECS = {
    "sfont": dict(size=9, yoff=-7, fill=2, outline=1, stroke=1),
    "mfont": dict(size=10, yoff=-8, fill=1, outline=None, stroke=0),
    "lfont": dict(size=14, yoff=-13, fill=1, outline=None, stroke=0),
}


def find_korean_font(explicit: str | None = None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env_font = os.environ.get("NOM_KOREAN_FONT")
    if env_font:
        candidates.append(Path(env_font))

    candidates.extend(
        Path(p)
        for p in (
            r"C:\Windows\Fonts\malgun.ttf",
            r"C:\Windows\Fonts\malgunsl.ttf",
            r"C:\Windows\Fonts\gulim.ttc",
            "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
            "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        )
    )
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "No Korean system font was found. On Windows, Malgun Gothic is normally "
        "available at C:\\Windows\\Fonts\\malgun.ttf. You can also set "
        "NOM_KOREAN_FONT to a Korean TTF/TTC path."
    )


def read_font_bin(data: bytes):
    f = io.BytesIO(data)
    _total = struct.unpack(">i", f.read(4))[0]
    utf_len = struct.unpack(">H", f.read(2))[0]
    chars = f.read(utf_len).decode("utf-8")
    count = struct.unpack(">h", f.read(2))[0]
    glyph_arrays = []
    for _ in range(count):
        length = struct.unpack(">b", f.read(1))[0]
        if length == -1:
            glyph_arrays.append(None)
        else:
            glyph_arrays.append(
                [struct.unpack(">h", f.read(2))[0] for _ in range(length)]
            )
    rect_count = struct.unpack(">h", f.read(2))[0]
    rects = [
        [struct.unpack(">h", f.read(2))[0] for _ in range(4)]
        for _ in range(rect_count)
    ]
    width_count = struct.unpack(">h", f.read(2))[0]
    widths = [struct.unpack(">b", f.read(1))[0] for _ in range(width_count)]
    tail = [struct.unpack(">b", f.read(1))[0] for _ in range(4)]
    if f.tell() != len(data):
        raise RuntimeError("Unexpected data at end of NOM bitmap font")
    return chars, glyph_arrays, rects, widths, tail


def write_java_utf(text: str) -> bytes:
    encoded = text.encode("utf-8")
    if len(encoded) > 65535:
        raise ValueError("Font character table is too long")
    return struct.pack(">H", len(encoded)) + encoded


def write_font_bin(chars, glyph_arrays, rects, widths, tail) -> bytes:
    body = bytearray(write_java_utf(chars))
    body += struct.pack(">h", len(glyph_arrays))
    for values in glyph_arrays:
        if values is None:
            body += struct.pack(">b", -1)
        else:
            body += struct.pack(">b", len(values))
            for value in values:
                body += struct.pack(">h", value)
    body += struct.pack(">h", len(rects))
    for rect in rects:
        for value in rect:
            body += struct.pack(">h", value)
    body += struct.pack(">h", len(widths))
    for value in widths:
        body += struct.pack(">b", value)
    for value in tail:
        body += struct.pack(">b", value)
    return struct.pack(">i", len(body) + 4) + body


def required_characters(korean_text: str) -> list[str]:
    result = []
    seen = set()
    for ch in "한국어" + korean_text:
        if ch in "\r\n\t " or ch in "[]|":
            continue
        if ch not in seen:
            seen.add(ch)
            result.append(ch)
    return result


def extend_font(stem: str, png_bytes: bytes, bin_bytes: bytes, chars_to_add, font_path: Path):
    spec = FONT_SPECS[stem]
    chars, glyph_arrays, rects, widths, tail = read_font_bin(bin_bytes)
    additions = [ch for ch in chars_to_add if ch not in chars]
    font = ImageFont.truetype(str(font_path), spec["size"])

    cell_w = max(10, spec["size"] + 2)
    cell_h = max(12, spec["size"] + 3)
    cols = 16
    rows = (len(additions) + cols - 1) // cols

    source = Image.open(io.BytesIO(png_bytes)).copy()
    new_width = max(source.width, cols * cell_w)
    new_height = source.height + rows * cell_h
    atlas = Image.new("P", (new_width, new_height), 0)
    atlas.putpalette(source.getpalette())
    atlas.info["transparency"] = 0
    atlas.paste(source, (0, 0))

    for index, ch in enumerate(additions):
        col, row = index % cols, index // cols
        cell_x, cell_y = col * cell_w, source.height + row * cell_h
        bbox_probe = Image.new("L", (cell_w, cell_h), 0)
        probe_draw = ImageDraw.Draw(bbox_probe)
        bbox = probe_draw.textbbox((0, 0), ch, font=font, stroke_width=0)
        glyph_w, glyph_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = max(0, (cell_w - glyph_w) // 2 - bbox[0])
        y = max(0, (cell_h - glyph_h) // 2 - bbox[1])

        if spec["outline"] is not None:
            outline_mask = Image.new("L", (cell_w, cell_h), 0)
            ImageDraw.Draw(outline_mask).text(
                (x, y), ch, font=font, fill=255,
                stroke_width=spec["stroke"], stroke_fill=255,
            )
            fill_mask = Image.new("L", (cell_w, cell_h), 0)
            ImageDraw.Draw(fill_mask).text((x, y), ch, font=font, fill=255)
            op, fp, pixels = outline_mask.load(), fill_mask.load(), atlas.load()
            for yy in range(cell_h):
                for xx in range(cell_w):
                    if op[xx, yy] >= 96:
                        pixels[cell_x + xx, cell_y + yy] = spec["outline"]
                    if fp[xx, yy] >= 96:
                        pixels[cell_x + xx, cell_y + yy] = spec["fill"]
        else:
            mask = Image.new("L", (cell_w, cell_h), 0)
            ImageDraw.Draw(mask).text((x, y), ch, font=font, fill=255)
            mp, pixels = mask.load(), atlas.load()
            for yy in range(cell_h):
                for xx in range(cell_w):
                    if mp[xx, yy] >= 96:
                        pixels[cell_x + xx, cell_y + yy] = spec["fill"]

        visible_x, visible_y = [], []
        for yy in range(cell_h):
            for xx in range(cell_w):
                if atlas.getpixel((cell_x + xx, cell_y + yy)) != 0:
                    visible_x.append(xx)
                    visible_y.append(yy)
        if visible_x:
            rx, ry = cell_x + min(visible_x), cell_y + min(visible_y)
            rw = max(visible_x) - min(visible_x) + 1
            rh = max(visible_y) - min(visible_y) + 1
        else:
            rx, ry, rw, rh = cell_x, cell_y, max(1, int(font.getlength(ch))), 1

        rect_index = len(rects)
        rects.append([rx, ry, rw, rh])
        glyph_arrays.append([rect_index, 0, spec["yoff"]])
        widths.append(min(127, max(rw, int(round(font.getlength(ch))))))
        chars += ch

    png_out = io.BytesIO()
    atlas.save(png_out, format="PNG", optimize=True, transparency=0)
    return png_out.getvalue(), write_font_bin(chars, glyph_arrays, rects, widths, tail)


def generate_fonts(source_jar: Path, korean_text_file: Path, output_dir: Path, font: str | None = None) -> None:
    korean_text = korean_text_file.read_text(encoding="utf-8-sig")
    chars_to_add = required_characters(korean_text)
    font_path = find_korean_font(font)
    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(source_jar, "r") as zf:
        for stem in FONT_SPECS:
            png_out, bin_out = extend_font(
                stem,
                zf.read(f"{stem}.png"),
                zf.read(f"{stem}.bin"),
                chars_to_add,
                font_path,
            )
            (output_dir / f"{stem}.png").write_bytes(png_out)
            (output_dir / f"{stem}.bin").write_bytes(bin_out)

    print(f"Korean bitmap fonts generated with: {font_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate NOM 1 Korean bitmap fonts")
    parser.add_argument("--jar", default="game/nom1.jar")
    parser.add_argument("--text", default="patch/ko/text__ko.txt")
    parser.add_argument("--output", default="game/generated/ko-fonts")
    parser.add_argument("--font", default=None)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    generate_fonts(root / args.jar, root / args.text, root / args.output, args.font)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
