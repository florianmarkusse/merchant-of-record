"""Client for the fake duty vendor's API (mor.fakes.duty), shaped like any
real provider module: our records in, our result union out, never our
database.

How a call ends:
- connection refused or no such host: `Unavailable`, it never left;
- a timeout (bounded by the account's timeout), a dropped connection or a
  5xx: `OutcomeUnknown`;
- 404 `unknown_code`: `UnknownCode`;
- anything else it does not document: `UndecodableBody`.
"""

import asyncio
from fractions import Fraction

import httpx2
import msgspec

from mor.duties.port import (
    AskResult,
    DutyVendorAccount,
    RateQuestion,
    UnknownCode,
    VendorRate,
    VendorRates,
    percent_formula,
)
from mor.markets.records import Charge
from mor.vendor_failures import OutcomeUnknown, Unavailable, UndecodableBody


class _Line(msgspec.Struct, frozen=True):
    formula: str
    note: str | None = None


class _Answer(msgspec.Struct, frozen=True):
    hs_code_used: str
    duties: list[_Line]
    taxes: list[_Line]


class _Error(msgspec.Struct, frozen=True):
    error: str


def _rate(charge: Charge, lines: list[_Line]) -> VendorRate:
    """One charge from the vendor's lines: none is a zero rate, one is read
    as it is, several are kept as text for a person."""
    if not lines:
        return VendorRate(charge=charge, formula="none", rate=Fraction(0), note=None)
    if len(lines) == 1:
        line = lines[0]
        return VendorRate(
            charge=charge,
            formula=line.formula,
            rate=percent_formula(line.formula),
            note=line.note,
        )
    formulas: list[str] = []
    notes: list[str] = []
    for line in lines:
        formulas.append(line.formula)
        if line.note:
            notes.append(line.note)
    return VendorRate(
        charge=charge,
        formula=" + ".join(formulas),
        rate=None,
        note="; ".join(notes) or None,
    )


async def ask_rates(
    http: httpx2.AsyncClient, account: DutyVendorAccount, question: RateQuestion
) -> AskResult:
    try:
        async with asyncio.timeout(account.timeout.total_seconds()):
            response = await http.post(
                account.base_url + "/rates",
                json={
                    "destination": question.destination,
                    "origin": question.origin,
                    "hs_code": question.hs_code,
                },
            )
    except httpx2.ConnectError as error:
        return Unavailable(repr(error))
    except (TimeoutError, httpx2.TransportError) as error:
        return OutcomeUnknown(repr(error))
    if response.status_code >= 500:
        return OutcomeUnknown(f"status {response.status_code}")
    if response.status_code == 404:
        try:
            error = msgspec.json.decode(response.content, type=_Error)
        except msgspec.DecodeError:
            return UndecodableBody("404 without an error body")
        if error.error == "unknown_code":
            return UnknownCode()
        return UndecodableBody(f"404 {error.error}")
    if response.status_code != 200:
        return UndecodableBody(f"status {response.status_code}")
    try:
        answer = msgspec.json.decode(response.content, type=_Answer)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    return VendorRates(
        answer.hs_code_used,
        (_rate(Charge.DUTY, answer.duties), _rate(Charge.IMPORT_TAX, answer.taxes)),
    )
