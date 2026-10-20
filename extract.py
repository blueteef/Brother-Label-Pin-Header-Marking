"""Pull pin labels out of a board pinout diagram (PNG/JPG via OCR, SVG via its <text> elements).

Pinout diagrams usually put several columns of label pills beside each header row
(MCU port name, Dxx alias, function...). Everything is reduced to text boxes with positions,
then: split left/right of the board, find the rows and columns, and read one column per side.
Column 1 is the one nearest the board.
"""
from __future__ import annotations

import re
import statistics as stats
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from PIL import Image, ImageDraw


@dataclass
class Box:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2

    @property
    def h(self):
        return self.y1 - self.y0


# ---------------------------------------------------------------- box sources

_engine = None


def ocr_boxes(img: Image.Image) -> list[Box]:
    global _engine
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR

    if _engine is None:
        _engine = RapidOCR()
    res, _ = _engine(np.array(img.convert("RGB")))
    out = []
    for poly, text, conf in res or []:
        if float(conf) < 0.4:
            continue
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        out.append(Box(text.strip(), min(xs), min(ys), max(xs), max(ys)))
    return out


def _parse_transform(t: str):
    """Return affine (a,b,c,d,e,f) for translate()/matrix() transforms; others are ignored."""
    a, b, c, d, e, f = 1, 0, 0, 1, 0, 0
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", t or ""):
        v = [float(x) for x in re.split(r"[ ,]+", args.strip()) if x]
        if name == "translate":
            e2, f2 = v[0], (v[1] if len(v) > 1 else 0)
            e, f = a * e2 + c * f2 + e, b * e2 + d * f2 + f
        elif name == "matrix" and len(v) == 6:
            a, b, c, d, e, f = (a * v[0] + c * v[1], b * v[0] + d * v[1], a * v[2] + c * v[3], b * v[2] + d * v[3],
                                a * v[4] + c * v[5] + e, b * v[4] + d * v[5] + f)
    return a, b, c, d, e, f


def svg_boxes(data: bytes) -> list[Box]:
    root = ET.fromstring(data)
    out: list[Box] = []

    def num(s, default=0.0):
        m = re.match(r"\s*(-?[\d.]+)", s or "")
        return float(m.group(1)) if m else default

    def walk(el, m):
        tag = el.tag.split("}")[-1]
        a, b, c, d, e, f = m
        if el.get("transform"):
            ta, tb, tc, td, te, tf = _parse_transform(el.get("transform"))
            a, b, c, d, e, f = (a * ta + c * tb, b * ta + d * tb, a * tc + c * td, b * tc + d * td,
                                a * te + c * tf + e, b * te + d * tf + f)
        if tag == "text":
            txt = "".join(el.itertext()).strip()
            if txt:
                x = num(el.get("x")) if el.get("x") else num((el.find("./*[@x]") or el).get("x"))
                y = num(el.get("y")) if el.get("y") else num((el.find("./*[@y]") or el).get("y"))
                size = num(el.get("font-size"), 12) or 12
                w = len(txt) * size * 0.6
                anchor = el.get("text-anchor", "start")
                x0 = x - w / 2 if anchor == "middle" else x - w if anchor == "end" else x
                px0, py0 = a * x0 + c * (y - size * 0.8) + e, b * x0 + d * (y - size * 0.8) + f
                px1, py1 = a * (x0 + w) + c * (y + size * 0.2) + e, b * (x0 + w) + d * (y + size * 0.2) + f
                out.append(Box(txt, min(px0, px1), min(py0, py1), max(px0, px1), max(py0, py1)))
            return
        for child in el:
            walk(child, (a, b, c, d, e, f))

    walk(root, (1, 0, 0, 1, 0, 0))
    return out


# ---------------------------------------------------------------- geometry

def clean_label(t: str) -> str:
    t = t.strip()
    t = re.sub(r"^([AD])O(?=\d|$)", r"\g<1>0", t)  # OCR reads A0/D0 as AO/DO
    t = re.sub(r"^P[Oo](?=\.?\d)", "P0", t)           # ...and P0.17 as PO.17
    return t


def _cluster(values: list[float], gap: float) -> list[list[int]]:
    """Group indices of sorted values into clusters separated by more than gap."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    groups, cur = [], [order[0]]
    for i in order[1:]:
        if values[i] - values[cur[-1]] > gap:
            groups.append(cur)
            cur = [i]
        else:
            cur.append(i)
    groups.append(cur)
    return groups


def pick_pins(boxes: list[Box], column: int = 1) -> dict:
    """Return {'left': [...], 'right': [...], 'columns': n, 'used': [Box], 'all_kept': [Box]}.

    left/right are top-to-bottom lists of the same length (blank where a row has no label in the
    chosen column). column=1 is nearest the board; larger numbers go outward; -1 is the outermost.
    """
    empty = {"left": [], "right": [], "columns": 0, "used": [], "all_kept": [], "n_cols": (0, 0)}
    boxes = [Box(clean_label(b.text), b.x0, b.y0, b.x1, b.y1) for b in boxes if b.text.strip()]
    if len(boxes) < 4:
        return empty
    med_h = stats.median(b.h for b in boxes)
    kept = [b for b in boxes
            if b.h >= 0.6 * med_h and b.h <= 1.3 * med_h            # silkscreen digits / big logos
            and len(b.text) <= 9 and not re.search(r"[\s:]", b.text)  # board text lines
            and not (b.text.isalpha() and b.text.islower())]          # words like "seeed"
    if len(kept) < 4:
        return empty
    med_h = stats.median(b.h for b in kept)

    # rows: cluster y, keep the longest run of evenly spaced rows (drops legends and captions)
    ys = [b.cy for b in kept]
    rows = _cluster(ys, 0.6 * med_h)
    if sorted(len(g) for g in rows)[len(rows) // 2] >= 3:  # real pin rows carry several labels; drop strays
        rows = [g for g in rows if len(g) >= 2]
    centers = [stats.mean(ys[i] for i in g) for g in rows]
    if len(centers) < 2:
        return empty
    diffs = [b - a for a, b in zip(centers, centers[1:])]
    med_gap = stats.median(diffs)
    runs, cur = [], [0]
    for i, dgap in enumerate(diffs, start=1):
        if 0.5 * med_gap <= dgap <= 1.5 * med_gap:
            cur.append(i)
        else:
            runs.append(cur)
            cur = [i]
    runs.append(cur)
    best = max(runs, key=lambda r: sum(len(rows[i]) for i in r))
    row_centers = [centers[i] for i in best]
    keep_idx = {j for i in best for j in rows[i]}
    kept = [b for j, b in enumerate(kept) if j in keep_idx]
    tol = 0.5 * med_gap

    # left / right of the board: midpoint of the labelled span
    split = (min(b.cx for b in kept) + max(b.cx for b in kept)) / 2
    sides = {"left": [b for b in kept if b.cx < split], "right": [b for b in kept if b.cx >= split]}

    chosen = {}
    n_cols = []
    used = []
    for name, bs in sides.items():
        if not bs:
            chosen[name] = []
            n_cols.append(0)
            continue
        groups = _cluster([b.cx for b in bs], 1.5 * med_h)
        cols = [[bs[i] for i in g] for g in groups]
        cols = [c for c in cols if len(c) >= max(2, 0.3 * len(row_centers))]  # stray text is not a column
        cols.sort(key=lambda c: stats.mean(b.cx for b in c))
        if name == "left":
            cols.reverse()  # nearest the board first
        n_cols.append(len(cols))
        if not cols:
            chosen[name] = []
            continue
        idx = len(cols) - 1 if column == -1 else min(column, len(cols)) - 1
        chosen[name] = cols[idx]
        used += cols[idx]

    def per_row(col):
        out = []
        for rc in row_centers:
            m = [b for b in col if abs(b.cy - rc) <= tol]
            out.append(m[0].text if m else "")
        return out

    left, right = per_row(chosen.get("left", [])), per_row(chosen.get("right", []))
    while left and not left[0] and not right[0]:  # trim empty rows at the ends
        left, right = left[1:], right[1:]
    while left and not left[-1] and not right[-1]:
        left, right = left[:-1], right[:-1]
    return {"left": left, "right": right, "columns": max(n_cols), "n_cols": tuple(n_cols),
            "used": used, "all_kept": kept}


def annotate(img: Image.Image, boxes: list[Box], result: dict) -> Image.Image:
    """Green = labels taken, grey = other text the OCR/parse found."""
    im = img.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    used = {id(b) for b in result["used"]}
    for b in boxes:
        d.rectangle([b.x0, b.y0, b.x1, b.y1], outline=(160, 160, 160), width=1)
    for b in result["used"]:
        d.rectangle([b.x0 - 2, b.y0 - 2, b.x1 + 2, b.y1 + 2], outline=(0, 170, 0), width=3)
    return im


def to_pin_list(result: dict) -> tuple[list[str], int]:
    """DIP order: left column top->bottom, then right column bottom->top. Returns (labels, left_count)."""
    left, right = result["left"], result["right"]
    return left + right[::-1], len(left)
