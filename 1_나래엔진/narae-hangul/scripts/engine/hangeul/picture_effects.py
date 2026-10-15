"""Source-image tone effects, independently checked against Hancom exports."""
from PySide6.QtGui import QImage


def image_is_opaque(image):
    """An RGBA container can still have completely opaque source pixels."""
    if not image.hasAlphaChannel():return True
    from PIL import Image
    alpha=image.convertToFormat(QImage.Format.Format_Alpha8)
    if alpha.isNull():return False
    plane=Image.frombytes('L',(alpha.width(),alpha.height()),bytes(alpha.constBits()),
                          'raw','L',alpha.bytesPerLine())
    return plane.getextrema()==(255,255)


# Ordered coverage ranks, checked against all 248 partial-alpha PDF masks.
# The printer shifts the brush on each partial-alpha draw, across page breaks
# and successive exports in a reused native process. Independent PDFs start
# deterministically at zero; the unknown native initial phase is diagnosed.
_PDF_COVERAGE = (
    (0,42,10,34,2,40,8,32), (48,26,58,18,50,24,56,16),
    (12,38,6,46,14,36,4,44), (60,22,54,30,62,20,52,28),
    (3,41,9,33,1,43,11,35), (51,25,57,17,49,27,59,19),
    (15,37,5,45,13,39,7,47), (63,21,53,29,61,23,55,31),
)


def pdf_alpha_mask(alpha, sequence=0):
    """Eight packed rows: one preserves source, zero paints white.

    Hancom's PDF path differs from its PNG alpha compositing. Byte values
    0..3 draw opaque, 252..255 omit the image; only partial masks advance
    the brush sequence. Derived from native PDF objects after REA
    traced the installed drawing/EMF AlphaBlend path, without loading it.
    """
    alpha = max(0,min(255,int(alpha)))
    if alpha < 4:return b'\xff'*8
    if alpha >= 252:return b'\0'*8
    count=(alpha+2)//4
    rows=[sum(1<<x for x,rank in enumerate(row) if rank<count) for row in _PDF_COVERAGE]
    sx=int(sequence)%8;sy=((int(sequence)+1)//8)%8
    return bytes(((rows[(y-sy)%8]<<sx)|(rows[(y-sy)%8]>>(8-sx)))&255 for y in range(8))


def pdf_alpha_tile(alpha, sequence=0):
    """White stencil at 300 dpi; transparent pixels retain the source."""
    from PySide6.QtGui import QColor
    mask=pdf_alpha_mask(alpha,sequence)
    tile=QImage(8,8,QImage.Format.Format_RGBA8888)
    for y,row in enumerate(mask):
        for x in range(8):
            tile.setPixelColor(x,y,QColor(255,255,255,0 if row&(128>>x) else 255))
    return tile


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
