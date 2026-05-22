#!/usr/bin/env python3
"""Generate PWA icon PNGs from an SVG-like description using only stdlib."""
import struct
import zlib


def make_png(size: int) -> bytes:
    """Generate a simple PNG with the Balm 'B' mark on parchment background."""
    bg = (242, 237, 228)   # #f2ede4
    fg = (107, 130, 168)   # #6b82a8

    # Build RGBA pixel grid
    pixels = []
    cx, cy = size // 2, size // 2
    r_outer = int(size * 0.42)
    r_inner = int(size * 0.30)
    stroke = max(2, size // 48)

    for y in range(size):
        row = []
        for x in range(size):
            # Circle background disc
            dx, dy = x - cx, y - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist <= r_outer:
                # Draw a stylised 'B' letterform using simple geometry
                rel_x = (x - cx + r_outer) / (2 * r_outer)   # 0..1
                rel_y = (y - cy + r_outer) / (2 * r_outer)   # 0..1

                in_b = _in_letter_b(rel_x, rel_y)
                if in_b:
                    row.extend([fg[0], fg[1], fg[2], 255])
                else:
                    row.extend([bg[0], bg[1], bg[2], 255])
            else:
                row.extend([bg[0], bg[1], bg[2], 255])
        pixels.append(bytes(row))

    return _encode_png(pixels, size, size)


def _in_letter_b(rx: float, ry: float) -> bool:
    """Return True if normalised coords (0-1) fall inside a simple 'B' glyph."""
    # Vertical stem: left column
    stem_x = (0.28, 0.42)
    stem_y = (0.18, 0.82)

    if stem_x[0] <= rx <= stem_x[1] and stem_y[0] <= ry <= stem_y[1]:
        return True

    # Top bump
    if _in_bump(rx, ry, cy=0.31, r=0.19, x_start=0.40, thickness=0.14):
        return True

    # Bottom bump
    if _in_bump(rx, ry, cy=0.62, r=0.22, x_start=0.40, thickness=0.14):
        return True

    return False


def _in_bump(rx, ry, cy, r, x_start, thickness):
    """Right-side semicircle bump for the letter B."""
    cx_bump = x_start
    dy = ry - cy
    dx = rx - cx_bump
    if dx < 0:
        return False
    dist = (dx * dx + dy * dy) ** 0.5
    return (r - thickness) <= dist <= r


def _encode_png(rows, width, height):
    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b''.join(b'\x00' + row for row in rows)
    compressed = zlib.compress(raw, 9)

    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', compressed)
        + chunk(b'IEND', b'')
    )


if __name__ == '__main__':
    import os
    out_dir = os.path.join(os.path.dirname(__file__), 'docs', 'icons')
    os.makedirs(out_dir, exist_ok=True)
    for size in (192, 512):
        path = os.path.join(out_dir, f'icon-{size}.png')
        with open(path, 'wb') as f:
            f.write(make_png(size))
        print(f'Written {path}')
