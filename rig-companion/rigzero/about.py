"""Standard Nomad About screen: name, version, license, TheTechNomad, website QR."""
from __future__ import annotations

from . import __version__

APP_NAME = "Rig Companion"
LICENSE = "GPL-3.0"
DEVELOPER = "Ted Peterson"
WEBSITE = "www.TheTechNomad.com"
GITHUB = "github.com/TeddyPete"

# QR code for https://www.TheTechNomad.com (same matrix as the Nomad apps' nomadui.py).
SITE_QR = [
    "11111110111011110011101111111",
    "10000010101001001100001000001",
    "10111010001110001011101011101",
    "10111010101100110101001011101",
    "10111010010000101101001011101",
    "10000010000101011011101000001",
    "11111110101010101010101111111",
    "00000000101010011101000000000",
    "10110111001011010100001001011",
    "01000100111101110111100110001",
    "10111111101111001010110110110",
    "11001001010000001000101110001",
    "10100010111110110101000001100",
    "01100101000010101100001100111",
    "00011011110111011101000110111",
    "10000100101110101000010110010",
    "00111011100100111011110011010",
    "01001000010010101100100101110",
    "10111011010001111100100100100",
    "00010000011001100111110000100",
    "01100111110011101111111111100",
    "00000000100100011110100011111",
    "11111110111010110111101011010",
    "10000010110011110010100011001",
    "10111010001100001010111110101",
    "10111010111001000001110011010",
    "10111010101101000110011100101",
    "10000010011100110110100101010",
    "11111110111010100111100001010",
]


def draw_qr(surface, x, y, cell):
    quiet = 2
    side = (len(SITE_QR) + 2 * quiet) * cell
    surface.fill((255, 255, 255), (x, y, side, side))
    for r, row in enumerate(SITE_QR):
        for c, bit in enumerate(row):
            if bit == "1":
                surface.fill((0, 0, 0), (x + (c + quiet) * cell, y + (r + quiet) * cell, cell, cell))
    return side


def draw_about(display, accent, ink, dim):
    """Full About screen on the 320x170 display (header and hint row included)."""
    display.header("About")
    cell = 3
    side = (len(SITE_QR) + 4) * cell
    qx = 320 - side - 6
    draw_qr(display.surface, qx, 30, cell)
    width = qx - 16
    display.text(APP_NAME, 10, 30, accent, 17, width)
    lines = ((f"Version {__version__}", dim), (f"{LICENSE} License", dim),
             (f"Developer: {DEVELOPER}", ink), (WEBSITE, ink), (GITHUB, ink))
    for i, (line, color) in enumerate(lines):
        display.text(line, 10, 54 + i * 18, color, 12, width)
    display.text("Scan for the website · Esc back", 10, 149, dim, 12)
