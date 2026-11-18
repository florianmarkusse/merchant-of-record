"""A small synthetic shipping set for quote tests (docs/plan-quote.md §4):
an express card in SGD that is duty-paid and adds fuel, and an economy
card in VND that is not duty-paid and includes fuel, both into SG and TH.
Built by text, as the files are; every number is made up."""

from mor.markets.records import MarketSet
from mor.markets.source import parse
from tests.markets.fixtures import (
    FX_ROWS,
    SG_GST,
    TH_BORDER,
    market_texts,
    shipping_texts,
)


TH_HS = "TH,6109,duty,,,,30,,secondary,WTO,https://example.test,,,\n"
FX = FX_ROWS + "EUR,THB,37.459,2026-11-06,verified,ECB,,\n"

SHIPPING = {
    "services": (
        "exp,carrier,Express,VN,express,yes,5000,0.5,30,verified,,,,,,\n"
        "eco,carrier,Economy,VN,economy,no,5000,0.5,2,verified,,,,,,\n"
    ),
    "rate_cards": (
        "exp_card,exp,list,SGD,2026-01-01,,,,,,no,extra,excluded,verified,"
        "made up,,,,,\n"
        "eco_card,eco,list,VND,2026-01-01,,,,,,no,included,excluded,verified,"
        "made up,,,,,\n"
    ),
    "zones": (
        "exp_card,SG,A,verified,\n"
        "exp_card,TH,A,verified,\n"
        "eco_card,SG,1,verified,\n"
        "eco_card,TH,1,verified,\n"
    ),
    "rates": (
        "exp_card,A,0.5,2000,,,\n"
        "exp_card,A,1,3000,,,\n"
        "exp_card,A,2,4500,,,\n"
        "eco_card,1,0.5,100000,,,\n"
        "eco_card,1,1,150000,,,\n"
        "eco_card,1,2,250000,,,\n"
    ),
    # Lines 2-5: fuel (weekly, last day 15-Nov-2026), a demand surcharge
    # per kg that fuel applies to, the duty-paid fee with its minimum, and
    # a remote-area surcharge that is only charged on an event.
    "surcharges": (
        "exp,,fuel,percent_of_freight,50,,,SGD,always,,no,,2026-11-15,"
        "verified,made up,,,,,\n"
        "exp,,demand,per_kg,,100,,SGD,always,SG TH,yes,,,verified,made up,,,,,\n"
        "exp,,duty_tax_paid,percent_of_duties,2,,1000,SGD,duties_paid,,no,,,"
        "verified,made up,,,,,\n"
        "exp,,remote_area,per_shipment,,2000,,SGD,on_event,,yes,,,"
        "verified,made up,,,,,\n"
    ),
    "transit": (
        "exp,SG,1,3,verified,,,,\nexp,TH,1,3,verified,,,,\neco,SG,5,8,verified,,,,\n"
    ),
    "shipping_routes": (
        "SG,exp_card,own_table,placeholder,\n"
        "TH,exp_card,own_table,placeholder,\n"
        "SG,eco_card,own_table,agreed,\n"
        "TH,eco_card,own_table,agreed,\n"
    ),
    "fees": (
        "psp_fee,,3,,,2026-01-01,placeholder,made up,\n"
        "fx,,0,,,2026-01-01,agreed,made up,\n"
        "mor_margin,,5,,,2026-01-01,placeholder,made up,\n"
    ),
}


def quote_market(
    shipping: dict[str, str] | None = None, duty_sources: str = ""
) -> MarketSet:
    """The set above; `shipping` replaces whole files of it."""
    files = SHIPPING | (shipping or {})
    market = parse(
        market_texts(
            SG_GST + TH_BORDER,
            hs_rows=TH_HS,
            fx_rows=FX,
            duty_sources=duty_sources,
        )
        | shipping_texts(files)
    )
    assert not isinstance(market, list), market
    return market
