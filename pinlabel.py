"""Pin-label strip renderer for Brother PT-P750W (128-pin head, 180 dpi).

Output is a 1-bit PNG with square pixels at OUT_DPI (360). Cell positions are
computed from float pitch and rounded per cell, so a 2.54 mm pitch is exactly
36 px/cell and never drifts.

Printable dots across the tape come from Brother's raster command reference for the
PT-E550W/P750W/P710BT (cv_pte550wp750wp710bt_eng_raster_102.pdf, section 2.3 "Print Area"),
as cited by the `ptouch` library. The calibration label is still the real check.
"""
from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

OUT_DPI = 360  # square pixels: 2 px per 180-dpi dot across the tape, 1 px per 360-dpi dot along it
MIN_READABLE_PX = 14  # ~1.0 mm em; below this text gets hard to read on tape

# tape mm -> printable dots across the tape at 180 dpi (unverified, see module docstring)
TAPE_DOTS = {3.5: 24, 6: 32, 9: 50, 12: 70, 18: 112, 24: 128}

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
# Chosen by a bake-off at the printer's real grid (text runs across the tape at 180 dpi, so a
# 6-7 dot cap height and ~5 dot wide letters): narrow, heavy, tall-x-height faces win because the
# text *length* is the limit, not the cell width.
FONTS = {
    "Arial Bold": ("arialbd.ttf", None),
    "Franklin Gothic Medium Cond": ("FRAMDCN.TTF", None),
    "Bahnschrift Bold Condensed": ("bahnschrift.ttf", b"Bold Condensed"),
    "Bahnschrift Extra-Condensed": ("bahnschrift.ttf", [700, 75]),  # [weight, width]
    "Arial Narrow Bold": ("ARIALNB.TTF", None),
    "Tahoma Bold": ("tahomabd.ttf", None),
    "Trebuchet Bold": ("trebucbd.ttf", None),
    "Segoe UI Bold": ("segoeuib.ttf", None),
    "Consolas Bold": ("consolab.ttf", None),
}
DEFAULT_FONT = "Arial Bold"


# common abbreviations used when a label has to fit in fewer characters
SHORT = {"RESET": "RST", "VBAT": "BAT", "MISO": "MI", "MOSI": "MO", "EXT_VCC": "EXT", "NFC1": "NF1", "NFC2": "NF2",
         "SCL1": "SC1", "SDA1": "SD1", "TXD0": "TX0", "RXD0": "RX0", "TXD": "TX", "RXD": "RX", "VBUS": "VBU",
         "AREF": "REF", "DAC1": "DA1", "DAC2": "DA2", "ADC1": "AD1", "ADC2": "AD2"}


def shorten_label(text: str, n: int) -> str:
    """Fit a label into n characters: drop GPIO/IO prefixes, trailing +/-, known abbreviations, then truncate."""
    t = text.strip()
    if n <= 0 or len(t) <= n:
        return t
    u = t.upper()
    for pre in ("GPIO", "IO"):
        if u.startswith(pre) and len(t) > len(pre):
            t = t[len(pre):]
            break
    if len(t) <= n:
        return t
    t = t.rstrip("+-")
    if len(t) <= n:
        return t
    abbr = SHORT.get(t.upper())
    if abbr and len(abbr) <= n:
        return abbr
    return t[:n]


POWER_WORDS = ("VCC", "VDD", "VIN", "VBUS", "3V3", "3.3V", "5V", "12V", "VBAT", "VSYS", "V+")
GND_WORDS = ("GND", "VSS", "AGND", "DGND", "0V", "V-")


def mm_to_px(mm: float) -> float:
    return mm * OUT_DPI / 25.4


def tape_height_px(tape_mm: float) -> int:
    # printable dots are 180 dpi across the tape
    return round(TAPE_DOTS[tape_mm] * OUT_DPI / 180)


@functools.lru_cache(maxsize=256)
def font_for(name: str, px: int) -> ImageFont.FreeTypeFont:
    file, var = FONTS.get(name) or FONTS[DEFAULT_FONT]
    f = ImageFont.truetype(os.path.join(FONT_DIR, file), max(px, 1), layout_engine=ImageFont.Layout.BASIC)
    if var is not None:
        if isinstance(var, list):
            f.set_variation_by_axes(var)
        else:
            f.set_variation_by_name(var)
    return f


def _snap(tile: Image.Image, x_div: int, y_div: int, dark_text: bool) -> Image.Image:
    """Average a text tile down to the printer's dot grid, threshold, and blow it back up.

    Editor resamples our 360-dpi PNG to the printer's dots anyway; doing it here (with a bias
    toward solid strokes: a dot prints if >=40% of it is ink) gives crisp, predictable dots.
    """
    w, h = tile.size
    small = tile.resize((max(w // x_div, 1), max(h // y_div, 1)), Image.BOX)
    thr = 153 if dark_text else 102  # dark text: dot black if avg <= 60% white; light text: white if avg >= 40%
    small = small.point(lambda v: 255 if v > thr else 0)
    return small.resize((w, h), Image.NEAREST)


@dataclass
class Pin:
    number: str = ""
    label: str = ""
    desc: str = ""  # secondary line (function / alt)
    style: str = "auto"  # auto | normal | inverted | boxed


@dataclass
class Options:
    tape_mm: float = 12
    pitch_mm: float = 2.54
    font: str = DEFAULT_FONT
    lines: tuple = ("number", "label")  # subset of number,label,desc in draw order
    text_dir: str = "up"  # up | down | horizontal
    pin1_side: str = "left"  # left | right
    board_side: str = "top"  # top | bottom
    divider: str = "ticks"  # none | ticks | lines
    divider_mm: float = 0.3  # line thickness; >= 0.28 mm survives Editor resampling to 180 dpi
    mark_pin1: bool = True
    max_chars: int = 0  # 0 = off; else shorten printed labels to this many characters
    snap: bool = False  # snap text to printer dots (blockier preview; opt-in)
    print_mode: str = "standard"  # standard (180x180) | high (180x360): along-tape axis snaps to 180 or stays 360
    shrink_each: bool = True
    uniform: bool = True  # one text size for the whole strip (smallest that fits the longest label)
    font_cap_mm: float = 2.0  # max text height (line pitch) in mm
    lead_mm: float = 0.0
    trail_mm: float = 0.0
    align: str = "center"  # center | outside | inside: where text sits within its cell (two-row mode)
    edge_margin_mm: float = 0.3
    # two-row mode: one strip lies between two header rows, one half per row
    dual: bool = False
    gutter_mm: float = 0.6
    center_rule: bool = True
    dual_dir: str = "same"  # same (both rows read the same way) | outward (read away from the centre)


def auto_max_chars(o: Options, target_mm: float = 1.7) -> int:
    """How many characters fit while keeping the text about target_mm tall, for this tape/pitch/layout."""
    H = tape_height_px(o.tape_mm)
    pitch = mm_to_px(o.pitch_mm)
    pad = max(round(mm_to_px(o.edge_margin_mm)), 1)
    zh = (H - max(round(mm_to_px(o.gutter_mm)), 0)) // 2 // 2 * 2 if o.dual else H
    horiz = o.text_dir == "horizontal"
    stack = (zh if horiz else round(pitch)) - 2 * pad
    run = (round(pitch) if horiz else zh) - 2 * pad
    nl = max(len(o.lines), 1)
    em = _fit(["Hg"] * nl, o.font, max(min(stack, round(mm_to_px(o.font_cap_mm)) * nl), 4), 10**6)
    target = min(em, round(mm_to_px(target_mm)))
    return max(2, int(run // (0.72 * target)))


def resolved_style(p: Pin) -> str:
    if p.style != "auto":
        return p.style
    t = p.label.strip().upper()
    if t in GND_WORDS:
        return "inverted"
    if t in POWER_WORDS:
        return "boxed"
    return "normal"


def reversed_order(o: Options) -> bool:
    """Single row: pin 1 on the right XOR viewing from the bottom reverses cell order.
    Dual row: flipping the board swaps the two halves instead, so only pin1_side reverses."""
    if o.dual:
        return o.pin1_side == "right"
    return (o.pin1_side == "right") != (o.board_side == "bottom")


def ordered(pins: list[Pin], o: Options) -> list[Pin]:
    return list(reversed(pins)) if reversed_order(o) else list(pins)


def _text_size(font, s):
    l, t, r, b = font.getbbox(s)
    return r - l, b - t, l, t


def _fit(texts, font_name, max_h, max_len):
    """Largest font px where the stacked lines are <= max_h tall and each fits max_len."""
    n = max(len(texts), 1)
    hi = int(max_h / n * 1.3)
    for px in range(max(hi, 4), 3, -1):
        f = font_for(font_name, px)
        line_h = f.getbbox("Hg")[3] - f.getbbox("Hg")[1]
        if line_h * n > max_h:
            continue
        if all(_text_size(f, s)[0] <= max_len for s in texts):
            return px
    return 4


def _lines_of(p: Pin, o: Options) -> list[str]:
    out = []
    for k in o.lines:
        v = {"number": p.number, "label": shorten_label(p.label, o.max_chars), "desc": p.desc}[k].strip()
        if v:
            out.append(v)
    return out


def _draw_cell(img, rect, p, o, base_px, direction, pad, warnings, idx, v_align="center"):
    """Draw one pin into rect=(x0,y0,x1,y1) of img."""
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    style = resolved_style(p)
    if style == "inverted":
        d.rectangle([x0, y0, x1 - 1, y1 - 1], fill=0)
    lines = _lines_of(p, o)
    if lines:
        horiz = direction == "horizontal"
        run_extent = max((w if horiz else h) - 2 * pad, 4)
        run_extent -= run_extent % 2  # even, so the tile lines up with the 180-dpi dot grid
        fg, bg = (255, 0) if style == "inverted" else (0, 255)
        px = base_px

        def widest(size):
            return max(_text_size(font_for(o.font, size), s)[0] for s in lines)

        if widest(px) > run_extent:
            if o.shrink_each:
                while px > 4 and widest(px) > run_extent:
                    px -= 1
            else:
                warnings.append(f"cell {idx} ('{p.label}') is too long at the chosen size")
        if px < MIN_READABLE_PX and px < base_px:
            warnings.append(f"cell {idx} ('{p.label}') shrank to {px / OUT_DPI * 25.4:.2f} mm text - hard to read")
        f = font_for(o.font, px)
        top = f.getbbox("Hg")[1]
        line_h = f.getbbox("Hg")[3] - top
        total = line_h * len(lines)
        total += total % 2
        if horiz:
            tile = Image.new("L", (w, h), bg)
            td = ImageDraw.Draw(tile)
            y_start = (h - total) // 2
            for k, s in enumerate(lines):
                tw, _, l, _ = _text_size(f, s)
                tx = (w - tw) // 2 - l if o.align == "center" else pad - l
                td.text((tx, y_start + k * line_h - top), s, font=f, fill=fg)
            img.paste(tile, (x0, y0))
        else:
            tile = Image.new("L", (run_extent, total), bg)
            td = ImageDraw.Draw(tile)
            for k, s in enumerate(lines):
                tw, _, l, _ = _text_size(f, s)
                if v_align == "center":
                    tx = (run_extent - tw) // 2 - l
                else:  # text reads bottom->top when "up": its start is the bottom of the cell
                    at_start = (v_align == "bottom") == (direction == "up")
                    tx = -l if at_start else run_extent - tw - l
                td.text((tx, k * line_h - top), s, font=f, fill=fg)
            if o.snap:  # tile x axis = along the text = across the tape (always 180 dpi); y axis = along the feed
                tile = _snap(tile, 2, 2 if o.print_mode == "standard" else 1, style != "inverted")
            tile = tile.rotate(90 if direction == "up" else -90, expand=True)
            tx = x0 + ((w - tile.width) // 2) // 2 * 2
            ty = y0 + ((h - tile.height) // 2) // 2 * 2
            img.paste(tile, (tx, ty))
    if style == "boxed":
        d.rectangle([x0 + 1, y0 + 1, x1 - 2, y1 - 2], outline=0, width=max(1, OUT_DPI // 180))


def render_strip(pins: list[Pin], o: Options, pins_b: list[Pin] | None = None) -> tuple[Image.Image, dict]:
    """pins = row A (the only row when o.dual is False); pins_b = row B for dual mode."""
    H = tape_height_px(o.tape_mm)
    pitch = mm_to_px(o.pitch_mm)
    lead = round(mm_to_px(o.lead_mm))
    trail = round(mm_to_px(o.trail_mm))
    pad = max(round(mm_to_px(o.edge_margin_mm)), 1)
    warnings: list[str] = []

    rows = [list(pins)]
    if o.dual:
        rows.append(list(pins_b or []))
        if o.board_side == "bottom":  # flipped board: the two rows swap sides
            rows.reverse()
    n = max(len(r) for r in rows)
    if n == 0:
        return Image.new("1", (8, H), 1), {"px": (8, H), "mm": (0, 0), "pitch_px": pitch,
                                            "font_px": 0, "font_mm": 0, "warnings": []}
    rows = [ordered(r + [Pin()] * (n - len(r)), o) for r in rows]
    W = lead + round(n * pitch / 2) * 2 + trail
    img = Image.new("L", (max(W, 1), H), 255)
    d = ImageDraw.Draw(img)

    if o.dual:
        gut = max(round(mm_to_px(o.gutter_mm)), 0)
        zh = (H - gut) // 2 // 2 * 2  # even, so both halves start on the dot grid
        zones = [(H - zh, H), (0, zh)]  # side A (left column when the strip is turned upright) is the bottom half
        if o.text_dir == "horizontal":
            dirs = ["horizontal", "horizontal"]
        elif o.dual_dir == "outward":
            dirs = ["up", "down"]
        else:
            dirs = [o.text_dir, o.text_dir]
    else:
        zones = [(0, H)]
        dirs = [o.text_dir]

    cap_px = round(mm_to_px(o.font_cap_mm))
    nmax = max([len(_lines_of(p, o)) for r in rows for p in r] + [1])
    base = []
    for (zy0, zy1), dr in zip(zones, dirs):
        stack = (round(pitch) if dr != "horizontal" else zy1 - zy0) - 2 * pad
        base.append(_fit(["Hg"] * nmax, o.font, max(min(stack, cap_px * nmax), 4), 10**6))
    if o.uniform:
        for z, ((zy0, zy1), dr) in enumerate(zip(zones, dirs)):
            run = max((pitch if dr == "horizontal" else zy1 - zy0) - 2 * pad, 4)
            texts = [t for r in rows for p in r for t in _lines_of(p, o)]
            px = base[z]
            while px > 4 and any(_text_size(font_for(o.font, px), t)[0] > run for t in texts):
                px -= 1
            base[z] = px
    if min(base) < MIN_READABLE_PX:
        warnings.append(f"base text is only {min(base) / OUT_DPI * 25.4:.2f} mm - use fewer lines per cell, "
                        f"a wider pitch or wider tape")

    def cell_x(i):  # cell edges on even pixels = whole 180-dpi dots
        return lead + round(i * pitch / 2) * 2, lead + round((i + 1) * pitch / 2) * 2

    def v_align_for(zy0):
        if o.align == "center":
            return "center"
        bottom_zone = zy0 > 0 or not o.dual
        outside = "bottom" if bottom_zone else "top"
        return outside if o.align in ("outside", "start") else ("top" if outside == "bottom" else "bottom")

    for r, ((zy0, zy1), dr, bpx) in enumerate(zip(zones, dirs, base)):
        for i, p in enumerate(rows[r]):
            x0, x1 = cell_x(i)
            _draw_cell(img, (x0, zy0, x1, zy1), p, o, bpx, dr, pad, warnings,
                       f"{i + 1}{'AB'[r] if o.dual else ''}", v_align_for(zy0))

    if o.mark_pin1:
        tri = max(round(mm_to_px(0.7)), 4)
        marks = [(r, i) for r in range(len(rows)) for i, p in enumerate(rows[r]) if p.number.strip() == "1"]
        if not marks:  # no pin numbered 1: mark the first cell of the first row
            marks = [(0, n - 1 if reversed_order(o) else 0)]
        for r, i in marks:
            x0, _ = cell_x(i)
            col = 255 if resolved_style(rows[r][i]) == "inverted" else 0
            zy0, zy1 = zones[r]
            if o.dual and zy0 > 0:  # bottom zone (the left column once the strip is turned upright): outer corner is the bottom
                d.polygon([(x0, zy1 - 1), (x0 + tri, zy1 - 1), (x0, zy1 - 1 - tri)], fill=col)
            else:
                d.polygon([(x0, zy0), (x0 + tri, zy0), (x0, zy0 + tri)], fill=col)

    thick = max(round(mm_to_px(o.divider_mm)), 2)
    thick += thick % 2  # even, so it maps to whole dots if Editor resamples 360 -> 180 dpi

    if o.dual and o.center_rule:
        cy = H // 2
        for x in range(lead, lead + round(n * pitch), 4 * thick):  # dashed centre rule
            d.rectangle([x, cy - thick // 4 - 1, min(x + 2 * thick - 1, W - 1), cy + thick // 4], fill=0)

    if o.divider != "none":
        tick = round(mm_to_px(1.0))
        for i in range(n + 1):
            x = lead + round(i * pitch / 2) * 2
            if i == 0:
                xa = 0 if lead == 0 else x - thick // 2
            elif i == n and trail == 0:
                xa = img.width - thick
            else:
                xa = (x - thick // 2) // 2 * 2
            xb = xa + thick - 1
            if o.divider == "lines":
                d.rectangle([xa, 0, xb, H - 1], fill=0)
            else:
                d.rectangle([xa, 0, xb, tick], fill=0)
                d.rectangle([xa, H - 1 - tick, xb, H - 1], fill=0)

    bw = img.point(lambda v: 255 if v > 127 else 0).convert("1")
    return bw, {
        "px": bw.size,
        "mm": (bw.width / OUT_DPI * 25.4, bw.height / OUT_DPI * 25.4),
        "pitch_px": pitch,
        "font_px": min(base),
        "font_mm": min(base) / OUT_DPI * 25.4,
        "warnings": warnings,
    }


def calibration_strip(tape_mm: float, length_mm: float = 50.8) -> Image.Image:
    """Ruler + 2.54 mm comb + known-size frame. Import into Editor and measure."""
    H = tape_height_px(tape_mm)
    W = round(mm_to_px(length_mm))
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W - 1, H - 1], outline=0, width=2)
    f = font_for("Arial Bold", round(mm_to_px(1.6)))
    for mm in range(0, int(length_mm) + 1):
        x = min(round(mm_to_px(mm)), W - 1)
        ln = round(mm_to_px(3 if mm % 10 == 0 else 2 if mm % 5 == 0 else 1))
        d.line([(x, 0), (x, ln)], fill=0)
        if mm % 10 == 0 and 0 < mm < length_mm:
            d.text((x + 3, ln + 2), str(mm), font=f, fill=0)
    k = 0
    while k * 2.54 <= length_mm + 1e-6:
        x = min(round(mm_to_px(k * 2.54)), W - 1)
        d.line([(x, H - 1 - round(mm_to_px(2))), (x, H - 1)], fill=0)
        k += 1
    d.text((6, H // 2 - 8), f"{length_mm:.1f} x {H / OUT_DPI * 25.4:.2f} mm  {tape_mm}mm tape", font=f, fill=0)
    return img.point(lambda v: 255 if v > 127 else 0).convert("1")


def save_png(img: Image.Image, fp):
    img.save(fp, format="PNG", dpi=(OUT_DPI, OUT_DPI))
