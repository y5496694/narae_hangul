"""Source-image tone effects, independently checked against Hancom exports."""
from PySide6.QtGui import QImage


def channel_table(brightness=0, contrast=0, black_white=False):
    """Native integer adjustment, including the -100 contrast special case.

    Recovered through REA from HncBaseDraw's channel transform and checked
    against Hancom PrintToImage PNG output; no native code is loaded here.
    """
    brightness = max(-100, min(100, int(brightness)))
    contrast = max(-100, min(100, int(contrast)))
    coefficient = 100 + contrast if contrast < 0 else 10000 // (100 - contrast) if contrast < 100 else 0
    offset = (coefficient * 128 + 12700) * brightness
    result = []
    for value in range(256):
        if contrast == 100:
            adjusted, offset = (25500 if value > 128 else 0), 25500 * brightness
        elif contrast == -100:
            adjusted, offset = 128, 25500 * brightness
        else:
            adjusted = (value - 128) * coefficient + 12800
        numerator = adjusted * 100 + offset
        # C/C++ integer division truncates towards zero (Python // does not).
        value = numerator // 10000 if numerator >= 0 else -((-numerator) // 10000)
        if black_white:
            value = 255 if value > 128 else 0
        result.append(max(0, min(255, value)))
    return result


def apply_tone(image, effect, *, brightness=0, contrast=0, alpha=0):
    """Gray first, integer adjustments next, BW last; subtract picture alpha.

    Picture alpha is a byte, distinct from the bitmap's own alpha. Hancom's
    PDF printer can dither transparency; these pixels follow its PNG path.
    """
    if image.isNull() or not (brightness or contrast or alpha or effect in ("GRAY_SCALE", "BLACK_WHITE")):
        return image
    from PIL import Image, ImageMath
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    source = Image.frombytes("RGBA", (rgba.width(), rgba.height()), bytes(rgba.constBits()),
                             "raw", "RGBA", rgba.bytesPerLine())
    red, green, blue, source_alpha = source.split()
    if effect in ("GRAY_SCALE", "BLACK_WHITE"):
        gray = ImageMath.lambda_eval(
            lambda channels: (channels["r"] * 299 + channels["g"] * 587 + channels["b"] * 114) / 1000,
            r=red, g=green, b=blue).convert("L")
        red = green = blue = gray
    if brightness or contrast or effect == "BLACK_WHITE":
        table = channel_table(brightness, contrast, effect == "BLACK_WHITE")
        red, green, blue = (channel.point(table) for channel in (red, green, blue))
    if alpha:
        amount = max(0, min(255, int(alpha)))
        source_alpha = source_alpha.point([max(0, value - amount) for value in range(256)])
    data = Image.merge("RGBA", (red, green, blue, source_alpha)).tobytes()
    return QImage(data, rgba.width(), rgba.height(), rgba.width() * 4,
                  QImage.Format.Format_RGBA8888).copy()
