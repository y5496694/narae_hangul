"""Experimental HFT outlines -> user-local OpenType CFF; no Hancom DLL use.

Caller supplies installed HFT paths and an explicit cache path. Font binaries,
including this cache, are not application distribution assets. Normal Hanyang
KS outlines are supported; bitmap/unknown glyphs are reported as unavailable.
Qt font registration is process-local. Vertical OpenType metrics are an
explicit approximation, so use document baseline/line metrics for placement.
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import math
try:
    from .hft import HftFont, HftError
except ImportError:
    from hft import HftFont, HftError


def normalized_operations(glyph, quantization="native-pdf"):
    """Return upward-y coordinates in 1000/em, preserving cubic segments."""
    if glyph.kind != "outline":
        raise HftError("CFF conversion requires a supported outline glyph")
    shift = glyph.provenance.get("baseline_shift_em", 0) * glyph.units_per_em
    result = []
    for op, *values in glyph.operations:
        points = [(value - (shift if index % 2 else 0)) * 1000 / glyph.units_per_em
                  for index, value in enumerate(values)]
        if quantization == "native-pdf":
            points = [math.trunc(value) for value in points]
        elif quantization != "unrounded":
            raise ValueError("quantization must be native-pdf or unrounded")
        result.append((op, *points))
    return tuple(result)


def convert_outline_fonts(hft_paths, cache_path, *, characters=None,
                          family=None, quantization="native-pdf", space_advance_em=None,
                          max_total_operations=2_000_000):
    """Create local CFF from verified HFT outline mappings.

    Default tries all modern Hangul and reports unavailable codepoints. ASCII
    and whitespace are opt-in, since script-specific font and space metrics
    must be chosen by the document engine. Unsupported glyphs are never
    synthesized or taken from native PDF output.
    """
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.teePen import TeePen
    fonts = [HftFont(path) for path in hft_paths]
    if not fonts:
        raise ValueError("At least one installed HFT font path is required")
    if space_advance_em is not None and not 0<space_advance_em<=2:
        raise ValueError("space_advance_em must be an explicitly verified value in (0,2]")
    if not isinstance(max_total_operations,int) or not 1<=max_total_operations<=2_000_000:
        raise ValueError("max_total_operations must be an integer in [1,2000000]")
    cache = Path(cache_path)
    if any(cache.resolve() == font.path.resolve() for font in fonts):
        raise ValueError("Font cache must not overwrite a source HFT")
    chars = sorted(set(characters if characters is not None else
                       (chr(value) for value in range(0xac00, 0xd7a4))), key=ord)
    if len(chars)>65534:raise ValueError("Too many characters for an OpenType cache")
    digest = hashlib.sha256(("|".join(font.sha256 for font in fonts) +
                            quantization + repr(space_advance_em) + "".join(chars)).encode()).hexdigest()
    family = family or ("IndependentHFT-" + digest[:12])
    postscript_name = "IndependentHFT-" + digest[:16]
    metrics = {".notdef": (1000, 0)}
    empty = T2CharStringPen(1000, None, roundTolerance=0).getCharString()
    charstrings = {".notdef": empty}
    cmap = {}
    unavailable = []
    zero_advances = []
    provenance = []
    total_operations = 0
    for char in chars:
        if len(char) != 1:
            raise ValueError("Characters must contain Unicode scalar strings")
        selected = None
        for font in fonts:
            try:
                glyph = font.glyph(char)
                if glyph.kind == "outline":
                    selected = font, glyph
                    break
            except (HftError, UnicodeError):
                pass
        if selected is None:
            unavailable.append(ord(char))
            continue
        font, glyph = selected
        total_operations += len(glyph.operations)
        if total_operations>max_total_operations:
            raise HftError("HFT outline conversion operation budget exceeded")
        ops = normalized_operations(glyph, quantization)
        advance = round(font.advance(char, 1000))
        if char==" " and space_advance_em is not None:
            advance=round(space_advance_em*1000)
        if not 0<=advance<=65535:
            raise HftError("Invalid OpenType horizontal advance")
        if advance <= 0:
            # Preserve observed font width; do not invent a whitespace metric.
            zero_advances.append(ord(char))
        charstring_pen = T2CharStringPen(advance, None, roundTolerance=0)
        bounds_pen = BoundsPen(None)
        pen = TeePen([charstring_pen, bounds_pen])
        open_contour = False
        for op, *values in ops:
            if op == "M":
                if open_contour:
                    pen.closePath()
                pen.moveTo(tuple(values)); open_contour = True
            elif op == "L":
                pen.lineTo(tuple(values))
            elif op == "C":
                pen.curveTo(tuple(values[:2]), tuple(values[2:4]), tuple(values[4:6]))
            elif op == "Z" and open_contour:
                pen.closePath(); open_contour = False
            elif op != "Z":
                raise HftError("Unsupported CFF path operator")
        if open_contour:
            pen.closePath()
        charstring = charstring_pen.getCharString()
        glyph_name = "uni%04X" % ord(char)
        bounds = bounds_pen.bounds
        lsb = math.floor(bounds[0]) if bounds else 0
        metrics[glyph_name] = advance, lsb
        charstrings[glyph_name] = charstring
        cmap[ord(char)] = glyph_name
        provenance.append({"codepoint": ord(char), "source_sha256": font.sha256,
                           "glyph_index": glyph.provenance["glyph_index"]})
    if not cmap:
        raise HftError("No supported outline glyphs for requested characters")
    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder(list(charstrings))
    fb.setupCharacterMap(cmap)
    fb.setupCFF(postscript_name, {"FullName": family, "FamilyName": family,
                "Weight": "Regular"}, charstrings, {})
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=850, descent=-150)
    fb.setupNameTable({"familyName": family, "styleName": "Regular",
                      "uniqueFontIdentifier": digest, "fullName": family,
                      "psName": postscript_name, "version": "Version 0.1"})
    fb.setupOS2(sTypoAscender=850, sTypoDescender=-150, sTypoLineGap=0,
                usWinAscent=1000, usWinDescent=200)
    fb.setupPost()
    cache.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(cache))
    metadata = {"family": family, "cache_path": str(cache.resolve()),
                "cache_sha256": hashlib.sha256(cache.read_bytes()).hexdigest(),
                "quantization": quantization, "units_per_em": 1000,
                "space_advance_em_override": space_advance_em,
                "total_path_operations": total_operations,
                "max_total_operations": max_total_operations,
                "glyph_count": len(cmap), "unavailable_codepoints": unavailable,
                "zero_advance_codepoints": zero_advances,
                "source_fonts": [{"path": str(f.path), "sha256": f.sha256,
                                  "header_family": f.family} for f in fonts],
                "limitations": ["Normal outlines only; native hinting and style synthesis are not implemented",
                    "Ascent/descent are provisional; use document baseline/line metrics",
                    "Native PDF quantization is verified for sampled outline fixtures, not all screen pixel output",
                    "Missing glyphs and bitmap-only fonts need an explicit separate renderer"],
                "glyph_provenance": provenance}
    cache.with_suffix(cache.suffix + ".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf8")
    return metadata


def register_qt_cache(cache_path):
    """Call after creating QGuiApplication; returns process-local font id/name."""
    from PySide6.QtGui import QFontDatabase
    font_id = QFontDatabase.addApplicationFont(str(cache_path))
    if font_id < 0:
        raise HftError("Qt could not register generated OpenType cache")
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        QFontDatabase.removeApplicationFont(font_id)
        raise HftError("Qt registered cache without an available family")
    return font_id, tuple(families)
