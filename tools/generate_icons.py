from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "icons"
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"


def make_icon(size: int) -> None:
    scale = 4
    canvas = size * scale
    image = Image.new("RGB", (canvas, canvas), "#f3eddf")
    draw = ImageDraw.Draw(image)
    inset = round(canvas * 0.09)
    radius = round(canvas * 0.18)
    draw.rounded_rectangle(
        (inset, inset, canvas - inset, canvas - inset),
        radius=radius,
        fill="#132b54",
    )
    accent = round(canvas * 0.055)
    draw.rounded_rectangle(
        (canvas - inset - accent * 2, inset, canvas - inset, canvas - inset),
        radius=accent,
        fill="#b43a31",
    )
    font = ImageFont.truetype(FONT, round(canvas * 0.52))
    bounds = draw.textbbox((0, 0), "译", font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    x = (canvas - width) / 2 - accent * 0.35
    y = (canvas - height) / 2 - bounds[1]
    draw.text((x, y), "译", font=font, fill="#f8f2e7")
    image.resize((size, size), Image.Resampling.LANCZOS).save(
        OUTPUT / f"icon-{size}.png"
    )


OUTPUT.mkdir(exist_ok=True)
for icon_size in (16, 32, 48, 128):
    make_icon(icon_size)
