"""Source-image tone effects, independently checked against Hancom exports."""
from PySide6.QtGui import QImage


def apply_tone(image, effect):
    """Keep source alpha; use native floor luminance and its >128 threshold.

    Brightness, contrast and picture-level transparency remain separately
    diagnosed by SourceLayout. This function only handles these two tone modes.
    """
    if image.isNull() or effect not in ("GRAY_SCALE", "BLACK_WHITE"):
        return image
    from PIL import Image, ImageMath
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    source = Image.frombytes("RGBA", (rgba.width(), rgba.height()), bytes(rgba.constBits()),
                             "raw", "RGBA", rgba.bytesPerLine())
    red, green, blue, alpha = source.split()
    gray = ImageMath.lambda_eval(
        lambda channels: (channels["r"] * 299 + channels["g"] * 587 + channels["b"] * 114) / 1000,
        r=red, g=green, b=blue).convert("L")
    if effect == "BLACK_WHITE":
        gray = gray.point([0] * 129 + [255] * 127)
    data = Image.merge("RGBA", (gray, gray, gray, alpha)).tobytes()
    return QImage(data, rgba.width(), rgba.height(), rgba.width() * 4,
                  QImage.Format.Format_RGBA8888).copy()
