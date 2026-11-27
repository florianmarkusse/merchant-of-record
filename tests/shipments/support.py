"""Shared setup for the shipment tests: the carrier every service of the
quote set names, a fault scripted at the fake carrier, and the session's
shipment."""

from mor.db.connect import connect
from mor.ids import CarrierCode
from mor.orders.store import order_by_session
from mor.shipments.records import Shipment
from mor.shipments.store import order_shipment
from tests.payments.support import Setup


CARRIER = CarrierCode("carrier")  # every service of the quote set's


async def fault(setup: Setup, script: str, operation: str = "book") -> None:
    """The fake carrier's next `operation` fails as `script` says."""
    response = await setup.wired.control.post(
        "http://mor/_fake/carrier/faults",
        json={"fault": script, "operation": operation},
    )
    assert response.status_code == 204, response.text


async def shipment_of_session(setup: Setup) -> Shipment:
    """The shipment of the order the setup's session became."""
    async with await connect(setup.url) as conn:
        order = await order_by_session(conn, setup.session_id)
        assert order is not None
        shipment = await order_shipment(conn, order.id)
    assert shipment is not None
    return shipment
