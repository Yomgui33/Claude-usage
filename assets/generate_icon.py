"""
Generates assets/icon.ico from scratch using Pillow.
Run once before building the .exe:
    python assets/generate_icon.py
"""
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow required: pip install Pillow")
    sys.exit(1)

SIZES = [16, 24, 32, 48, 64, 128, 256]


def make_frame(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Outer circle – Claude orange/teal gradient-ish, use a dark teal
    margin = max(1, size // 10)
    box = [margin, margin, size - margin, size - margin]
    draw.ellipse(box, fill=(34, 139, 142, 255))

    # Inner white ring
    inner_margin = size // 4
    inner_box = [inner_margin, inner_margin, size - inner_margin, size - inner_margin]
    draw.ellipse(inner_box, fill=(255, 255, 255, 255))

    # "C" letter in the centre
    if size >= 32:
        try:
            font = ImageFont.truetype("arialbd.ttf", size // 2)
        except (IOError, OSError):
            font = ImageFont.load_default()
        letter = "C"
        bbox = draw.textbbox((0, 0), letter, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (size - tw) // 2
        ty = (size - th) // 2
        draw.text((tx, ty), letter, fill=(34, 139, 142, 255), font=font)

    return img


def main():
    out_dir = os.path.dirname(os.path.abspath(__file__))
    frames = [make_frame(s) for s in SIZES]
    ico_path = os.path.join(out_dir, "icon.ico")
    # Save as multi-size .ico
    frames[0].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=frames[1:],
    )
    print(f"Icon saved to: {ico_path}")


if __name__ == "__main__":
    main()
