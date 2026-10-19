"""Generate the synthetic Product Lock fixtures (MIT, no third-party imagery).

Run: uv run python fixtures/product-lock/make_fixtures.py
Each fixture is an RGBA PNG of an opaque product with small label text and an
anti-aliased silhouette, so the alpha path, core erosion and label fidelity are
all exercised.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent
SS = 4  # supersampling for anti-aliased edges


def font(size):
    return ImageFont.load_default(size=size * SS)


def finish(img: Image.Image, name: str):
    img = img.resize((img.width // SS, img.height // SS), Image.LANCZOS)
    img.save(OUT / f"{name}.png", optimize=True)


def can():
    w, h = 300 * SS, 560 * SS
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((10 * SS, 20 * SS, 290 * SS, 540 * SS), radius=40 * SS, fill=(196, 30, 58, 255))
    d.rectangle((10 * SS, 200 * SS, 290 * SS, 330 * SS), fill=(250, 246, 236, 255))
    d.text((40 * SS, 220 * SS), "LOCK COLA", fill=(20, 20, 20, 255), font=font(40))
    d.text((40 * SS, 280 * SS), "355 ml  ·  zero sugar  ·  lot 0419", fill=(60, 60, 60, 255), font=font(14))
    finish(img, "can_lock_cola")


def bottle():
    w, h = 240 * SS, 640 * SS
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((95 * SS, 10 * SS, 145 * SS, 120 * SS), fill=(30, 90, 60, 255))
    d.rounded_rectangle((20 * SS, 110 * SS, 220 * SS, 630 * SS), radius=50 * SS, fill=(24, 121, 78, 255))
    d.rectangle((20 * SS, 300 * SS, 220 * SS, 450 * SS), fill=(240, 236, 220, 255))
    d.text((34 * SS, 320 * SS), "MOSS", fill=(24, 60, 40, 255), font=font(48))
    d.text((34 * SS, 400 * SS), "cold brew tea 500ml", fill=(40, 40, 40, 255), font=font(14))
    finish(img, "bottle_moss")


def box():
    w, h = 420 * SS, 420 * SS
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(40 * SS, 120 * SS), (210 * SS, 40 * SS), (380 * SS, 120 * SS), (380 * SS, 380 * SS),
               (210 * SS, 410 * SS), (40 * SS, 380 * SS)], fill=(242, 200, 80, 255))
    d.line([(210 * SS, 160 * SS), (210 * SS, 410 * SS)], fill=(200, 150, 40, 255), width=3 * SS)
    d.text((70 * SS, 220 * SS), "SUNNY", fill=(30, 30, 30, 255), font=font(36))
    d.text((230 * SS, 230 * SS), "net wt 200g", fill=(30, 30, 30, 255), font=font(14))
    finish(img, "box_sunny")


def jar():
    w, h = 360 * SS, 400 * SS
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((60 * SS, 10 * SS, 300 * SS, 80 * SS), radius=12 * SS, fill=(40, 40, 46, 255))
    d.rounded_rectangle((20 * SS, 70 * SS, 340 * SS, 390 * SS), radius=60 * SS, fill=(250, 250, 248, 255))
    d.text((60 * SS, 170 * SS), "Cloud Cream", fill=(90, 70, 160, 255), font=font(36))
    d.text((60 * SS, 230 * SS), "50 mL / 1.7 fl oz  SPF 30", fill=(80, 80, 80, 255), font=font(14))
    finish(img, "jar_cloud_cream")


def tube():
    w, h = 200 * SS, 620 * SS
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(20 * SS, 20 * SS), (180 * SS, 20 * SS), (150 * SS, 540 * SS), (50 * SS, 540 * SS)],
              fill=(52, 120, 196, 255))
    d.rectangle((70 * SS, 540 * SS, 130 * SS, 610 * SS), fill=(230, 230, 230, 255))
    d.text((40 * SS, 120 * SS), "AQUA", fill=(255, 255, 255, 255), font=font(36))
    d.text((40 * SS, 180 * SS), "gel 120ml", fill=(255, 255, 255, 255), font=font(14))
    finish(img, "tube_aqua")


if __name__ == "__main__":
    for make in (can, bottle, box, jar, tube):
        make()
    print("fixtures written to", OUT)
