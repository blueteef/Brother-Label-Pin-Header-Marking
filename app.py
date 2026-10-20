import dataclasses
import io

import pandas as pd
import streamlit as st
from PIL import Image

import pinlabel as pl
import printer as pr

st.set_page_config(page_title="Pin Label Maker", layout="wide")
st.title("Pin Label Maker")
st.caption("Builds a 1-bit PNG at exact physical size for P-touch Editor (PT-P750W). Import it, don't resize it.")

COLS = ["number", "label", "desc", "style"]

NUMBERING_HTML = """
<div style="display:flex;gap:32px;flex-wrap:wrap;align-items:flex-start;font-family:sans-serif;font-size:13px">
 <figure style="margin:0;text-align:center">
  <svg width="210" height="190" viewBox="0 0 210 190" fill="none" stroke="currentColor" stroke-width="1.5">
   <path d="M70 20 H140 V170 H70 Z"/><path d="M95 20 a10 10 0 0 0 20 0"/>
   <g stroke-width="2"><path d="M50 45 H70 M50 80 H70 M50 115 H70 M50 150 H70 M140 45 H160 M140 80 H160 M140 115 H160 M140 150 H160"/></g>
   <g fill="currentColor" stroke="none" font-size="13" text-anchor="end">
    <text x="44" y="50">1</text><text x="44" y="85">2</text><text x="44" y="120">3</text><text x="44" y="155">4</text></g>
   <g fill="currentColor" stroke="none" font-size="13">
    <text x="166" y="50">8</text><text x="166" y="85">7</text><text x="166" y="120">6</text><text x="166" y="155">5</text></g>
   <circle cx="82" cy="34" r="3" fill="currentColor"/>
  </svg>
  <figcaption>DIP chip, upright (notch up)</figcaption>
 </figure>
 <figure style="margin:0;text-align:center">
  <svg width="230" height="190" viewBox="0 0 230 190" fill="none" stroke="currentColor" stroke-width="1.5">
   <rect x="70" y="14" width="90" height="162" rx="6"/><rect x="95" y="6" width="40" height="22" rx="3"/>
   <g fill="currentColor" stroke="none"><circle cx="82" cy="52" r="4"/><circle cx="82" cy="84" r="4"/><circle cx="82" cy="116" r="4"/><circle cx="82" cy="148" r="4"/>
    <circle cx="148" cy="52" r="4"/><circle cx="148" cy="84" r="4"/><circle cx="148" cy="116" r="4"/><circle cx="148" cy="148" r="4"/></g>
   <g fill="currentColor" stroke="none" font-size="13" text-anchor="end">
    <text x="62" y="57">1</text><text x="62" y="89">2</text><text x="62" y="121">3</text><text x="62" y="153">4</text></g>
   <g fill="currentColor" stroke="none" font-size="13">
    <text x="168" y="57">8</text><text x="168" y="89">7</text><text x="168" y="121">6</text><text x="168" y="153">5</text></g>
   <text x="115" y="100" font-size="11" fill="currentColor" stroke="none" text-anchor="middle">MCU board</text>
   <text x="115" y="114" font-size="11" fill="currentColor" stroke="none" text-anchor="middle">(USB up)</text>
  </svg>
  <figcaption>MCU board, USB / antenna up</figcaption>
 </figure>
 <div style="max-width:280px;line-height:1.5">
  Same rule for both: pin 1 is top-left, numbers run <b>down the left side</b>, then
  <b>up the right side</b> ending top-right. Enter pins in that order. The label strip lies
  between the two rows: the <b>left column</b> of pins on one half, the <b>right column</b> on the other.
 </div>
</div>
"""


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in COLS:
        if c not in df:
            df[c] = "auto" if c == "style" else ""
    df = df[COLS].fillna({"label": "", "desc": "", "style": "auto"})
    df["number"] = df["number"].astype(str).replace("nan", "")
    return df.reset_index(drop=True)


if "df" not in st.session_state:
    labels = ["3V3", "EN", "GPIO36", "GPIO39", "GPIO34", "GPIO35", "GPIO32", "GND",
              "GPIO19", "GND", "GPIO21", "RXD0", "TXD0", "GPIO22", "GPIO23", "GND"]
    st.session_state.df = normalize(pd.DataFrame({"number": range(1, 17), "label": labels}))
if "_left_pending" in st.session_state:  # set before the widget exists on this run
    st.session_state["left_n"] = st.session_state.pop("_left_pending")


def live() -> pd.DataFrame:
    """Latest edited table (falls back to the editor's input before the first edit)."""
    return st.session_state.get("cur", st.session_state.df)


def set_df(d: pd.DataFrame, left: int | None = None):
    """Replace the table wholesale and reset the editor so stale edits are not re-applied."""
    st.session_state.df = normalize(d)
    st.session_state.cur = st.session_state.df
    st.session_state.pop("ed", None)
    st.session_state["_left_pending"] = left if left else max(len(st.session_state.df) // 2, 1)
    st.rerun()


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Label")
    tape = st.selectbox("Tape width (mm)", list(pl.TAPE_DOTS), index=list(pl.TAPE_DOTS).index(12))
    pitch_choice = st.selectbox("Pin pitch", ["2.54 mm (0.1\")", "2.00 mm", "1.27 mm (0.05\")", "1.00 mm", "Custom"])
    pitch = {"2.54 mm (0.1\")": 2.54, "2.00 mm": 2.0, "1.27 mm (0.05\")": 1.27, "1.00 mm": 1.0}.get(pitch_choice)
    if pitch is None:
        pitch = st.number_input("Custom pitch (mm)", 0.5, 20.0, 2.54, 0.01)

    quality = st.radio("Print quality (set the same in Editor)", ["High 180x360 (recommended)", "Standard 180x180"],
                       help="Text runs across the tape, which is always 180 dpi, but letter height runs along the feed. "
                            "High-res gives the letters twice the detail (G vs O, 3 vs 8). Choose the same quality in "
                            "P-touch Editor's print settings so the dots line up.")
    print_mode = "high" if quality.startswith("High") else "standard"

    layout = st.radio("Pin layout", ["Two rows (MCU / DIP)", "One row (header)"],
                      help="Two rows: one strip lies between both header rows, left column on one half and right column on the other.")
    dual = layout.startswith("Two")
    flipped = st.radio("Board side", ["Top", "Bottom (flipped over)"],
                       help="Bottom: the left and right columns swap halves, as when you turn the board over. "
                            "One row: the pin order reverses instead.").startswith("Bottom")
    if dual:
        align = st.radio("Text position", ["center", "outside", "inside"], horizontal=True,
                         help="Where each label sits within its half: centred, toward the board edge, or toward the middle of the strip.")
    else:
        align = "center"

    limit_mode = st.radio("Label length limit", ["Auto", "Custom", "Off"], horizontal=True,
                          help="Long names force the whole strip's text to shrink. Auto picks the most characters "
                               "that fit at a readable size for the current tape, pitch and layout. Labels over the "
                               "limit are shortened on the label only (GPIO36 -> 36, RESET -> RST); the table keeps "
                               "the full names. Type a short name in the table to override one.")
    custom_chars = 3
    if limit_mode == "Custom":
        custom_chars = st.number_input("Max characters per label", 1, 12, 3)

    with st.expander("More options"):
        lines = st.multiselect("Lines per cell (in order)", ["number", "label", "desc"],
                               default=["label"] if dual else ["number", "label"])
        font = st.selectbox("Font", list(pl.FONTS), index=0,
                            help="Condensed bold faces fit the most legible text into the short space across the tape.")
        snap = st.checkbox("Snap text to printer dots", False,
                           help="Pre-resamples text to whole printer dots so Editor doesn't blur it.")
        cap = st.slider("Max text height (mm)", 0.8, 4.0, 2.0, 0.1)
        uniform = st.checkbox("Uniform text size", True, help="One size for the whole strip, set by the longest label.")
        shrink = st.checkbox("Shrink long labels individually", True)
        corr = st.number_input("Length correction (%)", -5.0, 5.0, 0.0, 0.1,
                               help="Stretches (+) or shrinks (-) the whole strip if the printed pin spacing is "
                                    "slightly off. E.g. 20 pins (50.8 mm) printing 0.5 mm short needs about +1.0.")
        divider = st.selectbox("Cell dividers", ["ticks", "lines", "none"])
        divider_mm = st.slider("Divider thickness (mm)", 0.1, 0.8, 0.3, 0.05,
                               help="Thin lines can drop out when Editor resamples to 180 dpi. 0.3 mm or more prints reliably.")
        mark1 = st.checkbox("Mark pin 1 (corner triangle)", True)
        if dual:
            gutter = st.number_input("Centre gutter (mm)", 0.0, 5.0, 0.6, 0.1)
            rule = st.checkbox("Dashed centre rule", True)
        else:
            gutter, rule = 0.6, True

# ------------------------------------------------------------------ main
with st.expander("How pins are numbered", expanded=True):
    st.html(NUMBERING_HTML)

left_col, right_col = st.columns([3, 2])

with left_col:
    st.subheader("Pins")
    df = live()
    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        n = st.number_input("Pin count", 1, 160, len(df))
    with c2:
        st.write("")
        if st.button("Resize table"):
            d = df.reindex(range(int(n)))
            d["number"] = [str(i + 1) for i in range(int(n))]
            set_df(d)
    if dual:
        with c3:
            if "left_n" not in st.session_state:
                st.session_state["left_n"] = max(len(df) // 2, 1)
            left_n = st.number_input("Pins on left side", 1, max(len(df), 1), key="left_n",
                                     help="Pins 1 to this number run down the left side; the rest run up the right side.")
    else:
        left_n = len(df)

    cfg = {"style": st.column_config.SelectboxColumn(options=["auto", "normal", "inverted", "boxed"])}
    edited = st.data_editor(st.session_state.df, num_rows="dynamic", width="stretch", hide_index=True,
                            key="ed", column_config=cfg)
    st.session_state.cur = normalize(edited)
    st.caption("auto style: GND-type pins print inverted (white on black), supply rails get a box.")

    with st.expander("Paste labels"):
        paste = st.text_area("One per line, optional `label, description`", height=100, label_visibility="collapsed")
        if st.button("Replace table from paste") and paste.strip():
            rows = []
            for i, ln in enumerate(x for x in paste.splitlines() if x.strip()):
                parts = [x.strip() for x in ln.split(",", 1)]
                rows.append([str(i + 1), parts[0], parts[1] if len(parts) > 1 else "", "auto"])
            set_df(pd.DataFrame(rows, columns=COLS))

    with st.expander("Load / save CSV"):
        up_csv = st.file_uploader("Load CSV (number,label,desc,style)", type="csv")
        if up_csv is not None and st.session_state.get("loaded_csv") != up_csv.name:
            st.session_state["loaded_csv"] = up_csv.name
            set_df(pd.read_csv(up_csv, dtype=str).fillna(""))
        st.download_button("Save table as CSV", st.session_state.cur.to_csv(index=False), "pins.csv", "text/csv")

    with st.expander("Auto-fill labels from a pinout image (experimental)"):
        st.caption("Reads the text beside the header rows of a board diagram. Check the result before using it.")
        ref = st.file_uploader("PNG / JPG / SVG pinout", type=["png", "jpg", "jpeg", "svg"], key="ref")
        colsel = st.selectbox("Which label column", ["1 - nearest the board", "2", "3", "Outermost"],
                              help="Diagrams stack several label columns beside each row (port name, alias, function). "
                                   "Pick the one that matches the silkscreen.")
        if ref is not None:
            import extract as ex

            data = ref.getvalue()
            key = (ref.name, len(data))
            if st.session_state.get("_ocr_key") != key:
                with st.spinner("Reading the diagram (first run loads the OCR model)..."):
                    if ref.name.lower().endswith(".svg"):
                        boxes, pic = ex.svg_boxes(data), None
                    else:
                        pic = Image.open(io.BytesIO(data))
                        boxes = ex.ocr_boxes(pic)
                st.session_state.update(_ocr_key=key, _ocr_boxes=boxes, _ocr_pic=pic)
            boxes, pic = st.session_state["_ocr_boxes"], st.session_state["_ocr_pic"]
            colnum = {"1 - nearest the board": 1, "2": 2, "3": 3, "Outermost": -1}[colsel]
            res = ex.pick_pins(boxes, colnum)
            found, left_count = ex.to_pin_list(res)
            if pic is not None:
                st.image(ex.annotate(pic, boxes, res), caption="Green boxes = labels taken", width="stretch")
            if found:
                st.write(f"{len(found)} pins ({left_count} left, {len(found) - left_count} right), "
                         f"{res['n_cols'][0]} / {res['n_cols'][1]} label columns left / right")
                st.code(", ".join(x or "-" for x in found))
                if st.button("Use these labels"):
                    set_df(pd.DataFrame({"number": range(1, len(found) + 1), "label": found}), left=left_count)
            else:
                st.warning("No pin labels found.")

# ------------------------------------------------------------------ render
cur = st.session_state.cur


def to_pins(d):
    return [pl.Pin(str(r.number or ""), str(r.label or ""), str(r.desc or ""), str(r.style or "auto"))
            for r in d.itertuples()]


all_pins = to_pins(cur)
if dual:
    k = min(int(left_n), len(all_pins))
    pins_a, pins_b = all_pins[:k], all_pins[k:][::-1]  # right column runs bottom->top, so reverse it for the strip
    counts = (len(pins_a), len(pins_b))
else:
    pins_a, pins_b, counts = all_pins, None, None

opts = pl.Options(
    tape_mm=tape, pitch_mm=pitch * (1 + corr / 100), font=font, lines=tuple(lines), text_dir="up", dual_dir="same",
    board_side="bottom" if flipped else "top", divider=divider, divider_mm=divider_mm, mark_pin1=mark1, shrink_each=shrink,
    uniform=uniform, font_cap_mm=cap, snap=snap, print_mode=print_mode, align=align, dual=dual, gutter_mm=gutter, center_rule=rule,
)
if limit_mode == "Auto":
    max_chars = pl.auto_max_chars(opts)
elif limit_mode == "Custom":
    max_chars = int(custom_chars)
else:
    max_chars = 0
opts = dataclasses.replace(opts, max_chars=max_chars)
img, info = pl.render_strip(pins_a, opts, pins_b)

with right_col:
    st.subheader("Preview")
    w_mm, h_mm = info["mm"]
    # turned 90 degrees clockwise so the text reads upright: pin 1 at the top, left column on the left
    prev = img.convert("L").rotate(-90, expand=True)
    scale = max(1, min(3, 1400 // max(prev.height, 1)))
    st.image(prev.resize((prev.width * scale, prev.height * scale), Image.NEAREST))
    st.write(f"**{w_mm:.2f} x {h_mm:.2f} mm** ({img.width} x {img.height} px at {pl.OUT_DPI} dpi), "
             f"text {info['font_mm']:.2f} mm")
    st.caption("Preview is turned upright. The exported PNG is the horizontal strip Editor needs.")
    if limit_mode == "Auto":
        st.caption(f"Label length limit: Auto = up to {max_chars} characters here.")
    short = [(p.label, pl.shorten_label(p.label, int(max_chars))) for p in all_pins if p.label.strip()]
    short = [(a, b) for a, b in short if a.strip() != b]
    if short:
        st.caption("Shortened on the label: " + ", ".join(f"{a} -> {b}" for a, b in short[:20])
                   + (f" (+{len(short) - 20} more)" if len(short) > 20 else ""))
    if counts and counts[0] != counts[1]:
        st.warning(f"Left side has {counts[0]} pins, right side {counts[1]}; the shorter side is padded with blank cells.")
    for w in info["warnings"][:6]:
        st.warning(w)
    if len(info["warnings"]) > 6:
        st.warning(f"...and {len(info['warnings']) - 6} more")

    buf = io.BytesIO()
    pl.save_png(img, buf)
    st.download_button("Download label PNG", buf.getvalue(), "pin_label.png", "image/png", type="primary")
    cal = io.BytesIO()
    pl.save_png(pl.calibration_strip(tape), cal)
    st.download_button("Download calibration PNG (50.8 mm ruler)", cal.getvalue(), "calibration.png", "image/png")
    st.subheader("Print directly")
    names = pr.list_printers()
    via = st.radio("Send via", ["USB / Windows printer", "Network (IP address)"], horizontal=True)
    printer_name, host = None, None
    if via.startswith("USB"):
        guess = pr.find_printer()
        if names:
            printer_name = st.selectbox("Printer", names, index=names.index(guess) if guess in names else 0)
        else:
            st.warning("No printers found.")
    else:
        host = st.text_input("Printer IP address", help="PT-P750W over Wi-Fi/LAN, raw port 9100.")
    p1, p2, p3 = st.columns(3)
    copies = p1.number_input("Copies", 1, 20, 1)
    margin = p2.number_input("Margin (mm)", pr.MIN_MARGIN_MM, 50.0, pr.MIN_MARGIN_MM, 0.5,
                             help="Blank tape fed before and after the label (minimum 2 mm).")
    cut_mode = p3.selectbox("Cut", ["Half cut", "Full cut", "No cut"],
                            help="Half cut slices the label layer but leaves the backing attached, so the label "
                                 "can't drop into the printer. Full cut severs everything.")
    cut, half = cut_mode != "No cut", cut_mode == "Half cut"
    st.caption(f"Loaded tape must be {tape} mm. Printing in {'high (180x360)' if print_mode == 'high' else 'standard (180x180)'} quality "
               f"from the sidebar setting; the printer can't report a tape mismatch back, so check the tape first.")
    if st.button("Print label", type="primary", disabled=not (printer_name or host)):
        try:
            sent = pr.print_strip(img, tape, printer=printer_name, host=host or None, high_res=print_mode == "high",
                                  margin_mm=margin, copies=int(copies), auto_cut=cut, half_cut=half)
            st.success(f"Sent {copies} label(s) ({sent:,} bytes) to {host or printer_name}.")
        except Exception as e:
            st.error(f"Print failed: {e}")
    if st.button("Print calibration strip (50.8 mm ruler)", disabled=not (printer_name or host)):
        try:
            pr.print_strip(pl.calibration_strip(tape), tape, printer=printer_name, host=host or None,
                           high_res=print_mode == "high", margin_mm=margin, auto_cut=cut, half_cut=half)
            st.success("Calibration strip sent.")
        except Exception as e:
            st.error(f"Print failed: {e}")

    st.info(
        f"In P-touch Editor: set tape to {tape} mm, length to Auto or >= {w_mm:.1f} mm, Insert > Picture from file, "
        f"in print settings choose {"High (180x360)" if print_mode == "high" else "Standard (180x180)"} quality, then check the object size reads {w_mm:.2f} x {h_mm:.2f} mm. Print the calibration PNG first and "
        f"measure with calipers."
    )
