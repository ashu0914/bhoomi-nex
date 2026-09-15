"""Generates a synthetic land-record image so you can test the /api/upload
pipeline end to end without a real scanned document.

Usage:
    cd backend
    python scripts/make_sample_document.py
    # writes sample_record.png next to this script
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LINES = [
    "LAND RECORD - JAMABANDI",
    "Owner Name: Suresh Chandra",
    "Khasra No: 56/3",
    "Khata No: 18",
    "Village: Bhondsi",
    "Tehsil: Sohna",
    "District: Gurugram",
    "Area: 1.80 Acres",
    "Mutation No: 340",
]


def main():
    img = Image.new("RGB", (1000, 620), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except OSError:
        font = ImageFont.load_default()

    y = 30
    for line in LINES:
        draw.text((30, y), line, fill="black", font=font)
        y += 55

    out = Path(__file__).parent / "sample_record.png"
    img.save(out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
