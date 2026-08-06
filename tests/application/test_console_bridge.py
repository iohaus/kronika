from __future__ import annotations

import sys
from pathlib import Path

import pytest

console_dir = str(Path(__file__).parent.parent.parent / "kronika-console")
if console_dir not in sys.path:
    sys.path.insert(0, console_dir)

from bridge import ConsoleBridge  # noqa: E402
from PyQt6.QtCore import QCoreApplication  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def test_console_bridge_initialization(qt_app) -> None:
    bridge = ConsoleBridge()
    assert bridge.assetCount > 0
    assert len(bridge.graphNodes) == 4
    assert len(bridge.graphEdges) == 3
    assert bridge.systemStatus == "HEALTHY"
    assert bridge.selectedAsset.get("name") is not None


def test_console_bridge_anomaly_and_containment(qt_app) -> None:
    bridge = ConsoleBridge()
    raw_urn = "urn:li:dataset:(urn:li:dataPlatform:hive,raw_patients,PROD)"

    signal_received = []

    def on_prop(src, dst, impact):
        signal_received.append((src, dst, impact))

    bridge.propagationStep.connect(on_prop)

    bridge.triggerQualityAnomaly(raw_urn, "billing_amount")

    assert bridge.systemStatus == "CONTAINED"
    ep = bridge.currentEpisode
    assert ep["status"] == "CONTAINMENT_RECOMMENDED"
    assert len(ep["halt_set"]) >= 1
    assert any("mart_billing" in h for h in ep["halt_set"])
    assert len(signal_received) > 0

    pending = bridge.pendingActions
    assert len(pending) >= 1
    action_id = pending[0]["action_id"]

    bridge.approveAction(action_id)
    assert len(bridge.pendingActions) == len(pending) - 1


def test_console_bridge_reset(qt_app) -> None:
    bridge = ConsoleBridge()
    raw_urn = "urn:li:dataset:(urn:li:dataPlatform:hive,raw_patients,PROD)"

    bridge.triggerQualityAnomaly(raw_urn, "billing_amount")
    assert bridge.systemStatus == "CONTAINED"

    bridge.resetDemo()
    assert bridge.systemStatus == "HEALTHY"
    assert bridge.currentEpisode["status"] == "IDLE"
