"""The staff Classifications page (docs/plan-shipments.md §4): our HS code
per merchant SKU, set before the SKU is sold. A form sets a SKU's code,
new or changed; the codes in force are listed per merchant. A change
never touches lines already sold.

    GET  /staff/classifications     the form and the codes in force
    POST /staff/classifications     {"merchant", "sku", "hs_code"}

Mounted outside prod only until staff sign-in exists, as the other staff
pages; until then the actor is this page, not a person.
"""

from datetime import datetime
from urllib.parse import urlencode

import htpy
import msgspec
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mor.audit import Actor, ActorKind
from mor.dates import format_moment
from mor.db.connect import connect
from mor.ids import MerchantSku
from mor.merchants.classifications import (
    SkuClassification,
    classifications_in_force,
    classify,
    parse_our_code,
)
from mor.merchants.records import Merchant
from mor.merchants.store import merchants, read_merchant
from mor.public_ids import merchant_public_id, parse_merchant_id
from mor.web.forms import read_form, same_origin
from mor.web.staff_layout import staff_page


PAGE = "/staff/classifications"
PERSON = Actor(ActorKind.STAFF, "staff-classifications-page")
MAX_SKU = 200


class ClassifyForm(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    merchant: str
    sku: str
    hs_code: str


async def page(request: Request, database_url: str) -> Response:
    async with await connect(database_url) as conn:
        sellers = await merchants(conn)
        in_force: list[tuple[Merchant, dict[MerchantSku, SkuClassification]]] = []
        for seller in sellers:
            in_force.append((seller, await classifications_in_force(conn, seller.id)))
    note = request.query_params.get("note")
    return HTMLResponse(render(in_force, note))


def _back(note: str) -> Response:
    return RedirectResponse(f"{PAGE}?{urlencode({'note': note})}", status_code=303)


async def set_code(request: Request, database_url: str, now: datetime) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    form = await read_form(request, ClassifyForm)
    if form is None:
        return Response("bad form", status_code=400)
    merchant_id = parse_merchant_id(form.merchant)
    if merchant_id is None:
        return Response("bad form", status_code=400)
    sku = form.sku.strip()
    if not sku or len(sku) > MAX_SKU:
        return _back("not classified: give the SKU as the merchant sends it")
    hs_code = parse_our_code(form.hs_code)
    if hs_code is None:
        return _back("not classified: an HS code is 6 to 10 digits")
    async with await connect(database_url) as conn:
        if await read_merchant(conn, merchant_id) is None:
            return Response("no such merchant", status_code=404)
        await classify(conn, merchant_id, MerchantSku(sku), hs_code, PERSON, now)
        await conn.commit()
    return _back(f"{sku} is now {hs_code}")


def _form(sellers: list[Merchant]) -> htpy.Node:
    options: list[htpy.Node] = []
    for seller in sellers:
        options.append(
            htpy.option(value=merchant_public_id(seller.id))[seller.display_name]
        )
    return htpy.form(method="post", action=PAGE)[
        htpy.label["Merchant ", htpy.select(name="merchant")[options]],
        " ",
        htpy.label[
            "SKU ",
            htpy.input(type="text", name="sku", required=True, maxlength=MAX_SKU),
        ],
        " ",
        htpy.label[
            "HS code ",
            htpy.input(
                type="text",
                name="hs_code",
                required=True,
                inputmode="numeric",
                pattern="[0-9. ]{6,14}",
            ),
        ],
        " ",
        htpy.button(type="submit")["Classify"],
    ]


def _table(classifications: dict[MerchantSku, SkuClassification]) -> htpy.Node:
    if not classifications:
        return htpy.p(class_="muted")["No SKU classified yet."]
    rows: list[htpy.Node] = []
    for sku in sorted(classifications):
        classification = classifications[sku]
        rows.append(
            htpy.tr[
                htpy.td[sku],
                htpy.td[classification.hs_code],
                htpy.td[format_moment(classification.created_at)],
                htpy.td[classification.actor_id],
            ]
        )
    return htpy.div(class_="scroll")[
        htpy.table(class_="data")[
            htpy.thead[
                htpy.tr[
                    htpy.th["SKU"],
                    htpy.th["HS code"],
                    htpy.th["Since"],
                    htpy.th["By"],
                ]
            ],
            htpy.tbody[rows],
        ]
    ]


def render(
    in_force: list[tuple[Merchant, dict[MerchantSku, SkuClassification]]],
    note: str | None,
) -> str:
    sellers: list[Merchant] = []
    sections: list[htpy.Node] = []
    for seller, classifications in in_force:
        sellers.append(seller)
        sections.append(
            htpy.section[
                htpy.h2[f"{seller.display_name} ({merchant_public_id(seller.id)})"],
                _table(classifications),
            ]
        )
    return staff_page(
        "Classifications",
        PAGE,
        ("table_filter.js",),
        htpy.h1["Classifications"],
        htpy.p(role="status")[htpy.strong[note]] if note else None,
        htpy.p(class_="muted")[
            "Our HS code for each merchant SKU. A line is sold only under a"
            " SKU classified here; the merchant's own code is a hint. A new"
            " code applies to sessions from now on; lines already sold keep"
            " theirs."
        ],
        _form(sellers) if sellers else htpy.p(class_="muted")["No merchants yet."],
        sections,
    )
