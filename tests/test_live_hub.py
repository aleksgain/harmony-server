"""Optional: talks to a real hub on the LAN. Set HARMONY_TEST_HUB_IP to enable."""

from __future__ import annotations

import os

import pytest

from harmony_server.local_control import hub_client

HUB_IP = os.environ.get("HARMONY_TEST_HUB_IP")
pytestmark = pytest.mark.skipif(not HUB_IP, reason="HARMONY_TEST_HUB_IP not set")


async def test_live_status() -> None:
    assert HUB_IP
    st = await hub_client.status(HUB_IP)
    assert st["hubId"]


async def test_live_config_round_trips_into_our_model() -> None:
    from harmony_server.hub.models import HubConfigIn

    assert HUB_IP
    cfg = await hub_client.read_config(HUB_IP)
    parsed = HubConfigIn.model_validate(cfg)
    assert parsed.devices or parsed.activities
