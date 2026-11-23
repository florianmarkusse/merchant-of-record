from mor.echo.port import EchoProvider
from mor.messaging.guard import DuplicateGuard


def send_guard(provider: EchoProvider) -> DuplicateGuard:
    """What each provider promises about a send made twice. Cancel needs no
    guard: cancelling an echo twice leaves it cancelled."""
    match provider:
        case EchoProvider.FAKE_KEYED:
            return DuplicateGuard.VENDOR_CONSTRAINT
        case EchoProvider.FAKE_CANCELLABLE:
            return DuplicateGuard.COMPENSABLE
        case EchoProvider.FAKE_PLAIN:
            return DuplicateGuard.NONE
