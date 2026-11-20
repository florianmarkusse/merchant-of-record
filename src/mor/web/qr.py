"""A QR code as inline SVG, drawn on the server (docs/plan-payments.md §4):
no image file, no script, no `data:` URL for the CSP to allow. segno
makes the matrix; the SVG is built from htpy elements like any markup of
ours."""

import htpy
import segno


QUIET_ZONE = 4  # modules of light border the QR standard asks for


def qr_svg(text: str, label: str) -> htpy.Node:
    """Dark modules as one path, one unit per module."""
    matrix = segno.make(text, error="m").matrix
    size = len(matrix) + 2 * QUIET_ZONE
    moves: list[str] = []
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                moves.append(f"M{x + QUIET_ZONE},{y + QUIET_ZONE}h1v1h-1z")
    return htpy.svg(
        xmlns="http://www.w3.org/2000/svg",
        viewBox=f"0 0 {size} {size}",
        role="img",
        aria_label=label,
        class_="qr",
        shape_rendering="crispEdges",
    )[
        htpy.rect(width=str(size), height=str(size), fill="#ffffff"),
        htpy.path(d="".join(moves), fill="#000000"),
    ]
