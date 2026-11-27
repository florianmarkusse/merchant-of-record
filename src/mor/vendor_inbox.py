"""What every vendor inbox shares (docs/plan-messaging.md §7): the PSP's,
the bank's, the carrier's and the echo flow's.

Each inbox keeps a vendor's raw bytes before anything decodes them, unique
per account and body hash, with the fact that starts its decoding. The
hash only means "these bytes are stored already" (a webhook resent, a page
fetched again); the guard against a duplicate event is the vendor's own
id, where the decoded event is recorded. The tables, their facts and what
decoding does differ per inbox, so each inbox module writes its own
`store_inbox` and decoder; the results, the failure and the signature
check are here.
"""

import hashlib
import hmac
from dataclasses import dataclass


@dataclass(frozen=True)
class Stored[InboxId]:
    """The bytes are new: stored, with the fact that decodes them."""

    inbox_id: InboxId


@dataclass(frozen=True)
class AlreadyStored:
    """The same bytes arrived before: nothing new was written."""


class UndecodableInbox(Exception):
    """Stored bytes the decoder cannot read. The raw row stays; the delivery
    fails visibly and is retried, so a fixed decoder handles it later."""


def body_sha256(body: bytes) -> str:
    """The inbox key's hash of the raw bytes, hex."""
    return hashlib.sha256(body).hexdigest()


def hmac_sha256_signed(secret: str, body: bytes, signature: str | None) -> bool:
    """Whether `signature` is the HMAC-SHA256 of the raw body under the
    account's secret, hex, as the fakes sign their webhooks. Compared in
    constant time; no secret or no signature is never signed. A provider
    module whose vendor signs this way calls it."""
    if signature is None or secret == "":
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
