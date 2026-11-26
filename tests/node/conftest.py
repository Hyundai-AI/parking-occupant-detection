"""node 테스트가 함께 쓰는 도우미."""

import pytest

from psafe.common.config import parse_config
from psafe.node.app import build
from psafe.node.detectors.dummy import DummyDetector
from psafe.node.sources.scenario import parse_scenario


class CountingDetector(DummyDetector):
    """predict() 호출 횟수를 센다 (추론을 건너뛰었는지 확인용)."""

    def __init__(self, clock):
        super().__init__(clock)
        self.calls = 0

    def predict(self, pkt):
        self.calls += 1
        return super().predict(pkt)


def _make_components():
    sc = parse_scenario({"fps": 10, "timeline": [{"from": 0, "to": 1, "objects": []}]})
    cfg = parse_config({
        "source": {"name": "dummy", "options": {"scenario": "unused", "width": 64, "height": 48}},
        "model": {"name": "dummy"},
        "outputs": [],
    })
    cfg.source.options["scenario"] = sc
    c = build(cfg)
    c.detector = CountingDetector(c.clock)
    return c


@pytest.fixture
def make_components():
    """작은 가상 장면(64x48) + 호출 횟수를 세는 검출기로 부품 묶음을 만드는 함수."""
    return _make_components
