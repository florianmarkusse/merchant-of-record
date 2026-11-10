# /// script
# requires-python = ">=3.14"
# dependencies = ["htpy==25.8.1", "openpyxl==3.1.5"]
# ///
# pyright: strict
"""Check the markets research tables and build their views.

    uv run scripts/markets.py check
    uv run scripts/markets.py build

The CSV files in docs/markets/ are the source of truth (docs/plan-markets-data.md).
`build` writes docs/markets.html and docs/markets/out/markets.xlsx from them.
"""

import csv
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from fractions import Fraction
from pathlib import Path
from typing import Literal, assert_never

import htpy as h
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "markets"
HTML_OUT = ROOT / "docs" / "markets.html"
XLSX_OUT = DATA / "out" / "markets.xlsx"

type Row = dict[str, str]
type Table = list[Row]

SCHEMA: dict[str, tuple[str, ...]] = {
    "countries": (
        "iso2",
        "iso3",
        "name",
        "official_name",
        "region",
        "subregion",
        "currency",
        "market_status",
        "status_note",
    ),
    "groups": ("group", "label", "kind", "members"),
    "categories": ("category", "label", "hs_hint", "notes"),
    "topics": ("topic", "group", "label", "description"),
    "facts": (
        "country",
        "topic",
        "category",
        "origin",
        "value",
        "status",
        "source",
        "source_url",
        "research_ref",
        "checked_on",
        "question",
        "notes",
    ),
    "questions": (
        "question",
        "kind",
        "providers",
        "to",
        "subject",
        "text",
        "priority",
        "due_on",
        "blocked_by",
        "status",
        "sent_on",
        "answered_on",
        "answer",
        "research_ref",
    ),
    "providers": ("provider", "name", "kind", "website", "relationship", "notes"),
    "services": (
        "service",
        "provider",
        "name",
        "origin",
        "tier",
        "ddp",
        "volumetric_divisor",
        "weight_step_kg",
        "max_weight_kg",
        "status",
        "source",
        "source_url",
        "research_ref",
        "checked_on",
        "question",
        "notes",
    ),
    "rate_cards": (
        "rate_card",
        "service",
        "kind",
        "currency",
        "effective_from",
        "effective_to",
        "excludes",
        "status",
        "source",
        "source_url",
        "research_ref",
        "checked_on",
        "question",
        "notes",
    ),
    "zones": ("rate_card", "destination", "zone", "notes"),
    "rates": (
        "rate_card",
        "zone",
        "weight_kg",
        "amount_minor",
        "amount_max_minor",
        "notes",
    ),
    "surcharges": (
        "service",
        "surcharge",
        "basis",
        "rate_percent",
        "amount_minor",
        "min_amount_minor",
        "currency",
        "trigger",
        "destinations",
        "fuel_applies",
        "applies_when",
        "status",
        "source",
        "source_url",
        "research_ref",
        "checked_on",
        "question",
        "notes",
    ),
    "transit": (
        "service",
        "destination",
        "days_min",
        "days_max",
        "status",
        "source",
        "research_ref",
        "checked_on",
        "notes",
    ),
    "service_restrictions": (
        "service",
        "category",
        "rule",
        "status",
        "research_ref",
        "checked_on",
        "notes",
    ),
    "scenarios": (
        "scenario",
        "label",
        "weight_kg",
        "goods_value_minor",
        "currency",
        "category",
        "hs_lines",
        "notes",
    ),
    "border_charges": (
        "country",
        "category",
        "rule",
        "above_minor",
        "currency",
        "basis",
        "status",
        "source",
        "source_url",
        "research_ref",
        "checked_on",
        "question",
        "notes",
    ),
}

type Status = Literal[
    "verified",
    "secondary",
    "estimate",
    "unverified",
    "to_fetch",
    "awaiting_answer",
    "n/a",
]
STATUSES: tuple[Status, ...] = (
    "verified",
    "secondary",
    "estimate",
    "unverified",
    "to_fetch",
    "awaiting_answer",
    "n/a",
)
STATUS_LABEL: dict[Status, str] = {
    "verified": "Verified (primary source)",
    "secondary": "Secondary source",
    "estimate": "Estimate or rating",
    "unverified": "Unverified or conflicting",
    "to_fetch": "To look up",
    "awaiting_answer": "Waiting on a question",
    "n/a": "Does not apply",
}
# Fill colours for the workbook (light theme only; Sheets has no dark mode for fills).
STATUS_FILL: dict[Status, str] = {
    "verified": "C8E6C9",
    "secondary": "E3F2D9",
    "estimate": "FFF3C4",
    "unverified": "FFD8A8",
    "to_fetch": "D6E4FF",
    "awaiting_answer": "E5D4FF",
    "n/a": "EEEEEE",
}

MARKET_STATUSES = ("open", "conditional", "closed", "undecided", "not_assessed")
QUESTION_KINDS = ("email", "adviser", "fetch")
QUESTION_STATUSES = ("draft", "sent", "answered", "dropped")
OPEN_QUESTION_STATUSES = ("draft", "sent")
# How soon an open question must be settled, most urgent first.
PRIORITIES = ("now", "before_launch", "later")
PRIORITY_LABEL: dict[str, str] = {
    "now": "Now",
    "before_launch": "Before launch",
    "later": "Later",
}
RELATIONSHIPS = (
    "none",
    "rfq_planned",
    "rfq_sent",
    "quoted",
    "account_planned",
    "account_open",
    "contracted",
    "not_pursued",
    "n/a",
)
RATE_CARD_KINDS = ("list", "promo", "quote", "contract", "estimate")
SURCHARGE_BASES = (
    "percent_of_freight",
    "percent_of_duties",
    "percent_of_value",
    "per_shipment",
    "per_piece",
    "per_kg",
    "per_line",
)
RULES = ("allowed", "restricted", "prohibited", "unknown")
# When a surcharge is charged (docs/plan-markets-data.md, all-in cost).
TRIGGERS = ("always", "duties_paid", "passthrough", "optional", "on_event")
FUEL_APPLIES = ("yes", "no", "unknown")
BORDER_RULES = ("always", "above", "never", "unknown")
BORDER_BASES = ("goods", "goods_plus_freight")

# Minor-unit exponents for the currencies the tables use
# (AGENTS.md: one table, never "divide by 100"; IDR 0 as decided there).
EXPONENT: dict[str, int] = {
    "USD": 2,
    "VND": 0,
    "EUR": 2,
    "SGD": 2,
    "MYR": 2,
    "THB": 2,
    "PHP": 2,
    "IDR": 0,
    "JPY": 0,
    "KRW": 0,
    "AUD": 2,
    "NZD": 2,
    "CAD": 2,
    "GBP": 2,
    "CHF": 2,
}
# ECB euro reference rates of 2026-11-06 (units per EUR), for display only.
ECB_PER_EUR: dict[str, str] = {
    "USD": "1.1165",
    "JPY": "176.77",
    "GBP": "0.83415",
    "AUD": "1.6250",
    "SGD": "1.4309",
    "MYR": "4.5319",
    "PHP": "70.358",
    "IDR": "20208.93",
    "THB": "37.459",
    "KRW": "1517.02",
    "CHF": "0.9143",
    "NZD": "2.0141",
    "CAD": "1.5705",
}


def usd_per_unit() -> dict[str, Fraction]:
    rates: dict[str, Fraction] = {"USD": Fraction(1), "VND": Fraction(1, 26_300)}
    usd_per_eur = Fraction(ECB_PER_EUR["USD"])
    rates["EUR"] = usd_per_eur
    for currency, per_eur in ECB_PER_EUR.items():
        if currency != "USD":
            rates[currency] = usd_per_eur / Fraction(per_eur)
    return rates


# Display-only conversion to compare cards; the page shows the rate and source.
USD_PER_UNIT: dict[str, Fraction] = usd_per_unit()
USD_RATE_NOTE = (
    "VND converted at 26,300 per US$ (research/logistics/02, estimate), other "
    "currencies at the ECB reference rates of 2026-11-06, "
    "rounded half up to whole dollars"
)

OVERVIEW_TOPICS: tuple[str, ...] = (
    "ease",
    "duty_free_limit",
    "tax_free_limit",
    "import_tax",
    "seller_scheme",
    "registration_threshold",
    "buyer_data",
    "product_safety_rep",
    "withdrawal_right",
    "duties_on_refund",
)
ARGC = 2  # program name and command
COMPARE_WEIGHTS: tuple[str, ...] = ("0.5", "1", "2", "3", "5", "10", "20")

ISO2 = re.compile(r"^[A-Z]{2}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INTEGER = re.compile(r"^-?\d+$")
DECIMAL = re.compile(r"^\d+(\.\d+)?$")


@dataclass(frozen=True)
class Data:
    tables: dict[str, Table]

    def rows(self, name: str) -> Table:
        return self.tables[name]


@dataclass(frozen=True)
class Resolved:
    fact: Row
    via: str  # "" when set on the country itself, else the group code


type FactKey = tuple[str, str, str]  # topic, category, origin


# ---------------------------------------------------------------- loading


def load() -> tuple[Data, list[str]]:
    errors: list[str] = []
    tables: dict[str, Table] = {}
    for name, columns in SCHEMA.items():
        path = DATA / f"{name}.csv"
        if not path.exists():
            errors.append(f"{name}.csv: missing")
            tables[name] = []
            continue
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            header = tuple(reader.fieldnames or ())
            if header != columns:
                errors.append(f"{name}.csv: columns are {header}, expected {columns}")
            rows: Table = []
            for raw in reader:
                row: Row = {}
                for column in columns:
                    value = raw.get(column)
                    row[column] = value.strip() if isinstance(value, str) else ""
                rows.append(row)
            tables[name] = rows
    return Data(tables), errors


# ---------------------------------------------------------------- checks


@dataclass(frozen=True)
class Known:
    """The ids each table may refer to."""

    iso: frozenset[str]
    groups: dict[str, list[str]]
    places: frozenset[str]
    topics: frozenset[str]
    categories: frozenset[str]
    questions: frozenset[str]
    providers: frozenset[str]
    services: frozenset[str]
    cards: frozenset[str]


def where(table: str, index: int) -> str:
    return f"{table}.csv line {index + 2}"


def ids(data: Data, table: str, column: str) -> frozenset[str]:
    return frozenset(row[column] for row in data.rows(table))


def known_ids(data: Data) -> Known:
    iso = ids(data, "countries", "iso2")
    groups = group_members(data)
    return Known(
        iso=iso,
        groups=groups,
        places=iso | frozenset(groups),
        topics=ids(data, "topics", "topic"),
        categories=ids(data, "categories", "category"),
        questions=ids(data, "questions", "question"),
        providers=ids(data, "providers", "provider"),
        services=ids(data, "services", "service"),
        cards=ids(data, "rate_cards", "rate_card"),
    )


def check(data: Data) -> list[str]:
    known = known_ids(data)
    errors: list[str] = []
    errors.extend(check_countries(data))
    errors.extend(check_groups(data, known))
    errors.extend(check_questions(data, known))
    errors.extend(check_providers(data))
    errors.extend(check_facts(data, known))
    errors.extend(check_inheritance(data, known))
    errors.extend(check_services(data, known))
    errors.extend(check_rate_cards(data, known))
    errors.extend(check_zones_and_rates(data, known))
    errors.extend(check_surcharges(data, known))
    errors.extend(check_transit(data, known))
    errors.extend(check_restrictions(data, known))
    errors.extend(check_scenarios(data, known))
    errors.extend(check_border_charges(data, known))
    return errors


def one_of(
    table: str, i: int, row: Row, column: str, allowed: Iterable[str]
) -> list[str]:
    values = tuple(allowed)
    if row[column] in values:
        return []
    return [f"{where(table, i)}: {column} {row[column]!r} not in {values}"]


def known_ref(
    table: str, i: int, row: Row, column: str, ok: frozenset[str]
) -> list[str]:
    if row[column] in ok:
        return []
    return [f"{where(table, i)}: unknown {column} {row[column]!r}"]


def pattern(
    table: str, i: int, row: Row, columns: Iterable[str], regex: re.Pattern[str]
) -> list[str]:
    errors: list[str] = []
    for column in columns:
        if row[column] and not regex.match(row[column]):
            errors.append(f"{where(table, i)}: {column} {row[column]!r} is malformed")
    return errors


def check_status_row(table: str, i: int, row: Row, known: Known) -> list[str]:
    """Status, question link and checked_on, shared by every sourced table."""
    errors = one_of(table, i, row, "status", STATUSES)
    question = row.get("question", "")
    if question and question not in known.questions:
        errors.append(f"{where(table, i)}: unknown question {question}")
    if row["status"] == "awaiting_answer" and "question" in row and not question:
        errors.append(f"{where(table, i)}: awaiting_answer needs a question")
    errors.extend(check_dates(table, i, row, ("checked_on",)))
    return errors


def check_countries(data: Data) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(data.rows("countries")):
        if not ISO2.match(row["iso2"]):
            errors.append(f"{where('countries', i)}: bad iso2 {row['iso2']!r}")
        if row["iso2"] in seen:
            errors.append(f"{where('countries', i)}: duplicate iso2 {row['iso2']}")
        seen.add(row["iso2"])
        errors.extend(one_of("countries", i, row, "market_status", MARKET_STATUSES))
    return errors


def check_groups(data: Data, known: Known) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(data.rows("groups")):
        for member in row["members"].split():
            if member not in known.iso:
                errors.append(f"{where('groups', i)}: unknown member {member}")
        if row["group"] in known.iso or row["group"] in seen:
            errors.append(f"{where('groups', i)}: group code {row['group']} clashes")
        seen.add(row["group"])
    return errors


def check_questions(data: Data, known: Known) -> list[str]:
    errors: list[str] = []
    for i, row in enumerate(data.rows("questions")):
        here = where("questions", i)
        errors.extend(one_of("questions", i, row, "kind", QUESTION_KINDS))
        errors.extend(one_of("questions", i, row, "status", QUESTION_STATUSES))
        if row["status"] == "answered" and not row["answer"]:
            errors.append(f"{here}: answered without an answer")
        if row["status"] == "sent" and not row["sent_on"]:
            errors.append(f"{here}: sent without sent_on")
        # Open questions need a priority; closed ones may keep theirs.
        if row["status"] in OPEN_QUESTION_STATUSES or row["priority"]:
            errors.extend(one_of("questions", i, row, "priority", PRIORITIES))
        for provider in row["providers"].split():
            if provider not in known.providers:
                errors.append(f"{here}: unknown provider {provider}")
        dates = ("sent_on", "answered_on", "due_on")
        errors.extend(check_dates("questions", i, row, dates))
    return errors


def check_providers(data: Data) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(data.rows("providers")):
        errors.extend(one_of("providers", i, row, "relationship", RELATIONSHIPS))
        if row["provider"] in seen:
            errors.append(f"{where('providers', i)}: duplicate {row['provider']}")
        seen.add(row["provider"])
    return errors


def check_facts(data: Data, known: Known) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()
    for i, row in enumerate(data.rows("facts")):
        here = where("facts", i)
        errors.extend(check_status_row("facts", i, row, known))
        errors.extend(known_ref("facts", i, row, "country", known.places))
        errors.extend(known_ref("facts", i, row, "topic", known.topics))
        if row["category"]:
            errors.extend(known_ref("facts", i, row, "category", known.categories))
        if row["origin"]:
            errors.extend(known_ref("facts", i, row, "origin", known.iso))
        sourced = row["source"] or row["source_url"] or row["research_ref"]
        if not (sourced or row["question"]):
            errors.append(f"{here}: no source, research_ref or question")
        if not row["value"] and row["status"] not in ("to_fetch", "awaiting_answer"):
            errors.append(f"{here}: empty value with status {row['status']}")
        key = (row["country"], row["topic"], row["category"], row["origin"])
        if key in seen:
            errors.append(f"{here}: duplicate fact {key}")
        seen.add(key)
    return errors


def check_inheritance(data: Data, known: Known) -> list[str]:
    """A country may inherit one fact from one group only."""
    errors: list[str] = []
    for code in sorted(known.iso):
        owners: dict[FactKey, list[str]] = {}
        for row in data.rows("facts"):
            group = row["country"]
            if group in known.groups and code in known.groups[group]:
                key = (row["topic"], row["category"], row["origin"])
                by = owners.setdefault(key, [])
                if group not in by:
                    by.append(group)
        for key, by in owners.items():
            if len(by) > 1:
                errors.append(
                    f"facts: {code} inherits {key} from several groups {by}; "
                    f"set it on {code}"
                )
    return errors


def check_services(data: Data, known: Known) -> list[str]:
    errors: list[str] = []
    for i, row in enumerate(data.rows("services")):
        errors.extend(check_status_row("services", i, row, known))
        errors.extend(known_ref("services", i, row, "provider", known.providers))
        errors.extend(known_ref("services", i, row, "origin", known.iso))
        errors.extend(pattern("services", i, row, ("volumetric_divisor",), INTEGER))
        weights = ("weight_step_kg", "max_weight_kg")
        errors.extend(pattern("services", i, row, weights, DECIMAL))
    return errors


def check_rate_cards(data: Data, known: Known) -> list[str]:
    errors: list[str] = []
    for i, row in enumerate(data.rows("rate_cards")):
        errors.extend(check_status_row("rate_cards", i, row, known))
        errors.extend(known_ref("rate_cards", i, row, "service", known.services))
        errors.extend(one_of("rate_cards", i, row, "kind", RATE_CARD_KINDS))
        errors.extend(one_of("rate_cards", i, row, "currency", EXPONENT))
        dates = ("effective_from", "effective_to")
        errors.extend(check_dates("rate_cards", i, row, dates))
    return errors


def check_zones_and_rates(data: Data, known: Known) -> list[str]:
    errors: list[str] = []
    card_zones: dict[str, set[str]] = {}
    for i, row in enumerate(data.rows("zones")):
        errors.extend(known_ref("zones", i, row, "rate_card", known.cards))
        errors.extend(known_ref("zones", i, row, "destination", known.places))
        card_zones.setdefault(row["rate_card"], set()).add(row["zone"])

    seen: set[tuple[str, str, str]] = set()
    for i, row in enumerate(data.rows("rates")):
        here = where("rates", i)
        card = row["rate_card"]
        errors.extend(known_ref("rates", i, row, "rate_card", known.cards))
        if card in known.cards and row["zone"] not in card_zones.get(card, set()):
            errors.append(f"{here}: zone {row['zone']} of {card} has no destinations")
        if not DECIMAL.match(row["weight_kg"]):
            errors.append(f"{here}: weight_kg must be a number")
        amounts = ("amount_minor", "amount_max_minor")
        errors.extend(pattern("rates", i, row, amounts, INTEGER))
        if not row["amount_minor"]:
            errors.append(f"{here}: empty amount_minor")
        key = (card, row["zone"], row["weight_kg"])
        if key in seen:
            errors.append(f"{here}: duplicate rate {key}")
        seen.add(key)
    return errors


def check_surcharges(data: Data, known: Known) -> list[str]:
    errors: list[str] = []
    for i, row in enumerate(data.rows("surcharges")):
        errors.extend(check_status_row("surcharges", i, row, known))
        errors.extend(known_ref("surcharges", i, row, "service", known.services))
        errors.extend(one_of("surcharges", i, row, "basis", SURCHARGE_BASES))
        errors.extend(one_of("surcharges", i, row, "currency", EXPONENT))
        amounts = ("amount_minor", "min_amount_minor")
        errors.extend(pattern("surcharges", i, row, amounts, INTEGER))
        errors.extend(pattern("surcharges", i, row, ("rate_percent",), DECIMAL))
        errors.extend(one_of("surcharges", i, row, "trigger", TRIGGERS))
        errors.extend(one_of("surcharges", i, row, "fuel_applies", FUEL_APPLIES))
        for place in row["destinations"].split():
            if place not in known.places:
                errors.append(f"{where('surcharges', i)}: unknown destination {place}")
    return errors


def check_transit(data: Data, known: Known) -> list[str]:
    errors: list[str] = []
    for i, row in enumerate(data.rows("transit")):
        errors.extend(check_status_row("transit", i, row, known))
        errors.extend(known_ref("transit", i, row, "service", known.services))
        errors.extend(known_ref("transit", i, row, "destination", known.places))
        for column in ("days_min", "days_max"):
            if not INTEGER.match(row[column]):
                errors.append(f"{where('transit', i)}: {column} must be an integer")
    return errors


def check_restrictions(data: Data, known: Known) -> list[str]:
    table = "service_restrictions"
    errors: list[str] = []
    for i, row in enumerate(data.rows(table)):
        errors.extend(check_status_row(table, i, row, known))
        errors.extend(known_ref(table, i, row, "service", known.services))
        errors.extend(known_ref(table, i, row, "category", known.categories))
        errors.extend(one_of(table, i, row, "rule", RULES))
    return errors


def check_dates(table: str, i: int, row: Row, columns: Iterable[str]) -> list[str]:
    errors: list[str] = []
    for column in columns:
        value = row[column]
        if not value:
            continue
        if not DATE.match(value):
            errors.append(f"{where(table, i)}: {column} {value!r} is not YYYY-MM-DD")
            continue
        try:
            date.fromisoformat(value)
        except ValueError:
            errors.append(f"{where(table, i)}: {column} {value!r} is not a real date")
    return errors


# ---------------------------------------------------------------- derived views


def check_scenarios(data: Data, known: Known) -> list[str]:
    errors: list[str] = []
    for i, row in enumerate(data.rows("scenarios")):
        here = where("scenarios", i)
        if not DECIMAL.match(row["weight_kg"]):
            errors.append(f"{here}: weight_kg must be a number")
        for column in ("goods_value_minor", "hs_lines"):
            if not INTEGER.match(row[column]):
                errors.append(f"{here}: {column} must be an integer")
        errors.extend(one_of("scenarios", i, row, "currency", EXPONENT))
        if row["category"]:
            errors.extend(known_ref("scenarios", i, row, "category", known.categories))
    return errors


def check_border_charges(data: Data, known: Known) -> list[str]:
    table = "border_charges"
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for i, row in enumerate(data.rows(table)):
        here = where(table, i)
        errors.extend(check_status_row(table, i, row, known))
        errors.extend(known_ref(table, i, row, "country", known.places))
        if row["category"]:
            errors.extend(known_ref(table, i, row, "category", known.categories))
        errors.extend(one_of(table, i, row, "rule", BORDER_RULES))
        errors.extend(one_of(table, i, row, "basis", BORDER_BASES))
        if row["rule"] == "above":
            if not INTEGER.match(row["above_minor"]):
                errors.append(f"{here}: rule above needs an integer above_minor")
            errors.extend(one_of(table, i, row, "currency", USD_PER_UNIT))
        elif row["above_minor"]:
            errors.append(f"{here}: above_minor is only for rule above")
        key = (row["country"], row["category"])
        if key in seen:
            errors.append(f"{here}: duplicate border charge {key}")
        seen.add(key)
    return errors


def as_status(value: str) -> Status:
    for status in STATUSES:
        if status == value:
            return status
    raise ValueError(f"unknown status {value!r}; run check first")


def group_members(data: Data) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in data.rows("groups"):
        result[row["group"]] = row["members"].split()
    return result


def groups_of(data: Data, code: str) -> list[str]:
    result: list[str] = []
    for group, members in group_members(data).items():
        if code in members:
            result.append(group)
    return result


def resolve_facts(data: Data, code: str) -> dict[FactKey, Resolved]:
    """Facts for one country: its own rows win over rows set on one of its groups."""
    mine = set(groups_of(data, code))
    result: dict[FactKey, Resolved] = {}
    for row in data.rows("facts"):
        if row["country"] in mine:
            key = (row["topic"], row["category"], row["origin"])
            result.setdefault(key, Resolved(row, row["country"]))
    for row in data.rows("facts"):
        if row["country"] == code:
            result[(row["topic"], row["category"], row["origin"])] = Resolved(row, "")
    return result


def overview_fact(facts: dict[FactKey, Resolved], topic: str) -> Resolved | None:
    """The general (no category, any origin) fact for a topic."""
    return facts.get((topic, "", ""))


def zone_for(data: Data, card: str, code: str) -> str | None:
    mine = groups_of(data, code)
    via_group: str | None = None
    for row in data.rows("zones"):
        if row["rate_card"] != card:
            continue
        if row["destination"] == code:
            return row["zone"]
        if row["destination"] in mine and via_group is None:
            via_group = row["zone"]
    return via_group


def rate_for(data: Data, card: str, zone: str, weight: str) -> Row | None:
    wanted = Fraction(weight)
    for row in data.rows("rates"):
        if (
            row["rate_card"] == card
            and row["zone"] == zone
            and Fraction(row["weight_kg"]) == wanted
        ):
            return row
    return None


def to_usd_cents(amount_minor: int, currency: str) -> int | None:
    per_unit = USD_PER_UNIT.get(currency)
    if per_unit is None:
        return None
    units = Fraction(amount_minor, 10 ** EXPONENT[currency])
    cents = units * per_unit * 100
    # Rounding mode: half up (display only, never booked).
    return (2 * cents.numerator + cents.denominator) // (2 * cents.denominator)


def dollars(cents: int) -> int:
    """Whole dollars, rounded half up (display only)."""
    return (cents + 50) // 100


def money(amount_minor: int, currency: str) -> str:
    exponent = EXPONENT[currency]
    whole, frac = divmod(abs(amount_minor), 10**exponent)
    sign = "-" if amount_minor < 0 else ""
    if exponent == 0:
        return f"{sign}{currency} {whole:,}"
    return f"{sign}{currency} {whole:,}.{frac:0{exponent}d}"


def usd(amount_minor: int, currency: str) -> str:
    cents = to_usd_cents(amount_minor, currency)
    if cents is None:
        return ""
    return f"≈ US${dollars(cents):,}"


def price_text(row: Row, currency: str) -> str:
    low = int(row["amount_minor"])
    if row["amount_max_minor"]:
        high = int(row["amount_max_minor"])
        return f"{money(low, currency)}–{money(high, currency).split(' ', 1)[1]}"
    return money(low, currency)


def usd_text(row: Row, currency: str) -> str:
    low = to_usd_cents(int(row["amount_minor"]), currency)
    if low is None:
        return ""
    if row["amount_max_minor"]:
        high = to_usd_cents(int(row["amount_max_minor"]), currency)
        if high is not None:
            return f"US${dollars(low)}–{dollars(high)}"
    return f"US${dollars(low):,}"


def weights_of(data: Data, card: str) -> list[str]:
    found: list[str] = []
    for row in data.rows("rates"):
        if row["rate_card"] == card and row["weight_kg"] not in found:
            found.append(row["weight_kg"])
    found.sort(key=Fraction)
    return found


def zones_of(data: Data, card: str) -> list[str]:
    found: list[str] = []
    for row in data.rows("rates"):
        if row["rate_card"] == card and row["zone"] not in found:
            found.append(row["zone"])
    return found


def by_key(table: Table, key: str) -> dict[str, Row]:
    result: dict[str, Row] = {}
    for row in table:
        result[row[key]] = row
    return result


def status_counts(data: Data) -> dict[Status, int]:
    counts: dict[Status, int] = {status: 0 for status in STATUSES}
    for row in data.rows("facts"):
        counts[as_status(row["status"])] += 1
    return counts


def researched_countries(data: Data) -> list[Row]:
    """Countries with at least one fact of their own or from a legal group."""
    legal = {row["group"] for row in data.rows("groups") if row["kind"] == "legal"}
    own = {row["country"] for row in data.rows("facts")}
    result: list[Row] = []
    for row in data.rows("countries"):
        code = row["iso2"]
        if code in own:
            result.append(row)
            continue
        if (
            any(g in own and g in legal for g in groups_of(data, code))
            and row["market_status"] != "not_assessed"
        ):
            result.append(row)
    order = {status: i for i, status in enumerate(MARKET_STATUSES)}
    result.sort(key=lambda r: (order[r["market_status"]], r["name"]))
    return result


def compare_countries(data: Data) -> list[Row]:
    result: list[Row] = []
    for row in researched_countries(data):
        if (
            row["market_status"] in ("open", "conditional", "undecided")
            and row["iso2"] != "VN"
        ):
            result.append(row)
    return result


# ---------------------------------------------------------------- all-in cost

# The reference destinations of the quote requests (plan-logistics-next.md §2).
ALL_IN_DESTINATIONS: tuple[str, ...] = (
    "SG",
    "MY",
    "TH",
    "PH",
    "ID",
    "JP",
    "KR",
    "AU",
    "US",
    "DE",
    "GB",
)
# Worst last: a total takes the worst status of its parts.
STATUS_RANK: dict[Status, int] = {
    "n/a": 0,
    "verified": 1,
    "secondary": 2,
    "estimate": 3,
    "unverified": 4,
    "to_fetch": 5,
    "awaiting_answer": 6,
}
NEAR_LIMIT = Fraction(1, 10)  # within 10% of a border limit at display rates

type BorderOutcome = Literal["charged", "free", "unknown"]
type DutyPaid = Literal["yes", "no", "unknown"]


@dataclass(frozen=True)
class Part:
    """One line of an all-in total, in exact US cents (display only)."""

    label: str
    cents: Fraction | None  # None: the amount is not known
    status: Status
    note: str


@dataclass(frozen=True)
class Border:
    outcome: BorderOutcome
    status: Status
    note: str


@dataclass(frozen=True)
class AllIn:
    card: Row
    zone: str
    billed_kg: Fraction
    freight: Fraction  # US cents, before fuel and discount
    fuel_rate: Fraction | None  # e.g. 1/2 for 50%; None when unknown
    parts: tuple[Part, ...]  # freight first, then fuel, then fees
    border: Border
    duty_paid: DutyPaid
    status: Status

    def total(self) -> Fraction:
        total = Fraction(0)
        for part in self.parts:
            if part.cents is not None:
                total += part.cents
        return total

    def complete(self) -> bool:
        return all(part.cents is not None for part in self.parts)

    def buyer_pays(self) -> bool:
        """Duties would be collected from the buyer at the door."""
        return self.border.outcome == "charged" and self.duty_paid == "no"


def worst(statuses: Iterable[Status]) -> Status:
    result: Status = "n/a"
    for status in statuses:
        if STATUS_RANK[status] > STATUS_RANK[result]:
            result = status
    return result


def exact_usd_cents(amount_minor: int, currency: str) -> Fraction | None:
    per_unit = USD_PER_UNIT.get(currency)
    if per_unit is None:
        return None
    return Fraction(amount_minor, 10 ** EXPONENT[currency]) * per_unit * 100


def round_half_up(value: Fraction) -> int:
    """Rounding mode: half up (display only, never booked)."""
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def whole_dollars(cents: Fraction) -> int:
    return round_half_up(cents / 100)


def kg_text(weight: Fraction) -> str:
    """A weight break as written in the rate tables (e.g. 0.5, 2.5, 30)."""
    if weight.denominator == 1:
        return str(weight.numerator)
    return str(float(weight))  # breaks are halves or tenths: exact in float text


def in_places(data: Data, code: str, places: str) -> bool:
    """Whether code is listed in a space-separated list of countries or groups."""
    listed = places.split()
    if code in listed:
        return True
    return any(group in listed for group in groups_of(data, code))


def billed_weight(data: Data, card: str, weight: Fraction) -> Fraction | None:
    """The card's smallest weight break at or above the parcel weight."""
    for listed in weights_of(data, card):
        if Fraction(listed) >= weight:
            return Fraction(listed)
    return None


def border_row(data: Data, code: str, category: str) -> Row | None:
    """The most specific border rule: country before group, category before all."""
    places = [code, *groups_of(data, code)]
    categories = [category, ""] if category else [""]
    for place in places:
        for wanted in categories:
            for row in data.rows("border_charges"):
                if row["country"] == place and row["category"] == wanted:
                    return row
    return None


def border_for(
    data: Data, code: str, scenario: Row, freight_with_fuel: Fraction | None
) -> Border:
    row = border_row(data, code, scenario["category"])
    if row is None:
        return Border("unknown", "to_fetch", "no border rule in border_charges.csv")
    status = as_status(row["status"])
    match row["rule"]:
        case "always":
            return Border("charged", status, "duty or tax at the border at any value")
        case "never":
            return Border("free", status, "no duty or tax at the border")
        case "above":
            pass
        case _:
            return Border("unknown", status, "border rule unknown")
    limit = exact_usd_cents(int(row["above_minor"]), row["currency"])
    goods = exact_usd_cents(int(scenario["goods_value_minor"]), scenario["currency"])
    if limit is None or goods is None:
        return Border("unknown", "to_fetch", "no display rate for the limit")
    value = goods
    if row["basis"] == "goods_plus_freight":
        if freight_with_fuel is None:
            return Border("unknown", "unverified", "limit includes freight")
        value = goods + freight_with_fuel
    limit_text = money(int(row["above_minor"]), row["currency"])
    if abs(value - limit) <= limit * NEAR_LIMIT:
        status = worst((status, "estimate"))
        limit_text += ", near the limit at the display rate"
    if value > limit:
        return Border("charged", status, f"above {limit_text}")
    return Border("free", status, f"at or below {limit_text}")


def duty_paid_for(data: Data, service: Row, code: str) -> DutyPaid:
    match service["ddp"]:
        case "yes" | "mandatory":
            return "yes"
        case "ioss_only":
            # VIO prepays EU VAT and the EU flat duty only (sources/yunexpress_vn).
            return "yes" if in_places(data, code, "EU") else "no"
        case "no":
            return "no"
        case _:
            return "unknown"


def fee_cents(
    row: Row, billed_kg: Fraction, scenario: Row, freight: Fraction, goods: Fraction
) -> tuple[Fraction | None, str]:
    """A surcharge in exact US cents, with a note on how it was taken."""
    currency = row["currency"]
    percent = Fraction(row["rate_percent"]) / 100 if row["rate_percent"] else None
    amount = (
        exact_usd_cents(int(row["amount_minor"]), currency)
        if row["amount_minor"]
        else None
    )
    minimum = (
        exact_usd_cents(int(row["min_amount_minor"]), currency)
        if row["min_amount_minor"]
        else None
    )
    match row["basis"]:
        case "per_shipment" | "per_piece":
            return amount, ""
        case "per_kg":
            if amount is None:
                return None, ""
            value = amount * billed_kg
            if minimum is not None and value < minimum:
                return minimum, "minimum"
            return value, f"{kg_text(billed_kg)} kg"
        case "per_line":
            if amount is None:
                return None, ""
            return amount * int(scenario["hs_lines"]), f"{scenario['hs_lines']} line"
        case "percent_of_freight":
            if percent is None:
                return None, ""
            return freight * percent, f"{row['rate_percent']}% of freight"
        case "percent_of_value":
            if percent is None:
                return None, ""
            value = goods * percent
            if minimum is not None and value < minimum:
                return minimum, "minimum"
            return value, f"{row['rate_percent']}% of value"
        case "percent_of_duties":
            # Duties are not modelled. With combined rates up to 100%, duties are
            # at most the goods value plus freight; below that bound the minimum binds.
            if minimum is None:
                return None, "share of duties, no minimum"
            if percent is not None and (goods + freight) * percent > minimum:
                return minimum, "minimum; the percentage may be higher"
            return minimum, "minimum (binds at this value)"
        case _:
            raise ValueError(f"unknown basis {row['basis']!r}; run check first")


def all_in(data: Data, card: Row, code: str, scenario: Row) -> AllIn | None:
    name = card["rate_card"]
    zone = zone_for(data, name, code)
    if zone is None:
        return None
    billed = billed_weight(data, name, Fraction(scenario["weight_kg"]))
    if billed is None:
        return None
    rate = rate_for(data, name, zone, kg_text(billed))
    if rate is None:
        return None
    freight = exact_usd_cents(int(rate["amount_minor"]), card["currency"])
    goods = exact_usd_cents(int(scenario["goods_value_minor"]), scenario["currency"])
    if freight is None or goods is None:
        return None
    service = by_key(data.rows("services"), "service")[card["service"]]
    card_status = as_status(card["status"])
    weight = kg_text(billed)
    freight_note = f"zone {zone}, {weight} kg, {price_text(rate, card['currency'])}"
    if rate["amount_max_minor"]:
        card_status = worst((card_status, "estimate"))
        freight_note += " (low end of the range)"

    fees = [
        row
        for row in data.rows("surcharges")
        if row["service"] == card["service"]
        and (not row["destinations"] or in_places(data, code, row["destinations"]))
    ]
    fuel_row = next((row for row in fees if row["surcharge"] == "fuel"), None)
    fuel_rate: Fraction | None = None
    if fuel_row is not None and fuel_row["rate_percent"]:
        fuel_rate = Fraction(fuel_row["rate_percent"]) / 100

    freight_with_fuel = freight * (1 + fuel_rate) if fuel_rate is not None else None
    border = border_for(data, code, scenario, freight_with_fuel)
    duty_paid = duty_paid_for(data, service, code)

    parts: list[Part] = [Part("freight", freight, card_status, freight_note)]
    fuel_base = freight
    fuel_notes: list[str] = []
    for row in fees:
        trigger = row["trigger"]
        if row["surcharge"] == "fuel" or trigger in ("optional", "on_event"):
            continue
        if trigger == "passthrough":
            continue  # a duty or tax passed through: the same for every carrier
        status = as_status(row["status"])
        note = row["applies_when"]
        if trigger == "duties_paid":
            if border.outcome == "free" or duty_paid == "no":
                continue
            if border.outcome == "unknown":
                status = worst((status, "unverified"))
                note = "only if duties or taxes are charged at the border"
        cents, how = fee_cents(row, billed, scenario, freight, goods)
        if row["fuel_applies"] == "yes" and cents is not None:
            fuel_base += cents
            fuel_notes.append(row["surcharge"])
        elif row["fuel_applies"] == "unknown":
            status = worst((status, "unverified"))
            how = f"{how}; fuel on it unknown".lstrip("; ")
        parts.append(
            Part(
                row["surcharge"], cents, status, "; ".join(t for t in (how, note) if t)
            )
        )
    if fuel_row is None:
        fuel = Part("fuel", None, "to_fetch", "no fuel row for this service")
    elif fuel_rate is None:
        fuel = Part("fuel", None, as_status(fuel_row["status"]), "rate not known")
    else:
        on = "freight" + "".join(f" + {n}" for n in fuel_notes)
        fuel = Part(
            "fuel",
            fuel_base * fuel_rate,
            as_status(fuel_row["status"]),
            f"{fuel_row['rate_percent']}% of {on}",
        )
    parts.insert(1, fuel)

    statuses: list[Status] = [part.status for part in parts]
    statuses.append(border.status)
    return AllIn(
        card=card,
        zone=zone,
        billed_kg=billed,
        freight=freight,
        fuel_rate=fuel_rate,
        parts=tuple(parts),
        border=border,
        duty_paid=duty_paid,
        status=worst(statuses),
    )


def all_in_text(result: AllIn) -> str:
    prefix = "" if result.complete() else "≥ "
    text = f"{prefix}US${whole_dollars(result.total()):,}"
    if result.buyer_pays():
        return f"{text} DAP"
    if result.border.outcome != "free" and result.duty_paid == "unknown":
        return f"{text} DDP?"
    return text


def all_in_title(result: AllIn) -> str:
    lines: list[str] = []
    for part in result.parts:
        amount = "?" if part.cents is None else f"US${part.cents / 100:.2f}"
        detail = f" ({part.note})" if part.note else ""
        lines.append(f"{part.label}: {amount}{detail} [{part.status}]")
    lines.append(f"border: {result.border.outcome}, {result.border.note}")
    lines.append(f"duty-paid by this service: {result.duty_paid}")
    if result.buyer_pays():
        lines.append("DAP: the buyer would pay duties or tax at the door")
    return "\n".join(lines)


def provider_kind(data: Data, card: Row) -> str:
    service = by_key(data.rows("services"), "service")[card["service"]]
    return by_key(data.rows("providers"), "provider")[service["provider"]]["kind"]


def usable_benchmark(data: Data, result: AllIn) -> bool:
    """An economy price we could sell on today: published, complete, duty-paid."""
    if provider_kind(data, result.card) == "integrator":
        return False
    if result.card["status"] not in ("verified", "secondary"):
        return False
    if not result.complete() or result.buyer_pays():
        return False
    return not (result.border.outcome != "free" and result.duty_paid != "yes")


# The big-three published express cards that a discount would apply to.
BREAK_EVEN_CARDS: tuple[str, ...] = (
    "dhl_ew_vn_2026_list",
    "fedex_ip_vn_2026_list",
    "ups_saver_vn_2026_list",
)

type BreakEven = Literal["cheaper", "unreachable", "discount"]


@dataclass(frozen=True)
class Discount:
    kind: BreakEven
    needed: Fraction  # share of freight, only for kind "discount"
    at_least: bool  # the express total is incomplete, so the need is higher


def break_even(express: AllIn, benchmark: AllIn) -> Discount:
    """Freight discount at which express matches the benchmark.

    Fuel is charged on net freight, so a discount d gives
    F(1-d)(1+f) + rest; fixed fees (rest) are not discounted.
    """
    fuel = express.fuel_rate if express.fuel_rate is not None else Fraction(0)
    freight_and_fuel = express.freight * (1 + fuel)
    rest = express.total() - freight_and_fuel
    target = benchmark.total()
    at_least = not express.complete()
    if express.total() <= target:
        return Discount("cheaper", Fraction(0), at_least)
    if target <= rest:
        return Discount("unreachable", Fraction(0), at_least)
    return Discount("discount", 1 - (target - rest) / freight_and_fuel, at_least)


def discount_text(discount: Discount) -> str:
    match discount.kind:
        case "cheaper":
            return "cheaper already" if not discount.at_least else "?"
        case "unreachable":
            return "fees alone exceed it"
        case "discount":
            prefix = "≥ " if discount.at_least else ""
            return f"{prefix}{round_half_up(discount.needed * 100)}%"
        case _:
            assert_never(discount.kind)


def all_in_cards(data: Data) -> list[Row]:
    """Rate cards that price at least one reference destination."""
    result: list[Row] = []
    for card in data.rows("rate_cards"):
        for code in ALL_IN_DESTINATIONS:
            if zone_for(data, card["rate_card"], code) is not None:
                result.append(card)
                break
    return result


def cheapest_benchmark(results: Iterable[AllIn | None], data: Data) -> AllIn | None:
    best: AllIn | None = None
    for result in results:
        if result is None or not usable_benchmark(data, result):
            continue
        if best is None or result.total() < best.total():
            best = result
    return best


# ---------------------------------------------------------------- HTML

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#dedbd2;--panel:#ffffff;
--accent:#2d5d8a;--s-verified:#c8e6c9;--s-secondary:#e3f2d9;--s-estimate:#fff3c4;
--s-unverified:#ffd8a8;--s-to_fetch:#d6e4ff;--s-awaiting_answer:#e5d4ff;
--s-na:#eeeeee;--s-ink:#1d1d1b}@media (prefers-color-scheme: dark){
:root:not([data-theme=light]){--bg:#161615;--fg:#ecebe6;--muted:#a3a29b;
--line:#34332f;--panel:#1f1f1d;--accent:#8db8e0;--s-verified:#24452a;
--s-secondary:#2f3d27;--s-estimate:#4a4120;--s-unverified:#553a1c;
--s-to_fetch:#24365a;--s-awaiting_answer:#3b2d58;--s-na:#2b2b29;--s-ink:#ecebe6}}
:root[data-theme=dark]{--bg:#161615;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;
--panel:#1f1f1d;--accent:#8db8e0;--s-verified:#24452a;--s-secondary:#2f3d27;
--s-estimate:#4a4120;--s-unverified:#553a1c;--s-to_fetch:#24365a;
--s-awaiting_answer:#3b2d58;--s-na:#2b2b29;--s-ink:#ecebe6}*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1400px;
margin:0 auto;padding:24px 16px 64px}h1{font-size:26px;margin:0 0 4px}h2{
font-size:19px;margin:40px 0 8px;padding-top:8px;border-top:1px solid var(--line)}
h3{font-size:15px;margin:20px 0 6px}p,li{max-width:80ch}.muted{color:var(--muted)}a{
color:var(--accent)}nav{display:flex;flex-wrap:wrap;gap:6px 14px;margin:12px 0 4px}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:6px;
background:var(--panel)}table{border-collapse:collapse;width:100%}th,td{
border-bottom:1px solid var(--line);padding:5px 7px;text-align:left;
vertical-align:top}th{position:sticky;top:0;background:var(--panel);font-weight:600;
font-size:12.5px;z-index:1}td.num,th.num{text-align:right;
font-variant-numeric:tabular-nums;white-space:nowrap}.cell{min-width:150px;
max-width:260px;font-size:12.5px}.st-verified{background:var(--s-verified);
color:var(--s-ink)}.st-secondary{background:var(--s-secondary);color:var(--s-ink)}
.st-estimate{background:var(--s-estimate);color:var(--s-ink)}.st-unverified{
background:var(--s-unverified);color:var(--s-ink)}.st-to_fetch{
background:var(--s-to_fetch);color:var(--s-ink)}.st-awaiting_answer{
background:var(--s-awaiting_answer);color:var(--s-ink)}.st-na{
background:var(--s-na);color:var(--s-ink)}.chip{display:inline-block;
padding:1px 8px;border-radius:10px;font-size:12px;margin:2px 4px 2px 0}.via{
font-size:11px;color:var(--muted)}details{border:1px solid var(--line);
border-radius:6px;background:var(--panel);margin:6px 0}summary{cursor:pointer;
padding:8px 10px;font-weight:600}details>.scroll{border:0;
border-top:1px solid var(--line);border-radius:0}.ms{font-size:12px;font-weight:500;
color:var(--muted)}code{font-size:12.5px}
"""


def status_class(status: str) -> str:
    return "st-na" if status == "n/a" else f"st-{status}"


def chip(status: Status, text: str | None = None) -> h.Element:
    return h.span(class_=f"chip {status_class(status)}")[
        text if text is not None else STATUS_LABEL[status]
    ]


def source_link(row: Row) -> h.Node:
    label = row.get("source", "") or "source"
    url = row.get("source_url", "")
    if url:
        return h.a(href=url)[label]
    return label


def fact_title(resolved: Resolved) -> str:
    row = resolved.fact
    parts = [f"{STATUS_LABEL[as_status(row['status'])]}"]
    if row["source"]:
        parts.append(f"Source: {row['source']}")
    if row["research_ref"]:
        parts.append(f"Research: {row['research_ref']}")
    if row["question"]:
        parts.append(f"Question: {row['question']}")
    if resolved.via:
        parts.append(f"Set on group {resolved.via}")
    if row["notes"]:
        parts.append(f"Note: {row['notes']}")
    return "\n".join(parts)


def section_legend(data: Data) -> h.Element:
    counts = status_counts(data)
    chips: list[h.Node] = []
    for status in STATUSES:
        chips.append(chip(status, f"{STATUS_LABEL[status]}: {counts[status]}"))
    return h.p[chips]


def section_overview(data: Data) -> h.Element:
    topics = by_key(data.rows("topics"), "topic")
    head = h.tr[
        h.th["Code"],
        h.th["Country"],
        h.th["Market status"],
        [h.th[topics[t]["label"]] for t in OVERVIEW_TOPICS],
    ]
    body: list[h.Node] = []
    for country in researched_countries(data):
        code = country["iso2"]
        facts = resolve_facts(data, code)
        cells: list[h.Node] = []
        for topic in OVERVIEW_TOPICS:
            resolved = overview_fact(facts, topic)
            if resolved is None:
                cells.append(h.td(class_="cell")[""])
                continue
            row = resolved.fact
            cells.append(
                h.td(
                    class_=f"cell {status_class(row['status'])}",
                    title=fact_title(resolved),
                )[
                    row["value"] or STATUS_LABEL[as_status(row["status"])],
                    h.div(class_="via")[f"via {resolved.via}"]
                    if resolved.via
                    else None,
                ]
            )
        body.append(
            h.tr[
                h.td[h.a(href=f"#c-{code}")[code]],
                h.td[country["name"]],
                h.td[
                    country["market_status"],
                    h.div(class_="via")[country["status_note"]],
                ],
                cells,
            ]
        )
    return h.section[
        h.h2(id="overview")["Researched countries"],
        h.p(class_="muted")[
            "General facts per country (no product category). Hover a cell for its "
            "source; the code links to all facts. "
            "Facts set on a group (EU, ASEAN, GCC) show where they come from."
        ],
        h.div(class_="scroll")[h.table[h.thead[head], h.tbody[body]]],
    ]


def section_details(data: Data) -> h.Element:
    topics = by_key(data.rows("topics"), "topic")
    order = {row["topic"]: i for i, row in enumerate(data.rows("topics"))}
    blocks: list[h.Node] = []
    for country in researched_countries(data):
        code = country["iso2"]
        facts = resolve_facts(data, code)
        keys = sorted(facts, key=lambda k: (order.get(k[0], 99), k[1], k[2]))
        rows: list[h.Node] = []
        for key in keys:
            resolved = facts[key]
            row = resolved.fact
            topic, category, origin = key
            rows.append(
                h.tr[
                    h.td[topics[topic]["label"]],
                    h.td[category or "all", f" · from {origin}" if origin else ""],
                    h.td(class_=status_class(row["status"]))[
                        row["value"] or STATUS_LABEL[as_status(row["status"])]
                    ],
                    h.td[
                        STATUS_LABEL[as_status(row["status"])],
                        h.div(class_="via")[f"via {resolved.via}"]
                        if resolved.via
                        else None,
                    ],
                    h.td[source_link(row), h.div(class_="via")[row["research_ref"]]],
                    h.td[row["question"]],
                    h.td[row["notes"]],
                ]
            )
        blocks.append(
            h.details(id=f"c-{code}")[
                h.summary[
                    f"{code} · {country['name']} ",
                    h.span(class_="ms")[
                        f"{country['market_status']} · {len(keys)} facts"
                    ],
                ],
                h.div(class_="scroll")[
                    h.table[
                        h.thead[
                            h.tr[
                                h.th["Topic"],
                                h.th["Category"],
                                h.th["Value"],
                                h.th["Status"],
                                h.th["Source"],
                                h.th["Question"],
                                h.th["Notes"],
                            ]
                        ],
                        h.tbody[rows],
                    ]
                ],
            ]
        )
    return h.section[h.h2(id="details")["All facts per country"], blocks]


QUESTION_LINKED_TABLES = (
    "facts",
    "services",
    "rate_cards",
    "surcharges",
    "border_charges",
)


def rows_waiting(data: Data) -> dict[str, int]:
    """How many data rows point to each question."""
    waiting: dict[str, int] = {}
    for table in QUESTION_LINKED_TABLES:
        for row in data.rows(table):
            question = row.get("question", "")
            if question:
                waiting[question] = waiting.get(question, 0) + 1
    return waiting


@dataclass(frozen=True)
class TodoItem:
    question: Row
    waiting: int  # data rows this question would settle
    overdue: bool


def is_open(question: Row) -> bool:
    return question["status"] in OPEN_QUESTION_STATUSES


def urgency(question: Row) -> tuple[int, str, str]:
    """Sort key: priority, then due date (none last), then id."""
    rank = PRIORITIES.index(question["priority"])
    due = question["due_on"] or "9999-12-31"
    return (rank, due, question["question"])


def todo_items(data: Data, today: date) -> list[TodoItem]:
    waiting = rows_waiting(data)
    items: list[TodoItem] = []
    for question in data.rows("questions"):
        if not is_open(question):
            continue
        due = question["due_on"]
        overdue = bool(due) and date.fromisoformat(due) < today
        items.append(TodoItem(question, waiting.get(question["question"], 0), overdue))
    items.sort(key=lambda item: urgency(item.question))
    return items


def next_step(question: Row) -> str:
    """What to do next about one open question."""
    if question["blocked_by"]:
        return f"{question['question']} blocked: {question['blocked_by']}"
    if question["status"] == "sent":
        step = f"{question['question']}: waiting since {question['sent_on']}"
        if question["due_on"]:
            step += f", follow up {question['due_on']}"
        return step
    step = f"Send {question['question']} ({question['subject']})"
    if question["due_on"]:
        step += f" by {question['due_on']}"
    return step


@dataclass(frozen=True)
class Contact:
    provider: Row
    questions: list[Row]  # every question that names this provider
    last_sent: str  # latest sent_on, "" when nothing was sent
    answered: int
    next_step: str


def contacts(data: Data) -> list[Contact]:
    result: list[Contact] = []
    for provider in data.rows("providers"):
        mine: list[Row] = []
        for question in data.rows("questions"):
            if provider["provider"] in question["providers"].split():
                mine.append(question)
        last_sent = ""
        answered = 0
        open_questions: list[Row] = []
        for question in mine:
            last_sent = max(last_sent, question["sent_on"])
            if question["status"] == "answered":
                answered += 1
            if is_open(question):
                open_questions.append(question)
        if open_questions:
            step = next_step(min(open_questions, key=urgency))
        elif provider["relationship"] in ("not_pursued", "n/a"):
            step = "None: not pursued (see notes)"
        elif mine:
            step = "Nothing open"
        else:
            step = "No question written"
        result.append(Contact(provider, mine, last_sent, answered, step))
    return result


def question_trail(questions: list[Row]) -> list[str]:
    """One line per question: id, status and its dates."""
    lines: list[str] = []
    for question in questions:
        line = f"{question['question']} {question['status']}"
        if question["sent_on"]:
            line += f" {question['sent_on']}"
        if question["answered_on"]:
            line += f", answered {question['answered_on']}"
        lines.append(line)
    return lines


def section_todo(data: Data, today: date) -> h.Element:
    rows: list[h.Node] = []
    for item in todo_items(data, today):
        q = item.question
        due: h.Node = q["due_on"]
        if item.overdue:
            due = [q["due_on"], h.div[chip("unverified", "overdue")]]
        status: h.Node = q["status"]
        if q["sent_on"]:
            status = [q["status"], h.div(class_="via")[q["sent_on"]]]
        rows.append(
            h.tr[
                h.td[PRIORITY_LABEL[q["priority"]]],
                h.td(class_="num")[due],
                h.td[q["question"]],
                h.td[q["to"]],
                h.td[h.strong[q["subject"]], h.div(class_="via")[q["text"]]],
                h.td[status],
                h.td[q["blocked_by"]],
                h.td(class_="num")[str(item.waiting)],
            ]
        )
    return h.section[
        h.h2(id="todo")["To do"],
        h.p(class_="muted")[
            "Open questions (draft or sent), most urgent first: priority, then due "
            "date. 'Now' is needed for the next decisions, 'Before launch' before "
            "the first live order, 'Later' for later markets. 'Rows' counts the "
            "data rows the answer would settle. Set priority, due_on and "
            "blocked_by in questions.csv."
        ],
        h.div(class_="scroll")[
            h.table[
                h.thead[
                    h.tr[
                        h.th["Priority"],
                        h.th(class_="num")["Due"],
                        h.th["Id"],
                        h.th["To"],
                        h.th["Question"],
                        h.th["Status"],
                        h.th["Blocked by"],
                        h.th(class_="num")["Rows"],
                    ]
                ],
                h.tbody[rows],
            ]
        ],
    ]


def section_contacts(data: Data) -> h.Element:
    rows: list[h.Node] = []
    for contact in contacts(data):
        p = contact.provider
        name: h.Node = h.a(href=p["website"])[p["name"]] if p["website"] else p["name"]
        trail: list[h.Node] = []
        for line in question_trail(contact.questions):
            trail.append(h.div[line])
        rows.append(
            h.tr[
                h.td[name, h.div(class_="via")[p["provider"]]],
                h.td[p["kind"]],
                h.td[p["relationship"]],
                h.td(class_="num")[contact.last_sent],
                h.td[trail],
                h.td[contact.next_step],
            ]
        )
    return h.section[
        h.h2(id="contacts")["Contacts"],
        h.p(class_="muted")[
            "One row per provider: our relationship (providers.csv), the questions "
            "that name it (questions.csv, column providers) and the next step, "
            "taken from its most urgent open question."
        ],
        h.div(class_="scroll")[
            h.table[
                h.thead[
                    h.tr[
                        h.th["Provider"],
                        h.th["Kind"],
                        h.th["Relationship"],
                        h.th(class_="num")["Last sent"],
                        h.th["Questions"],
                        h.th["Next step"],
                    ]
                ],
                h.tbody[rows],
            ]
        ],
    ]


def section_questions(data: Data) -> h.Element:
    waiting = rows_waiting(data)
    rows: list[h.Node] = []
    for row in data.rows("questions"):
        rows.append(
            h.tr[
                h.td[row["question"]],
                h.td[row["kind"]],
                h.td[row["status"], h.div(class_="via")[row["sent_on"]]],
                h.td[row["to"]],
                h.td[h.strong[row["subject"]], h.div[row["text"]]],
                h.td(class_="num")[str(waiting.get(row["question"], 0))],
                h.td[row["answer"]],
            ]
        )
    return h.section[
        h.h2(id="questions")["Open questions"],
        h.p(class_="muted")[
            "Emails to send, adviser questions and pages to look up again. 'Rows' "
            "counts the table rows that point to the question."
        ],
        h.div(class_="scroll")[
            h.table[
                h.thead[
                    h.tr[
                        h.th["Id"],
                        h.th["Kind"],
                        h.th["Status"],
                        h.th["To"],
                        h.th["Question"],
                        h.th(class_="num")["Rows"],
                        h.th["Answer"],
                    ]
                ],
                h.tbody[rows],
            ]
        ],
    ]


def section_compare(data: Data) -> h.Element:
    cards = data.rows("rate_cards")
    blocks: list[h.Node] = []
    for weight in COMPARE_WEIGHTS:
        head = h.tr[
            h.th["Country"], [h.th(class_="num")[c["rate_card"]] for c in cards]
        ]
        rows: list[h.Node] = []
        for country in compare_countries(data):
            cells: list[h.Node] = []
            for card in cards:
                zone = zone_for(data, card["rate_card"], country["iso2"])
                rate = (
                    rate_for(data, card["rate_card"], zone, weight)
                    if zone is not None
                    else None
                )
                if rate is None:
                    cells.append(h.td(class_="num")[""])
                    continue
                cells.append(
                    h.td(
                        class_=f"num {status_class(card['status'])}",
                        title=f"zone {zone}: {price_text(rate, card['currency'])}",
                    )[usd_text(rate, card["currency"])]
                )
            rows.append(h.tr[h.td[f"{country['iso2']} {country['name']}"], cells])
        blocks.append(
            h.details[
                h.summary[f"{weight} kg"],
                h.div(class_="scroll")[h.table[h.thead[head], h.tbody[rows]]],
            ]
        )
    return h.section[
        h.h2(id="compare")["Price per country and weight"],
        h.p(class_="muted")[
            "Freight only, before fuel, duty-paid fee, other surcharges and any "
            "discount. "
            f"{USD_RATE_NOTE}. A blank cell means the card has no price at that exact "
            "weight or no zone for that country. "
            "Cell colour is the rate card's status; hover for the original amount."
        ],
        blocks,
    ]


def country_label(data: Data, code: str) -> str:
    return f"{code} {by_key(data.rows('countries'), 'iso2')[code]['name']}"


def all_in_grid(
    data: Data, scenario: Row, cards: list[Row]
) -> list[list[AllIn | None]]:
    grid: list[list[AllIn | None]] = []
    for code in ALL_IN_DESTINATIONS:
        grid.append([all_in(data, card, code, scenario) for card in cards])
    return grid


def section_all_in(data: Data) -> h.Element:
    cards = all_in_cards(data)
    express = [c for c in cards if c["rate_card"] in BREAK_EVEN_CARDS]
    blocks: list[h.Node] = []
    for scenario in data.rows("scenarios"):
        grid = all_in_grid(data, scenario, cards)
        head = h.tr[
            h.th["Destination"], [h.th(class_="num")[c["rate_card"]] for c in cards]
        ]
        rows: list[h.Node] = []
        even_rows: list[h.Node] = []
        for code, results in zip(ALL_IN_DESTINATIONS, grid, strict=True):
            cells: list[h.Node] = []
            for result in results:
                if result is None:
                    cells.append(h.td(class_="num")[""])
                    continue
                cells.append(
                    h.td(
                        class_=f"num {status_class(result.status)}",
                        title=all_in_title(result),
                    )[all_in_text(result)]
                )
            rows.append(h.tr[h.td[country_label(data, code)], cells])

            best = cheapest_benchmark(results, data)
            even_cells: list[h.Node] = []
            for card in express:
                result = results[cards.index(card)]
                if result is None or best is None:
                    even_cells.append(h.td(class_="num")[""])
                    continue
                even_cells.append(
                    h.td(class_="num", title=all_in_title(result))[
                        discount_text(break_even(result, best))
                    ]
                )
            even_rows.append(
                h.tr[
                    h.td[country_label(data, code)],
                    h.td(title=all_in_title(best) if best else "")[
                        f"{best.card['rate_card']} {all_in_text(best)}"
                        if best
                        else "none usable"
                    ],
                    even_cells,
                ]
            )
        goods = money(int(scenario["goods_value_minor"]), scenario["currency"])
        blocks.append(
            h.details[
                h.summary[
                    f"{scenario['label']}: {scenario['weight_kg']} kg, goods {goods}"
                ],
                h.div(class_="scroll")[h.table[h.thead[head], h.tbody[rows]]],
                h.div(style="padding:8px 10px")[
                    h.strong["Freight discount the express list cards need"],
                    " to match the cheapest usable economy line (hover for parts)",
                ],
                h.div(class_="scroll")[
                    h.table[
                        h.thead[
                            h.tr[
                                h.th["Destination"],
                                h.th["Cheapest usable economy line"],
                                [h.th(class_="num")[c["rate_card"]] for c in express],
                            ]
                        ],
                        h.tbody[even_rows],
                    ]
                ],
            ]
        )
    return h.section[
        h.h2(id="all-in")["All-in cost per parcel"],
        h.p(class_="muted")[
            "List price per parcel for a B2C delivery to a home address, duty-paid "
            "where the service can do it: freight at the card's next weight break, "
            "fuel (also on the fees it applies to), the fees charged on every "
            "parcel (trigger always), and the carrier's duty-paid fee when the "
            "border rule says duty or tax is due there. Duties and taxes themselves "
            "are left out: they are the same whichever carrier carries the parcel. "
            "Fees that depend on the event (remote area, address correction, "
            "inspections) and optional ones (insurance, pickup, residential "
            "notification at DHL, FedEx third-party billing) are left out. Where "
            "a border limit includes shipping (MY, KR, CH), the carrier's freight "
            "and fuel stand in for the shipping the buyer pays. Each "
            "part is converted to US$ and then summed, display only. "
            f"{USD_RATE_NOTE}."
        ],
        h.ul(class_="muted")[
            h.li[
                "≥: a part is missing (e.g. fuel or a duty-paid fee not known), "
                "so the total is a lower bound."
            ],
            h.li[
                "DAP: duty or tax is due at the border and the service does not "
                "pay it, so the buyer would pay at the door. Not usable for us "
                "as merchant of record."
            ],
            h.li[
                "DDP?: duty or tax may be due and the service's duty-paid terms "
                "are unknown."
            ],
            h.li[
                "Cell colour is the worst status among the parts and the border "
                "rule. Weights are actual weights, assumed at or above the "
                "volumetric weight."
            ],
            h.li[
                "Discount: the share off freight (fuel follows net freight, fees "
                "stay) at which the express list price equals the cheapest "
                "economy line that is published (verified or secondary), "
                "complete and duty-paid on that lane. 'Fees alone exceed it': "
                "no freight discount is enough."
            ],
        ],
        blocks,
        h.h3["Border rules used"],
        section_border_rules(data),
    ]


def section_border_rules(data: Data) -> h.Element:
    rows: list[h.Node] = []
    for row in data.rows("border_charges"):
        limit = (
            money(int(row["above_minor"]), row["currency"])
            if row["rule"] == "above"
            else ""
        )
        rows.append(
            h.tr[
                h.td[row["country"]],
                h.td[row["category"]],
                h.td[row["rule"]],
                h.td(class_="num")[limit],
                h.td[row["basis"]],
                h.td(class_=status_class(row["status"]))[
                    STATUS_LABEL[as_status(row["status"])]
                ],
                h.td[source_link(row)],
                h.td[row["question"]],
                h.td[row["notes"]],
            ]
        )
    return h.div(class_="scroll")[
        h.table[
            h.thead[
                h.tr[
                    h.th["Place"],
                    h.th["Category"],
                    h.th["Duty or tax at the border"],
                    h.th(class_="num")["Above"],
                    h.th["Value tested"],
                    h.th["Status"],
                    h.th["Source"],
                    h.th["Question"],
                    h.th["Notes"],
                ]
            ],
            h.tbody[rows],
        ]
    ]


def section_carriers(data: Data) -> h.Element:
    providers = data.rows("providers")
    services = data.rows("services")
    provider_rows = [
        h.tr[
            h.td[p["provider"]],
            h.td[h.a(href=p["website"])[p["name"]] if p["website"] else p["name"]],
            h.td[p["kind"]],
            h.td[p["relationship"]],
            h.td[p["notes"]],
        ]
        for p in providers
    ]
    service_rows = [
        h.tr[
            h.td[s["service"]],
            h.td[s["name"]],
            h.td[s["origin"]],
            h.td[s["tier"]],
            h.td[s["ddp"]],
            h.td(class_="num")[s["volumetric_divisor"]],
            h.td(class_="num")[s["weight_step_kg"]],
            h.td(class_=status_class(s["status"]))[
                STATUS_LABEL[as_status(s["status"])]
            ],
            h.td[source_link(s), h.div(class_="via")[s["research_ref"]]],
            h.td[s["question"]],
            h.td[s["notes"]],
        ]
        for s in services
    ]
    return h.section[
        h.h2(id="carriers")["Providers and services"],
        h.div(class_="scroll")[
            h.table[
                h.thead[
                    h.tr[
                        h.th["Id"],
                        h.th["Provider"],
                        h.th["Kind"],
                        h.th["Our contact"],
                        h.th["Notes"],
                    ]
                ],
                h.tbody[provider_rows],
            ]
        ],
        h.h3["Services"],
        h.div(class_="scroll")[
            h.table[
                h.thead[
                    h.tr[
                        h.th["Id"],
                        h.th["Service"],
                        h.th["Origin"],
                        h.th["Tier"],
                        h.th["DDP"],
                        h.th(class_="num")["Divisor"],
                        h.th(class_="num")["Step kg"],
                        h.th["Status"],
                        h.th["Source"],
                        h.th["Question"],
                        h.th["Notes"],
                    ]
                ],
                h.tbody[service_rows],
            ]
        ],
    ]


def section_rate_cards(data: Data) -> h.Element:
    blocks: list[h.Node] = []
    for card in data.rows("rate_cards"):
        name = card["rate_card"]
        currency = card["currency"]
        zones = zones_of(data, name)
        members: dict[str, list[str]] = {}
        for row in data.rows("zones"):
            if row["rate_card"] == name:
                members.setdefault(row["zone"], []).append(row["destination"])
        rows: list[h.Node] = []
        for weight in weights_of(data, name):
            cells: list[h.Node] = []
            for zone in zones:
                rate = rate_for(data, name, zone, weight)
                if rate is None:
                    cells.append(h.td(class_="num")[""])
                    continue
                cells.append(
                    h.td(class_="num", title=usd_text(rate, currency))[
                        price_text(rate, currency).split(" ", 1)[1]
                    ]
                )
            rows.append(h.tr[h.th(class_="num")[f"{weight} kg"], cells])
        zone_rows = [
            h.tr[h.td[zone], h.td[" ".join(members.get(zone, []))]] for zone in members
        ]
        blocks.append(
            h.details[
                h.summary[
                    f"{name} ",
                    h.span(class_="ms")[
                        f"{card['kind']} · {currency} · "
                        f"{STATUS_LABEL[as_status(card['status'])]}"
                    ],
                ],
                h.div(style="padding:8px 10px")[
                    h.p[
                        chip(as_status(card["status"])),
                        (
                            f" Valid {card['effective_from'] or '?'} to "
                            f"{card['effective_to'] or 'open'}. "
                            f"Excludes: {card['excludes'] or 'not stated'}. "
                        ),
                        source_link(card),
                        f" · {card['research_ref']}",
                        f" · question {card['question']}" if card["question"] else "",
                    ],
                    h.p(class_="muted")[card["notes"]] if card["notes"] else None,
                ],
                h.div(class_="scroll")[
                    h.table[
                        h.thead[
                            h.tr[
                                h.th(class_="num")[currency],
                                [h.th(class_="num")[f"zone {z}"] for z in zones],
                            ]
                        ],
                        h.tbody[rows],
                    ]
                ],
                h.div(class_="scroll")[
                    h.table[
                        h.thead[h.tr[h.th["Zone"], h.th["Destinations"]]],
                        h.tbody[zone_rows],
                    ]
                ],
            ]
        )
    return h.section[
        h.h2(id="rates")["Rate cards"],
        h.p(class_="muted")[
            "Amounts in the card's currency; hover for the US$ equivalent."
        ],
        blocks,
    ]


def section_surcharges(data: Data) -> h.Element:
    rows: list[h.Node] = []
    for s in data.rows("surcharges"):
        amount = ""
        if s["rate_percent"]:
            amount = f"{s['rate_percent']}%"
        if s["amount_minor"]:
            unit = {
                "per_kg": " per kg",
                "per_line": " per line",
                "per_piece": " per piece",
            }.get(s["basis"], "")
            amount = (
                f"{amount} {money(int(s['amount_minor']), s['currency'])}{unit}".strip()
            )
        if s["min_amount_minor"]:
            amount = f"{amount}, min {money(int(s['min_amount_minor']), s['currency'])}"
        rows.append(
            h.tr[
                h.td[s["service"]],
                h.td[s["surcharge"]],
                h.td[s["basis"]],
                h.td(class_="num")[amount or "?"],
                h.td[s["trigger"]],
                h.td[s["destinations"]],
                h.td[s["fuel_applies"]],
                h.td[s["applies_when"]],
                h.td(class_=status_class(s["status"]))[
                    STATUS_LABEL[as_status(s["status"])]
                ],
                h.td[source_link(s)],
                h.td[s["question"]],
                h.td[s["notes"]],
            ]
        )
    return h.section[
        h.h2(id="surcharges")["Surcharges"],
        h.div(class_="scroll")[
            h.table[
                h.thead[
                    h.tr[
                        h.th["Service"],
                        h.th["Surcharge"],
                        h.th["Basis"],
                        h.th(class_="num")["Amount"],
                        h.th["Charged"],
                        h.th["Destinations"],
                        h.th["Fuel on it"],
                        h.th["When"],
                        h.th["Status"],
                        h.th["Source"],
                        h.th["Question"],
                        h.th["Notes"],
                    ]
                ],
                h.tbody[rows],
            ]
        ],
    ]


def section_transit(data: Data) -> h.Element:
    services: list[str] = []
    destinations: list[str] = []
    cell: dict[tuple[str, str], Row] = {}
    for row in data.rows("transit"):
        if row["service"] not in services:
            services.append(row["service"])
        if row["destination"] not in destinations:
            destinations.append(row["destination"])
        cell[(row["destination"], row["service"])] = row
    rows: list[h.Node] = []
    for dest in destinations:
        cells: list[h.Node] = []
        for service in services:
            row = cell.get((dest, service))
            if row is None:
                cells.append(h.td(class_="num")[""])
                continue
            label = STATUS_LABEL[as_status(row["status"])]
            title = f"{label}. {row['source']}. {row['notes']}".strip()
            cells.append(
                h.td(class_=f"num {status_class(row['status'])}", title=title)[
                    f"{row['days_min']}–{row['days_max']}"
                ]
            )
        rows.append(h.tr[h.td[dest], cells])
    restriction_rows = [
        h.tr[
            h.td[r["service"]],
            h.td[r["category"]],
            h.td[r["rule"]],
            h.td(class_=status_class(r["status"]))[
                STATUS_LABEL[as_status(r["status"])]
            ],
            h.td[r["notes"]],
            h.td[r["research_ref"]],
        ]
        for r in data.rows("service_restrictions")
    ]
    return h.section[
        h.h2(id="transit")["Transit days"],
        h.p(class_="muted")[
            "Business days door to door from HCMC or Hanoi. Destinations are "
            "countries or research regions (see groups.csv)."
        ],
        h.div(class_="scroll")[
            h.table[
                h.thead[
                    h.tr[h.th["Destination"], [h.th(class_="num")[s] for s in services]]
                ],
                h.tbody[rows],
            ]
        ],
        h.h3["Restrictions per product category"],
        h.div(class_="scroll")[
            h.table[
                h.thead[
                    h.tr[
                        h.th["Service"],
                        h.th["Category"],
                        h.th["Rule"],
                        h.th["Status"],
                        h.th["Notes"],
                        h.th["Source"],
                    ]
                ],
                h.tbody[restriction_rows],
            ]
        ],
    ]


def section_all_countries(data: Data) -> h.Element:
    rows = [
        h.tr[
            h.td[c["iso2"]],
            h.td[c["iso3"]],
            h.td[c["name"]],
            h.td[c["subregion"] or c["region"]],
            h.td[c["currency"]],
            h.td[c["market_status"]],
            h.td[c["status_note"]],
        ]
        for c in data.rows("countries")
    ]
    return h.section[
        h.details(id="countries")[
            h.summary[f"All {len(rows)} countries (ISO 3166-1)"],
            h.div(class_="scroll")[
                h.table[
                    h.thead[
                        h.tr[
                            h.th["ISO2"],
                            h.th["ISO3"],
                            h.th["Name"],
                            h.th["Region"],
                            h.th["Currency"],
                            h.th["Status"],
                            h.th["Note"],
                        ]
                    ],
                    h.tbody[rows],
                ]
            ],
        ]
    ]


def build_html(data: Data, today: date) -> str:
    page = h.html(lang="en")[
        h.head[
            h.meta(charset="utf-8"),
            h.meta(name="viewport", content="width=device-width, initial-scale=1"),
            h.title["Markets Data"],
            h.style[CSS],
        ],
        h.body[
            h.main[
                h.h1["Markets and logistics data"],
                h.p(class_="muted")[
                    f"Generated {today.isoformat()} from docs/markets/*.csv by "
                    "scripts/markets.py. Do not edit this page; edit the CSVs. "
                    "Origin: Vietnam. Plan: docs/plan-markets-data.md."
                ],
                section_legend(data),
                h.nav[
                    h.a(href="#overview")["Overview"],
                    h.a(href="#todo")["To do"],
                    h.a(href="#contacts")["Contacts"],
                    h.a(href="#details")["Facts per country"],
                    h.a(href="#questions")["Open questions"],
                    h.a(href="#compare")["Price comparison"],
                    h.a(href="#all-in")["All-in cost"],
                    h.a(href="#carriers")["Providers"],
                    h.a(href="#rates")["Rate cards"],
                    h.a(href="#surcharges")["Surcharges"],
                    h.a(href="#transit")["Transit"],
                    h.a(href="#countries")["All countries"],
                ],
                section_overview(data),
                section_todo(data, today),
                section_contacts(data),
                section_details(data),
                section_questions(data),
                section_compare(data),
                section_all_in(data),
                section_carriers(data),
                section_rate_cards(data),
                section_surcharges(data),
                section_transit(data),
                section_all_countries(data),
            ]
        ],
    ]
    return str(page) + "\n"


# ---------------------------------------------------------------- workbook


def fill(status: Status) -> PatternFill:
    return PatternFill(
        start_color=STATUS_FILL[status],
        end_color=STATUS_FILL[status],
        fill_type="solid",
    )


def write_raw(sheet: Worksheet, columns: Sequence[str], rows: Table) -> None:
    sheet.append(list(columns))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append([row[c] for c in columns])
    sheet.freeze_panes = "A2"
    if "status" in columns:
        index = list(columns).index("status") + 1
        for r in range(2, len(rows) + 2):
            cell = sheet.cell(row=r, column=index)
            value = str(cell.value or "")
            if value in STATUSES:
                cell.fill = fill(as_status(value))
    for i, column in enumerate(columns, start=1):
        width = (
            14
            if column
            not in (
                "value",
                "text",
                "notes",
                "source",
                "source_url",
                "official_name",
                "members",
                "label",
                "description",
            )
            else 50
        )
        sheet.column_dimensions[get_column_letter(i)].width = width


def bold_row(sheet: Worksheet, row: int) -> None:
    for cell in sheet[row]:
        cell.font = Font(bold=True)


def sheet_readme(book: Workbook, today: date) -> None:
    sheet = book.create_sheet("README")
    sheet.append(["Markets and logistics data"])
    sheet.append(
        [
            (
                f"Generated {today.isoformat()} from docs/markets/*.csv "
                "(scripts/markets.py). The CSVs in the repo are the source of truth."
            )
        ]
    )
    sheet.append(
        [
            (
                "Sheets after 'Prices' are the CSVs with the same columns, so edits "
                "here can be read back later."
            )
        ]
    )
    sheet.append([])
    sheet.append(["Status", "Meaning"])
    for status in STATUSES:
        sheet.append([status, STATUS_LABEL[status]])
        sheet.cell(row=sheet.max_row, column=1).fill = fill(status)
    sheet.column_dimensions["A"].width = 20
    sheet.column_dimensions["B"].width = 90


OVERVIEW_FIRST_FACT_COLUMN = 5  # after code, country, market status, note


def sheet_overview(book: Workbook, data: Data) -> None:
    topics = by_key(data.rows("topics"), "topic")
    sheet = book.create_sheet("Overview")
    labels = [topics[t]["label"] for t in OVERVIEW_TOPICS]
    sheet.append(["Code", "Country", "Market status", "Status note", *labels])
    bold_row(sheet, 1)
    for country in researched_countries(data):
        facts = resolve_facts(data, country["iso2"])
        sheet.append(
            [
                country["iso2"],
                country["name"],
                country["market_status"],
                country["status_note"],
            ]
        )
        r = sheet.max_row
        for j, topic in enumerate(OVERVIEW_TOPICS, start=OVERVIEW_FIRST_FACT_COLUMN):
            resolved = overview_fact(facts, topic)
            if resolved is None:
                continue
            status = as_status(resolved.fact["status"])
            value = resolved.fact["value"] or STATUS_LABEL[status]
            cell = sheet.cell(row=r, column=j, value=value)
            cell.fill = fill(status)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.comment = Comment(fact_title(resolved), "markets.py")
    sheet.freeze_panes = "C2"
    sheet.column_dimensions["A"].width = 8
    for j in range(2, OVERVIEW_FIRST_FACT_COLUMN):
        sheet.column_dimensions[get_column_letter(j)].width = 22
    last = OVERVIEW_FIRST_FACT_COLUMN + len(OVERVIEW_TOPICS)
    for j in range(OVERVIEW_FIRST_FACT_COLUMN, last):
        sheet.column_dimensions[get_column_letter(j)].width = 34


TODO_WIDTHS = (14, 12, 8, 34, 40, 70, 16, 40, 8)
CONTACT_WIDTHS = (34, 16, 16, 12, 40, 60)


def set_widths(sheet: Worksheet, widths: Sequence[int]) -> None:
    for j, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(j)].width = width


def wrap_row(sheet: Worksheet, row: int) -> None:
    for cell in sheet[row]:
        cell.alignment = Alignment(wrap_text=True, vertical="top")


def sheet_todo(book: Workbook, data: Data, today: date) -> None:
    sheet = book.create_sheet("To do")
    sheet.append(
        [
            "Priority",
            "Due",
            "Id",
            "To",
            "Subject",
            "Question",
            "Status",
            "Blocked by",
            "Rows",
        ]
    )
    bold_row(sheet, 1)
    for item in todo_items(data, today):
        q = item.question
        status = f"{q['status']} {q['sent_on']}".strip()
        due = f"{q['due_on']} (overdue)" if item.overdue else q["due_on"]
        sheet.append(
            [
                PRIORITY_LABEL[q["priority"]],
                due,
                q["question"],
                q["to"],
                q["subject"],
                q["text"],
                status,
                q["blocked_by"],
                item.waiting,
            ]
        )
        wrap_row(sheet, sheet.max_row)
        if item.overdue:
            sheet.cell(row=sheet.max_row, column=2).fill = fill("unverified")
    sheet.freeze_panes = "D2"
    set_widths(sheet, TODO_WIDTHS)


def sheet_contacts(book: Workbook, data: Data) -> None:
    sheet = book.create_sheet("Contacts")
    sheet.append(
        ["Provider", "Kind", "Relationship", "Last sent", "Questions", "Next step"]
    )
    bold_row(sheet, 1)
    for contact in contacts(data):
        p = contact.provider
        sheet.append(
            [
                f"{p['name']} ({p['provider']})",
                p["kind"],
                p["relationship"],
                contact.last_sent,
                "\n".join(question_trail(contact.questions)),
                contact.next_step,
            ]
        )
        wrap_row(sheet, sheet.max_row)
    sheet.freeze_panes = "B2"
    set_widths(sheet, CONTACT_WIDTHS)


def sheet_prices(book: Workbook, data: Data) -> None:
    cards = data.rows("rate_cards")
    sheet = book.create_sheet("Prices")
    sheet.append(
        [
            "Freight only in US$ (before fuel, duty-paid fee, surcharges, discount). "
            + USD_RATE_NOTE
        ]
    )
    for weight in COMPARE_WEIGHTS:
        sheet.append([])
        sheet.append([f"{weight} kg"] + [c["rate_card"] for c in cards])
        bold_row(sheet, sheet.max_row)
        for country in compare_countries(data):
            values: list[str] = [f"{country['iso2']} {country['name']}"]
            for card in cards:
                zone = zone_for(data, card["rate_card"], country["iso2"])
                rate = None
                if zone is not None:
                    rate = rate_for(data, card["rate_card"], zone, weight)
                values.append(usd_text(rate, card["currency"]) if rate else "")
            sheet.append(values)
            r = sheet.max_row
            for j, card in enumerate(cards, start=2):
                if sheet.cell(row=r, column=j).value:
                    sheet.cell(row=r, column=j).fill = fill(as_status(card["status"]))
    sheet.column_dimensions["A"].width = 28
    for j in range(2, len(cards) + 2):
        sheet.column_dimensions[get_column_letter(j)].width = 18


def sheet_all_in(book: Workbook, data: Data) -> None:
    cards = all_in_cards(data)
    express = [c for c in cards if c["rate_card"] in BREAK_EVEN_CARDS]
    sheet = book.create_sheet("All-in")
    sheet.append(
        [
            "All-in list price per parcel in US$ (freight, fuel, per-parcel fees, "
            "duty-paid fee where due; no duties or taxes, no discount). "
            "≥ lower bound, DAP buyer pays at the door. Comments hold the parts. "
            + USD_RATE_NOTE
        ]
    )
    for scenario in data.rows("scenarios"):
        grid = all_in_grid(data, scenario, cards)
        goods = money(int(scenario["goods_value_minor"]), scenario["currency"])
        sheet.append([])
        sheet.append(
            [f"{scenario['label']} {scenario['weight_kg']} kg, goods {goods}"]
            + [c["rate_card"] for c in cards]
            + ["cheapest usable economy"]
            + [f"discount {c['rate_card']}" for c in express]
        )
        bold_row(sheet, sheet.max_row)
        for code, results in zip(ALL_IN_DESTINATIONS, grid, strict=True):
            best = cheapest_benchmark(results, data)
            values: list[str] = [country_label(data, code)]
            for result in results:
                values.append(all_in_text(result) if result else "")
            values.append(
                f"{best.card['rate_card']} {all_in_text(best)}" if best else "none"
            )
            for card in express:
                result = results[cards.index(card)]
                values.append(
                    discount_text(break_even(result, best)) if result and best else ""
                )
            sheet.append(values)
            r = sheet.max_row
            for j, result in enumerate(results, start=2):
                if result is not None:
                    cell = sheet.cell(row=r, column=j)
                    cell.fill = fill(result.status)
                    cell.comment = Comment(all_in_title(result), "markets.py")
    sheet.column_dimensions["A"].width = 30
    for j in range(2, len(cards) + len(express) + 3):
        sheet.column_dimensions[get_column_letter(j)].width = 18


def build_xlsx(data: Data, today: date) -> Workbook:
    book = Workbook()
    book.remove(book.worksheets[0])  # the default empty sheet
    sheet_readme(book, today)
    sheet_overview(book, data)
    sheet_todo(book, data, today)
    sheet_contacts(book, data)
    sheet_prices(book, data)
    sheet_all_in(book, data)
    for name, columns in SCHEMA.items():
        write_raw(book.create_sheet(name[:31]), columns, data.rows(name))
    return book


# ---------------------------------------------------------------- main


type Command = Literal["check", "build"]


def parse(argv: Sequence[str]) -> Command | None:
    if len(argv) != ARGC:
        return None
    match argv[1]:
        case "check":
            return "check"
        case "build":
            return "build"
        case _:
            return None


def main(argv: Sequence[str]) -> int:
    command = parse(argv)
    if command is None:
        print("usage: uv run scripts/markets.py check|build", file=sys.stderr)
        return 2
    data, errors = load()
    if not errors:
        errors = check(data)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"{len(errors)} error(s)", file=sys.stderr)
        return 1
    counts = status_counts(data)
    summary = ", ".join(f"{status} {n}" for status, n in counts.items())
    print(
        f"ok: {len(data.rows('countries'))} countries, "
        f"{len(data.rows('facts'))} facts ({summary}), "
        f"{len(data.rows('rates'))} rates"
    )
    match command:
        case "check":
            return 0
        case "build":
            today = datetime.now(UTC).date()
            HTML_OUT.write_text(build_html(data, today), encoding="utf-8")
            XLSX_OUT.parent.mkdir(parents=True, exist_ok=True)
            build_xlsx(data, today).save(XLSX_OUT)
            print(
                f"wrote {HTML_OUT.relative_to(ROOT)} and {XLSX_OUT.relative_to(ROOT)}"
            )
            return 0
        case _:
            assert_never(command)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
