#!/usr/bin/env python3
"""
Build SoundBridge.app icon (AppIcon.icns) from a 1024x1024 PNG.
If icon_1024.png exists in the project root, use it. Otherwise create a simple
placeholder icon (bridge-like shape in teal).
"""
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
APP_RESOURCES = os.path.join(PROJECT_ROOT, "SoundBridge.app", "Contents", "Resources")
ICONSET = os.path.join(APP_RESOURCES, "SoundBridge.iconset")
SOURCE_PNG = os.path.join(PROJECT_ROOT, "icon_1024.png")
OUTPUT_ICNS = os.path.join(APP_RESOURCES, "AppIcon.icns")

SIZES = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
]


def create_placeholder_icon(path: str) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Pillow required to create placeholder icon: pip install Pillow", file=sys.stderr)
        sys.exit(1)
    size = 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 120
    # Teal rounded "bridge" arch
    color = (0, 140, 140, 255)
    draw.rounded_rectangle([margin, margin, size - margin, size - margin], radius=140, fill=color, outline=(0, 100, 100, 255), width=24)
    # Inner arch (bridge shape)
    draw.arc([margin + 80, margin + 80, size - margin - 80, size - margin - 80], 0, 180, fill=(255, 255, 255, 200), width=48)
    img.save(path, "PNG")


def main() -> None:
    os.makedirs(APP_RESOURCES, exist_ok=True)
    os.makedirs(ICONSET, exist_ok=True)

    created_placeholder = False
    if not os.path.isfile(SOURCE_PNG):
        print("No icon_1024.png found; creating placeholder icon.")
        create_placeholder_icon(SOURCE_PNG)
        created_placeholder = True

    for px, name in SIZES:
        out = os.path.join(ICONSET, name)
        subprocess.run(["sips", "-z", str(px), str(px), SOURCE_PNG, "--out", out], check=True, capture_output=True)

    subprocess.run(["iconutil", "-c", "icns", ICONSET, "-o", OUTPUT_ICNS], check=True, capture_output=True)

    for _, name in SIZES:
        os.remove(os.path.join(ICONSET, name))
    os.rmdir(ICONSET)
    if created_placeholder and os.path.isfile(SOURCE_PNG):
        try:
            os.remove(SOURCE_PNG)
        except OSError:
            pass
    print("Created", OUTPUT_ICNS)


if __name__ == "__main__":
    main()
