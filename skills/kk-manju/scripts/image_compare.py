#!/usr/bin/env python3
"""Build observation-only image comparisons without altering source files."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:
    raise SystemExit("Requires Pillow. Run: uv run --no-project --with pillow python image_compare.py ...")


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def choose_font(path, size):
    if path:
        return ImageFont.truetype(str(Path(path).expanduser()), size)
    for candidate in (
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def fit_label(draw, label, font, width):
    text = label
    while text and draw.textbbox((0, 0), text, font=font)[2] > width:
        text = text[:-1]
    if text != label:
        while text and draw.textbbox((0, 0), text + "…", font=font)[2] > width:
            text = text[:-1]
        text += "…"
    return text


def build(args):
    if len(args.image) < 2:
        raise ValueError("provide at least two --image LABEL PATH pairs")
    if not 160 <= args.width <= 1600:
        raise ValueError("--width must be between 160 and 1600")
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        raise ValueError("output directory already exists; choose a new revision")
    font = choose_font(args.font, max(16, round(args.width * 0.041)))
    records, thumbnails = [], []
    # Hash and decode the same byte snapshot, before creating any output.
    for label, source in args.image:
        path = Path(source).expanduser().resolve(strict=True)
        raw = path.read_bytes()
        with Image.open(io.BytesIO(raw)) as original:
            original.load()  # Reject truncated/corrupt downloads.
            encoded_size = list(original.size)
            format_name = original.format
            displayed = ImageOps.exif_transpose(original)
            if "A" in displayed.getbands() or "transparency" in displayed.info:
                rgba = displayed.convert("RGBA")
                image = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                image.alpha_composite(rgba)
                image = image.convert("RGB")
            else:
                image = displayed.convert("RGB")
        height = max(1, round(image.height * args.width / image.width))
        thumbnail = image.resize((args.width, height), Image.Resampling.LANCZOS)
        thumbnails.append(thumbnail)
        records.append({
            "label": label, "source": str(path),
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "source_bytes": len(raw), "source_format": format_name,
            "encoded_dimensions": encoded_size,
            "display_dimensions": list(image.size),
            "preview_dimensions": list(thumbnail.size),
        })
    columns = min(args.columns, len(thumbnails))
    header, gap = max(50, round(args.width * 0.11)), 12
    rows = [thumbnails[i:i + columns] for i in range(0, len(thumbnails), columns)]
    row_heights = [max(im.height for im in row) + header for row in rows]
    canvas = Image.new("RGB", (columns * args.width + (columns - 1) * gap,
                              sum(row_heights) + (len(rows) - 1) * gap), "#111820")
    draw = ImageDraw.Draw(canvas)
    y = 0
    for row_index, row in enumerate(rows):
        for column, thumbnail in enumerate(row):
            index = row_index * columns + column
            x = column * (args.width + gap)
            label = fit_label(draw, records[index]["label"], font, args.width - 24)
            draw.text((x + 12, y + 12), label, font=font, fill="white")
            canvas.paste(thumbnail, (x, y + header))
        y += row_heights[row_index] + gap
    output.mkdir(parents=True)
    for index, thumbnail in enumerate(thumbnails):
        name = f"preview-{index + 1:02}.jpg"
        thumbnail.save(output / name, quality=95, subsampling=0)
        records[index]["preview"] = name
    canvas.save(output / "comparison.jpg", quality=95, subsampling=0)
    manifest = {
        "complete": True,
        "purpose": "Observation at equal display width; no retouching or adoption decision",
        "note": "Equal width is not equal subject size or equal source resolution. Inspect scene, lighting, compression and framing before judging.",
        "comparison": "comparison.jpg", "images": records,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"comparison": str(output / "comparison.jpg"),
            "manifest": str(output / "manifest.json"), "images": len(records)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", action="append", nargs=2, metavar=("LABEL", "PATH"), required=True)
    parser.add_argument("--output", required=True, help="new directory; existing directories are refused")
    parser.add_argument("--width", type=positive_int, default=600, help="each preview width, 160..1600")
    parser.add_argument("--columns", type=positive_int, default=3)
    parser.add_argument("--font", help="optional TrueType/OpenType font with required glyphs")
    args = parser.parse_args()
    try:
        result = build(args)
    except (ValueError, OSError, Image.DecompressionBombError) as error:
        parser.exit(2, f"image_compare: {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
