"""Editable layout metadata. All dimensions use points (1/72 inch).

The model retains values that QTextDocument cannot lay out, such as floating
anchors and exact row heights. It is not a claim of identical pagination.
"""
from dataclasses import dataclass, field, asdict

PAGE_PROPERTY = 0x100001
TABLE_PROPERTY = 0x100002
CELL_PROPERTY = 0x100003
PICTURE_PROPERTY = 0x100004
FONT_PROPERTY = 0x100005  # original font kind and per-script declarations


@dataclass
class PageLayout:
    width_pt: float = 595.28
    height_pt: float = 841.86
    left_pt: float = 56.69
    right_pt: float = 56.69
    top_pt: float = 56.69
    bottom_pt: float = 56.69
    header_pt: float = 0
    footer_pt: float = 0
    gutter_pt: float = 0
    landscape: bool = False
    gutter_type: str = "LEFT_ONLY"

    @property
    def display_size(self):
        if self.landscape and self.width_pt < self.height_pt:
            return self.height_pt, self.width_pt
        return self.width_pt, self.height_pt


@dataclass(frozen=True)
class Border:
    style: str = "SOLID"
    width_pt: float = 0.283465
    color: str = "#000000"


@dataclass(frozen=True)
class BorderFill:
    borders: tuple = field(default_factory=lambda: (Border(),) * 4)
    background: str = ""


@dataclass
class ObjectPosition:
    inline: bool = True
    affect_line_spacing: bool = True
    flow_with_text: bool = True
    allow_overlap: bool = False
    hold_anchor: bool = False
    vert_rel: str = "PARA"
    horz_rel: str = "PARA"
    vert_align: str = "TOP"
    horz_align: str = "LEFT"
    x_pt: float = 0
    y_pt: float = 0
    wrap: str = "TOP_AND_BOTTOM"
    text_flow: str = "BOTH_SIDES"
    out_margin: tuple = (0, 0, 0, 0)
    z_order: int = 0


@dataclass
class PictureGeometry:
    position: ObjectPosition = field(default_factory=ObjectPosition)
    original_width_pt: float = 0
    original_height_pt: float = 0
    crop: tuple = (0, 0, 0, 0)  # left, top, right, bottom in original coordinates
    rect: tuple = ()  # four (x,y) corners in original coordinates
    rotation: float = 0
    flip_h: bool = False
    flip_v: bool = False
    brightness: int = 0
    contrast: int = 0
    effect: str = "REAL_PIC"
    alpha: int = 0
    in_margin: tuple = (0, 0, 0, 0)
    transforms: dict = field(default_factory=dict)
    image_width_pt: float = 0  # bitmap-coordinate extent, independently of display size
    image_height_pt: float = 0
    border_opacity: int = 0  # HWP PICTURE border byte, distinct from bitmap alpha
    current_width_pt: float | None = None  # transformed shape size, separate from requested sz
    current_height_pt: float | None = None
    offset_x_pt: float = 0  # shape component offset (not anchor pos)
    offset_y_pt: float = 0
    rotation_center_x_pt: float | None = None
    rotation_center_y_pt: float | None = None
    rotate_image: bool = True
    native_flags: int = 0


@dataclass
class DocumentModel:
    blocks: list = field(default_factory=list)
    page: PageLayout = field(default_factory=PageLayout)
    section_pages: list = field(default_factory=list)
    header_footers: list = field(default_factory=list)


def borders_from_data(data):
    return tuple(Border(**b) if isinstance(b, dict) else b for b in data)


def position_from_data(data):
    if isinstance(data, ObjectPosition):
        return data
    data = dict(data or {})
    if "out_margin" in data:
        data["out_margin"] = tuple(data["out_margin"])
    return ObjectPosition(**data)


def picture_from_data(data):
    if isinstance(data, PictureGeometry):
        return data
    data = dict(data or {})
    data["position"] = position_from_data(data.get("position"))
    for key in ("crop", "rect", "in_margin"):
        if key in data:
            data[key] = tuple(tuple(v) if isinstance(v, list) else v for v in data[key])
    return PictureGeometry(**data)


def as_data(value):
    return asdict(value)
