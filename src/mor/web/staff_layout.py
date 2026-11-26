"""The frame of every staff page: the head, the menu and the stylesheet
(`static/css/staff.css`). Scripts are TypeScript in `ts/`, compiled into
`static/js/`; each page names the ones it improves itself with, and works
without them."""

from datetime import date
from pathlib import Path

import htpy

from mor.dates import format_day
from mor.money.amount import Money, format_major


STATIC_DIR = Path(__file__).resolve().parents[3] / "static"

# The staff menu: its address and label, in order.
MENU = (
    ("/staff/markets", "Quote"),
    ("/staff/markets/answers", "Vendor answers"),
    ("/staff/markets/data", "Market data"),
    ("/staff/markets/sessions", "Sessions"),
    ("/staff/classifications", "Classifications"),
    ("/staff/shipments", "Shipments"),
    ("/staff/settlement", "Settlement"),
    ("/staff/bank", "Bank"),
)


def static_url(path: str) -> str:
    """A file under static/, tagged with when it last changed so a browser
    fetches the new version after an edit or a build. `path` is relative to
    static/, e.g. "css/staff.css"."""
    try:
        changed = int((STATIC_DIR / path).stat().st_mtime)
    except OSError:
        changed = 0
    return f"/static/{path}?v={changed}"


def staff_page(
    title: str, current: str, scripts: tuple[str, ...], *content: htpy.Node
) -> str:
    """A complete page. `current` is the menu address of this page;
    `scripts` are paths under static/js/."""
    links: list[htpy.Node] = []
    for href, label in MENU:
        if href == current:
            links.append(htpy.a(href=href, aria_current="page")[label])
        else:
            links.append(htpy.a(href=href)[label])
    tags: list[htpy.Node] = []
    for script in scripts:
        tags.append(htpy.script(src=static_url(f"js/{script}"), type="module"))
    page = htpy.html(lang="en")[
        htpy.head[
            htpy.meta(charset="utf-8"),
            htpy.meta(name="viewport", content="width=device-width, initial-scale=1"),
            htpy.title[f"{title} · Staff"],
            htpy.link(rel="stylesheet", href=static_url("css/staff.css")),
            htpy.link(rel="icon", href=static_url("staff.svg"), type="image/svg+xml"),
            tags,
        ],
        htpy.body[
            htpy.header(class_="top")[
                htpy.span(class_="brand")["MoR staff"],
                htpy.nav[links],
            ],
            htpy.main[content],
        ],
    ]
    return str(page)


def money_text(money: Money) -> str:
    """An amount for people: digits grouped by thousands, then the currency
    ("7,430.02 THB")."""
    plain = format_major(money)
    sign = ""
    if plain.startswith("-"):
        sign, plain = "-", plain[1:]
    whole, dot, fraction = plain.partition(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{sign}{','.join(groups)}{dot}{fraction} {money.currency.value}"


def day_input(day: date, name: str = "on") -> htpy.Element:
    """A date picker with the day beside it as people read it (18-Nov-2026).
    The picker itself is drawn in the browser's locale, which a page cannot
    change, and sends ISO 8601. The day shown is rendered here, so pages
    that update live (`data-live`) swap it in with the rest."""
    return htpy.span(class_="day")[
        htpy.input(type="date", name=name, value=day.isoformat()),
        htpy.span(id=f"{name}-day", class_="day-text", data_live="")[format_day(day)],
    ]
