"""A small synthetic market set for tests: SG, TH and FR open, DE in the
EU and closed, one category. Built by text, as the files are."""

HS_HEADER = (
    "country,hs_prefix,charge,origin,agreement,conditions,rate_percent,"
    "effective_from,status,source,source_url,research_ref,checked_on,notes\n"
)
LANDED_HEADER = (
    "country,category,charge,collected,applies,limit_minor,limit_currency,"
    "limit_basis,basis,rate_percent,amount_minor,currency,base,effective_from,"
    "status,source,source_url,research_ref,checked_on,question,notes\n"
)


# EUR per the ECB's way of quoting, plus USD to VND as the SBV quotes it.
FX_ROWS = (
    "EUR,SGD,1.4309,2026-11-06,verified,ECB,,\n"
    "EUR,USD,1.1165,2026-11-06,verified,ECB,,\n"
    "USD,VND,25600,2026-11-10,secondary,SBV,,\n"
)


def market_texts(
    landed_rows: str,
    countries: str = "",
    hs_rows: str = "",
    registrations: str = "",
    fx_rows: str = FX_ROWS,
    duty_sources: str = "",
) -> dict[str, str]:
    """A small synthetic set: SG and TH open, DE in the EU and closed. A
    registration here is made up for the test, never real data."""
    return {
        "categories": "category,label,hs_hint,notes\napparel,Apparel,,\n",
        "countries": (
            "iso2,iso3,name,official_name,region,subregion,currency,"
            "market_status,status_note\n"
            "SG,SGP,Singapore,,Asia,,SGD,open,\n"
            "TH,THA,Thailand,,Asia,,THB,open,\n"
            "DE,DEU,Germany,,Europe,,EUR,conditional,product-safety rep needed\n"
            "FR,FRA,France,,Europe,,EUR,open,\n"
            "VN,VNM,Viet Nam,,Asia,,VND,undecided,origin country\n"
            "CN,CHN,China,,Asia,,CNY,closed,local importer needed\n" + countries
        ),
        "groups": "group,label,kind,members\nEU,European Union,legal,FR DE\n",
        "landed_rates": LANDED_HEADER + landed_rows,
        "registrations": "country,scheme,number,effective_from,notes\n" + registrations,
        "fx_rates": "base,quote,rate,as_of,status,source,source_url,notes\n" + fx_rows,
        "hs_rates": HS_HEADER + hs_rows,
        "duty_sources": "country,mode,vendor,notes\n" + duty_sources,
    } | shipping_texts({})


SHIPPING_HEADERS = {
    "services": (
        "service,carrier,name,origin,tier,ddp,volumetric_divisor,weight_step_kg,"
        "max_weight_kg,status,source,source_url,research_ref,checked_on,question,"
        "notes\n"
    ),
    "rate_cards": (
        "rate_card,service,kind,currency,effective_from,effective_to,received_on,"
        "valid_until,supersedes,volume_per_month,account_required,fuel,vat,status,"
        "source,source_url,research_ref,checked_on,question,notes\n"
    ),
    "zones": "rate_card,destination,zone,status,notes\n",
    "rates": "rate_card,zone,weight_kg,amount_minor,amount_max_minor,ack,notes\n",
    "surcharges": (
        "service,rate_card,surcharge,basis,rate_percent,amount_minor,"
        "min_amount_minor,currency,trigger,destinations,fuel_applies,applies_when,"
        "valid_until,status,source,source_url,research_ref,checked_on,question,"
        "notes\n"
    ),
    "transit": (
        "service,destination,days_min,days_max,status,source,research_ref,"
        "checked_on,notes\n"
    ),
    "service_restrictions": (
        "service,category,rule,status,research_ref,checked_on,notes\n"
    ),
    "shipping_routes": "destination,service,rate_card,source,standing,notes\n",
    "fees": (
        "component,country,percent,fixed_minor,currency,effective_from,standing,"
        "source,notes\n"
    ),
}


def shipping_texts(rows: dict[str, str]) -> dict[str, str]:
    """The shipping and fee files: each header plus the rows given for it."""
    texts: dict[str, str] = {}
    for name, header in SHIPPING_HEADERS.items():
        texts[name] = header + rows.get(name, "")
    return texts


SG_GST = (
    "SG,,import_tax,seller,up_to,40000,SGD,goods,percent,9,,,goods_freight_duty,,"
    "verified,IRAS,https://example.test/sg,,,,\n"
    "SG,,import_tax,border,above,40000,SGD,goods,percent,9,,,goods_freight_duty,,"
    "verified,IRAS,https://example.test/sg,,,,\n"
)

# Thailand: VAT and duty at the border on every order, as the research has it.
TH_BORDER = (
    "TH,,import_tax,border,always,,,,percent,7,,,goods_freight_duty,,"
    "secondary,T&G,https://example.test/th,,,,\n"
    "TH,apparel,duty,border,always,,,,percent,30,,,goods_plus_freight,,"
    "secondary,WTO,https://example.test/th,,,,\n"
)
