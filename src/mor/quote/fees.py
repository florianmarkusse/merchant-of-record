"""Which fee rule applies to a buyer's country on a day (docs/plan-quote.md
§4, plan-architecture §8). Pure. Per merchant overrides come with
merchants (4d); until then the rows are per country, group or default."""

from datetime import date

from mor.ids import CountryCode, GroupCode
from mor.markets.records import FeeComponent, FeeRule, MarketSet


def fee_rule_for(
    market: MarketSet, component: FeeComponent, destination: CountryCode, on: date
) -> FeeRule | None:
    """The most specific rule started on or before `on`: the country's,
    else a group's it is in, else the default; within one place the latest
    start wins."""
    groups: set[GroupCode] = set()
    for group in market.groups:
        if destination in group.members:
            groups.add(group.code)
    for level in ("country", "group", "default"):
        best: FeeRule | None = None
        for rule in market.fees:
            if rule.component != component or rule.effective_from > on:
                continue
            if not _at_level(rule, level, destination, groups):
                continue
            if best is None or rule.effective_from > best.effective_from:
                best = rule
        if best is not None:
            return best
    return None


def _at_level(
    rule: FeeRule, level: str, destination: CountryCode, groups: set[GroupCode]
) -> bool:
    match level:
        case "country":
            return rule.scope is not None and rule.scope.country == destination
        case "group":
            return rule.scope is not None and rule.scope.group in groups
        case _:
            return rule.scope is None
