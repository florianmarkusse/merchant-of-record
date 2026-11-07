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
        "to",
        "subject",
        "text",
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

# Minor-unit exponents for the currencies the tables use
# (AGENTS.md: one table, never "divide by 100").
EXPONENT: dict[str, int] = {"USD": 2, "VND": 0, "EUR": 2, "SGD": 2}
# Display-only conversion to compare cards; the page shows the rate and source.
USD_PER_UNIT: dict[str, Fraction] = {"USD": Fraction(1), "VND": Fraction(1, 26_300)}
USD_RATE_NOTE = (
    "VND converted at 26,300 per US$ (research/logistics/02, estimate), "
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
    errors.extend(check_questions(data))
    errors.extend(check_facts(data, known))
    errors.extend(check_inheritance(data, known))
    errors.extend(check_services(data, known))
    errors.extend(check_rate_cards(data, known))
    errors.extend(check_zones_and_rates(data, known))
    errors.extend(check_surcharges(data, known))
    errors.extend(check_transit(data, known))
    errors.extend(check_restrictions(data, known))
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


def check_questions(data: Data) -> list[str]:
    errors: list[str] = []
    for i, row in enumerate(data.rows("questions")):
        errors.extend(one_of("questions", i, row, "kind", QUESTION_KINDS))
        errors.extend(one_of("questions", i, row, "status", QUESTION_STATUSES))
        if row["status"] == "answered" and not row["answer"]:
            errors.append(f"{where('questions', i)}: answered without an answer")
        errors.extend(check_dates("questions", i, row, ("sent_on", "answered_on")))
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


def section_questions(data: Data) -> h.Element:
    waiting: dict[str, int] = {}
    for table in ("facts", "services", "rate_cards", "surcharges"):
        for row in data.rows(table):
            question = row.get("question", "")
            if question:
                waiting[question] = waiting.get(question, 0) + 1
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
                        f" Valid {card['effective_from'] or '?'} to "
                        f"{card['effective_to'] or 'open'}. "
                        f"Excludes: {card['excludes'] or 'not stated'}. ",
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
                    h.a(href="#details")["Facts per country"],
                    h.a(href="#questions")["Open questions"],
                    h.a(href="#compare")["Price comparison"],
                    h.a(href="#carriers")["Providers"],
                    h.a(href="#rates")["Rate cards"],
                    h.a(href="#surcharges")["Surcharges"],
                    h.a(href="#transit")["Transit"],
                    h.a(href="#countries")["All countries"],
                ],
                section_overview(data),
                section_details(data),
                section_questions(data),
                section_compare(data),
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
            f"Generated {today.isoformat()} from docs/markets/*.csv "
            "(scripts/markets.py). The CSVs in the repo are the source of truth."
        ]
    )
    sheet.append(
        [
            "Sheets after 'Prices' are the CSVs with the same columns, so edits "
            "here can be read back later."
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


def build_xlsx(data: Data, today: date) -> Workbook:
    book = Workbook()
    book.remove(book.worksheets[0])  # the default empty sheet
    sheet_readme(book, today)
    sheet_overview(book, data)
    sheet_prices(book, data)
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
