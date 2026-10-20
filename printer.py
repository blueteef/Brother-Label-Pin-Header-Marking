"""Direct printing to a Brother PT-P750W: build the raster job from our strip PNG and send it.

Protocol (Brother "Raster Command Reference PT-E550W/P750W/P710BT", cv_pte550wp750wp710bt_eng_raster_102.pdf;
command bytes and pin layout cross-checked against the `ptouch` library, which cites the same document):

    200 x 0x00 (invalidate), ESC @, ESC i a 01 (raster mode), ESC i z (print information),
    ESC i M (auto cut), ESC i A (cut every page), ESC i K (advanced: half cut / chain / high-res),
    ESC i d (margin), M 02 (TIFF/PackBits compression), then one 'G' (or 'Z' for blank) per raster
    line, then 0x1A (print and feed).

One raster line = 128 bits across the tape. In standard mode lines are 180 dpi along the feed; in
high-res (180x360) mode they are 360 dpi, so we send our 360-dpi columns as distinct lines (the
`ptouch` library only repeats each 180-dpi line twice, which adds no detail).

Transport: the Windows print spooler in RAW mode (works over USB with the stock Brother driver, no
libusb/Zadig needed) or raw TCP port 9100 (Wi-Fi / network).
"""
from __future__ import annotations

import ctypes
import socket
import struct
from ctypes import wintypes

import packbits
from PIL import Image

TOTAL_PINS = 128
BYTES_PER_LINE = 16

# tape mm -> (left unused pins, printable pins, right unused pins); from Brother's spec section 2.3
PIN_CONFIG = {3.5: (52, 24, 52), 6: (48, 32, 48), 9: (39, 50, 39), 12: (29, 70, 29), 18: (8, 112, 8), 24: (0, 128, 0)}
TAPE_WIDTH_CODE = {3.5: 4, 6: 6, 9: 9, 12: 12, 18: 18, 24: 24}  # 3.5 mm tape reports as 4 mm
MIN_MARGIN_MM = 2.0
INK_COVERAGE = 0.4  # a dot prints if at least this fraction of it is ink (matches the text snapping bias)


def _to_dots(img: Image.Image, tape_mm: float, high_res: bool, coverage: float = INK_COVERAGE) -> Image.Image:
    """Resample our 360-dpi strip PNG onto printer dots: width = raster lines, height = printable pins."""
    _, print_pins, _ = PIN_CONFIG[tape_mm]
    g = img.convert("L")
    cols = g.width if high_res else g.width // 2
    small = g.resize((max(cols, 1), print_pins), Image.BOX)
    thr = round(255 * (1 - coverage))  # black (ink) if the average is <= this
    return small.point(lambda v: 0 if v <= thr else 255).convert("1")


def build_job(img: Image.Image, tape_mm: float, high_res: bool = True, margin_mm: float = MIN_MARGIN_MM,
              auto_cut: bool = True, half_cut: bool = False, first_job: bool = True,
              coverage: float = INK_COVERAGE) -> bytes:
    if tape_mm not in PIN_CONFIG:
        raise ValueError(f"Unsupported tape width {tape_mm} mm")
    margin_mm = max(margin_mm, MIN_MARGIN_MM)
    left, print_pins, _ = PIN_CONFIG[tape_mm]
    dots = _to_dots(img, tape_mm, high_res, coverage)
    lines = dots.width
    px = dots.load()

    margin = round(margin_mm * 180 / 25.4) * (2 if high_res else 1)  # feed amount, in raster lines
    out = bytearray()
    if first_job:
        out += b"\x00" * 200 + b"\x1b\x40"
    out += b"\x1b\x69\x61\x01"                                          # raster mode
    out += struct.pack("<7BL2B", 0x1B, 0x69, 0x7A, 0x80 | 0x04 | 0x02,  # print info: recover|width|length valid
                       0x00, TAPE_WIDTH_CODE[tape_mm], 0x00, lines, 0x00, 0x00)
    out += struct.pack("4B", 0x1B, 0x69, 0x4D, 0x40 if auto_cut else 0x00)  # mode: auto cut
    if auto_cut:
        out += struct.pack("4B", 0x1B, 0x69, 0x41, 1)                   # cut every page
    adv = (1 << 2 if half_cut else 0) | (1 << 3) | (1 << 6 if high_res else 0)  # no chain printing
    out += struct.pack("4B", 0x1B, 0x69, 0x4B, adv)
    out += struct.pack("5B", 0x1B, 0x69, 0x64, margin & 0xFF, (margin >> 8) & 0xFF)
    out += b"\x4d\x02"                                                  # TIFF (PackBits) compression

    for col in range(lines):
        bits = [0] * TOTAL_PINS
        for row in range(print_pins):
            if px[col, row] == 0:
                bits[left + row] = 1
        line = bytearray(BYTES_PER_LINE)
        for i, b in enumerate(bits):
            if b:
                line[i // 8] |= 0x80 >> (i % 8)
        if not any(line):
            out += b"\x5a"                                              # Z: blank raster line
        else:
            comp = packbits.encode(bytes(line))
            out += b"\x47" + struct.pack("<H", len(comp)) + comp         # G: raster line
    out += b"\x1a"                                                      # print and feed
    return bytes(out)


def decode_job(data: bytes) -> tuple[list[bytes], dict]:
    """Parse a job back into raw 16-byte raster lines (for verification). Returns (lines, info)."""
    i, lines, info = 0, [], {}
    while i < len(data) and data[i] == 0:
        i += 1
    while i < len(data):
        c = data[i]
        if data[i:i + 2] == b"\x1b\x40":
            i += 2
        elif data[i:i + 3] == b"\x1b\x69\x61":
            i += 4
        elif data[i:i + 3] == b"\x1b\x69\x7a":
            width = data[i + 5]
            nlines = struct.unpack("<L", data[i + 7:i + 11])[0]
            info.update(width=width, raster_lines=nlines)
            i += 13
        elif data[i:i + 3] in (b"\x1b\x69\x4d", b"\x1b\x69\x41", b"\x1b\x69\x4b"):
            info[{0x4d: "mode", 0x41: "pages", 0x4b: "advanced"}[data[i + 2]]] = data[i + 3]
            i += 4
        elif data[i:i + 3] == b"\x1b\x69\x64":
            info["margin"] = data[i + 3] | data[i + 4] << 8
            i += 5
        elif data[i:i + 2] == b"\x4d\x02":
            i += 2
        elif c == 0x47:
            n = struct.unpack("<H", data[i + 1:i + 3])[0]
            lines.append(bytes(packbits.decode(data[i + 3:i + 3 + n])))
            i += 3 + n
        elif c == 0x5A:
            lines.append(b"\x00" * BYTES_PER_LINE)
            i += 1
        elif c in (0x1A, 0x0C):
            info["end"] = c
            i += 1
        else:
            raise ValueError(f"unexpected byte {c:#x} at {i}")
    return lines, info


# ---------------------------------------------------------------- Windows spooler (RAW)

_winspool = ctypes.WinDLL("winspool.drv", use_last_error=True) if hasattr(ctypes, "WinDLL") else None


class _DOC_INFO_1(ctypes.Structure):
    _fields_ = [("pDocName", wintypes.LPWSTR), ("pOutputFile", wintypes.LPWSTR), ("pDatatype", wintypes.LPWSTR)]


def list_printers() -> list[str]:
    """Names of local printer queues."""
    if _winspool is None:
        return []
    flags = 0x2 | 0x4  # PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    needed, returned = wintypes.DWORD(), wintypes.DWORD()
    _winspool.EnumPrintersW(flags, None, 4, None, 0, ctypes.byref(needed), ctypes.byref(returned))
    buf = ctypes.create_string_buffer(needed.value)
    if not _winspool.EnumPrintersW(flags, None, 4, buf, needed, ctypes.byref(needed), ctypes.byref(returned)):
        raise ctypes.WinError(ctypes.get_last_error())

    class PRINTER_INFO_4(ctypes.Structure):
        _fields_ = [("pPrinterName", wintypes.LPWSTR), ("pServerName", wintypes.LPWSTR), ("Attributes", wintypes.DWORD)]

    arr = (PRINTER_INFO_4 * returned.value).from_buffer(buf)
    return [p.pPrinterName for p in arr]


def find_printer() -> str | None:
    names = list_printers()
    for key in ("P750W", "PT-P7", "P-touch", "PT-"):
        for n in names:
            if key.lower() in n.lower():
                return n
    return None


def send_spooler(printer: str, data: bytes, doc_name: str = "Pin label") -> None:
    if _winspool is None:
        raise RuntimeError("The Windows print spooler is not available on this system")
    k = _winspool
    k.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), wintypes.LPVOID]
    k.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(_DOC_INFO_1)]
    k.WritePrinter.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    k.StartPagePrinter.argtypes = k.EndPagePrinter.argtypes = k.EndDocPrinter.argtypes = k.ClosePrinter.argtypes = [wintypes.HANDLE]
    h = wintypes.HANDLE()
    if not k.OpenPrinterW(printer, ctypes.byref(h), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        doc = _DOC_INFO_1(doc_name, None, "RAW")
        if not k.StartDocPrinterW(h, 1, ctypes.byref(doc)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            k.StartPagePrinter(h)
            for off in range(0, len(data), 16384):
                chunk = data[off:off + 16384]
                written = wintypes.DWORD()
                if not k.WritePrinter(h, chunk, len(chunk), ctypes.byref(written)) or written.value != len(chunk):
                    raise ctypes.WinError(ctypes.get_last_error())
            k.EndPagePrinter(h)
        finally:
            k.EndDocPrinter(h)
    finally:
        k.ClosePrinter(h)


def send_tcp(host: str, data: bytes, port: int = 9100, timeout: float = 10.0) -> None:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(data)


def print_strip(img: Image.Image, tape_mm: float, *, printer: str | None = None, host: str | None = None,
                high_res: bool = True, margin_mm: float = MIN_MARGIN_MM, copies: int = 1,
                auto_cut: bool = True, half_cut: bool = False, coverage: float = INK_COVERAGE) -> int:
    """Print `copies` of the strip. Returns the number of bytes sent."""
    data = b"".join(build_job(img, tape_mm, high_res, margin_mm, auto_cut, half_cut, first_job=(i == 0), coverage=coverage)
                    for i in range(max(copies, 1)))
    if host:
        send_tcp(host, data)
    else:
        name = printer or find_printer()
        if not name:
            raise RuntimeError("No Brother PT printer found. Is it on and connected?")
        send_spooler(name, data)
    return len(data)
