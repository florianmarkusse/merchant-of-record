import pytest

from mor.messaging.outcome import Stale
from mor.probe.flow import ProbeState, after_echo, after_finish


def test_echo_moves_a_started_probe_and_is_stale_after() -> None:
    assert after_echo(ProbeState.STARTED) == ProbeState.ECHOED
    assert after_echo(ProbeState.ECHOED) == Stale()
    assert after_echo(ProbeState.FINISHED) == Stale()


def test_finish_moves_an_echoed_probe_and_is_stale_after() -> None:
    assert after_finish(ProbeState.ECHOED) == ProbeState.FINISHED
    assert after_finish(ProbeState.FINISHED) == Stale()


def test_finish_before_echo_is_a_bug() -> None:
    with pytest.raises(AssertionError):
        after_finish(ProbeState.STARTED)
