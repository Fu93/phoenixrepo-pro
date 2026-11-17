"""Small PNG images. No font file: a built-in 5x7 alphabet."""

from __future__ import annotations

import struct
import zlib

# 5x7 glyphs, left to right, top to bottom, 1 = ink.
_GLYPHS = {
    " ": "00000000000000000000000000000000000",
    "A": "01110010001111111000110001100011000",
    "B": "11110010001111101000111110100011110",
    "C": "01111010001000010000100001000001110",
    "D": "11110010001100011000110001100011110",
    "E": "11111010001000011110100001000011111",
    "F": "11111010001000011110100001000010000",
    "G": "01111010001000010111100011000101110",
    "H": "10001100011111110001100011000110001",
    "I": "11111001000010000100001000010001111",
    "J": "00111000010000100001010010100100100",
    "K": "10001100101010011000101001001010001",
    "L": "10000100001000010000100001000011111",
    "M": "10001110111010110001100011000110001",
    "N": "10001110011010110011100011000110001",
    "O": "01110100011000110001100011000101110",
    "P": "11110100011000111110100001000010000",
    "Q": "01110100011000110001101011001001101",
    "R": "11110100011000111110101001001010001",
    "S": "01111100001000001110000011000011110",
    "T": "11111001000010000100001000010000100",
    "U": "10001100011000110001100011000101110",
    "V": "10001100011000110001010100101000100",
    "W": "10001100011000110101101011010100010",
    "X": "10001010100010000100010101000110001",
    "Y": "10001010100010000100001000010000100",
    "Z": "11111000010001000100010001000011111",
    "-": "00000000001111100000000000000000000",
    ".": "00000000000000000000000000000000100",
}


def _png(width: int, height: int, pixels: bytes) -> bytes:
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * stride:(y + 1) * stride])
    data = zlib.compress(bytes(raw), 9)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", data) + chunk(b"IEND", b"")


def _draw(width: int, height: int, title: str) -> bytes:
    cream = bytes((244, 239, 230))
    ink = bytes((28, 22, 18))
    ember = bytes((194, 65, 12))
    pixels = bytearray(cream * (width * height))

    def put(x: int, y: int, color: bytes) -> None:
        if 0 <= x < width and 0 <= y < height:
            start = (y * width + x) * 3
            pixels[start:start + 3] = color

    for y in range(height):
        for x in range(18):
            put(x, y, ember)
    text = "".join(ch if ch.upper() in _GLYPHS else " " for ch in title.upper())[:42]
    scale = 4
    origin_x, origin_y = 48, height // 2 - 14
    for index, ch in enumerate(text):
        glyph = _GLYPHS.get(ch, _GLYPHS[" "])
        for row in range(7):
            for col in range(5):
                if glyph[row * 5 + col] == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            put(origin_x + index * 6 * scale + col * scale + dx, origin_y + row * scale + dy, ink)
    return _png(width, height, bytes(pixels))


def og_png(title: str) -> bytes:
    clean = " ".join(str(title or "PhoenixRepo Pro").split()) or "PhoenixRepo Pro"
    return _draw(1200, 630, clean[:42])


def favicon_ico() -> bytes:
    """A PNG wrapped as an ICO so /favicon.ico is a real icon file."""

    import struct

    png = favicon_png()
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32, len(png), 22)
    return header + entry + png


def favicon_png() -> bytes:
    return _draw(32, 32, "P")
