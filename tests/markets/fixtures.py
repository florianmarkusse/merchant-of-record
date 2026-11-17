"""A small synthetic market set for tests: SG, TH and FR open, DE in the
EU and closed, one category. Built by text, as the files are."""

LANDED_HEADER = (
    "country,category,charge,collected,applies,limit_minor,limit_currency,"
    "limit_basis,basis,rate_percent,amount_minor,currency,base,effective_from,"
    "status,source,source_url,research_ref,checked_on,question,notes\n"
)


def market_texts(landed_rows: str, countries: str = "") -> dict[str, str]:
    """A small synthetic set: SG and TH open, DE in the EU and closed."""
    return {
        "categories": "category,label,hs_hint,notes\napparel,Apparel,,\n",
        "countries": (
            "iso2,iso3,name,official_name,region,subregion,currency,"
            "market_status,status_note\n"
            "SG,SGP,Singapore,,Asia,,SGD,open,\n"
            "TH,THA,Thailand,,Asia,,THB,open,\n"
            "DE,DEU,Germany,,Europe,,EUR,conditional,product-safety rep needed\n"
            "FR,FRA,France,,Europe,,EUR,open,\n" + countries
        ),
        "groups": "group,label,kind,members\nEU,European Union,legal,FR DE\n",
        "landed_rates": LANDED_HEADER + landed_rows,
        "registrations": "country,scheme,number,effective_from,notes\n",
        "fx_rates": (
            "base,quote,rate,as_of,status,source,source_url,notes\n"
            "EUR,SGD,1.4309,2026-11-06,verified,ECB,,\n"
        ),
    }


SG_GST = (
    "SG,,import_tax,seller,up_to,40000,SGD,goods,percent,9,,,goods_freight_duty,,"
    "verified,IRAS,https://example.test/sg,,,,\n"
    "SG,,import_tax,border,above,40000,SGD,goods,percent,9,,,goods_freight_duty,,"
    "verified,IRAS,https://example.test/sg,,,,\n"
)
