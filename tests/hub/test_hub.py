"""판정 Pi(C) 허브: 설정 검사, Hub.poll/tick 판정 흐름. (카메라 노드와 함께 도는 통합 시험은 tests/integration)"""

import pytest

from psafe.common.clock import FakeClock
from psafe.common.config import ConfigError, is_hub_config, load_hub_config, parse_hub_config
from psafe.common.packets import State
from psafe.common.verdict import VerdictKind as K
from psafe.hub import Hub
from psafe.common.messages import Control, Report, decode, encode
from psafe.common.transport import LoopbackNetwork
from psafe.common.output_api import VerdictOutput


class Rec(VerdictOutput):
    def __init__(self):
        self.got = []

    def show(self, v):
        self.got.append(v)


def hub_cfg(**hub):
    return parse_hub_config({
        "hub": {"bind": "c:5005", "nodes": [{"name": "A", "address": "a:5006"}, {"name": "B", "address": "b:5007"}], **hub},
        "outputs": [],
    })


def test_repo_hub_config_loads():
    cfg = load_hub_config("config/hub_c.yaml")
    assert [n.name for n in cfg.hub.nodes] == ["A", "B"]
    assert [o.name for o in cfg.outputs] == ["console", "decision"]


@pytest.mark.parametrize("hub", [
    {"nodes": []},
    {"nodes": [{"name": "A", "address": "a:1"}, {"name": "A", "address": "a:2"}]},
    {"node": "A"},
    {"bind": "nope"},
    {"heartbeat_ms": 0},
    {"typo": 1},
])
def test_hub_config_validation(hub):
    base = {"bind": "c:5005", "nodes": [{"name": "A", "address": "a:5006"}]}
    with pytest.raises(ConfigError):
        parse_hub_config({"hub": {**base, **hub}})


def test_is_hub_config():
    assert is_hub_config({"hub": {}}) and not is_hub_config({"source": {}})


def test_hub_tick_flow_with_loopback():
    clock = FakeClock()
    net = LoopbackNetwork(clock)
    a, b = net.endpoint(("a", 5006)), net.endpoint(("b", 5007))
    a.open(), b.open()
    rec = Rec()
    hub = Hub(hub_cfg(), clock, transport=net.endpoint(("c", 5005)), outputs=[rec], first_confirm_id=50)
    hub.transport.open()

    def report(ep, node, seq, cid=None, confirm=None, echo=None, hold=None):
        ep.send(encode(Report(node, "b" + node, seq, State.CLEAR, True, 0, "clear", 100, cid, confirm, echo, hold)),
                ("c", 5005))

    assert hub.tick().kind == K.UNKNOWN                                     # 아무도 없음
    first = decode(a.recv(0)[0])                                            # 첫 Control (seq는 임의 시작값)
    decode(b.recv(0)[0])
    report(a, "A", 1), report(b, "B", 1)                                    # 아직 echo 없음
    while hub.poll(0):
        pass
    assert hub.tick().reason == "A_no_rtt"                                  # 왕복 시간을 모르면 EMPTY 쪽으로 못 감
    clock.advance(0.03)
    report(a, "A", 2, echo=first.seq, hold=10), report(b, "B", 2, echo=first.seq, hold=10)
    while hub.poll(0):
        pass
    v = hub.tick()
    assert (v.kind, v.confirm_id) == (K.CHECKING, 50)
    assert v.nodes[0].transit_ms == pytest.approx(20)                       # 30 ms 왕복 - 노드가 들고 있던 10 ms
    while (m := a.recv(0)) is not None:
        ctl = decode(m[0])
    assert isinstance(ctl, Control) and ctl.confirm_id == 50 and decode(b.recv(0)[0]).node == "C"
    clock.advance(0.2)
    report(a, "A", 3, 50, "empty", ctl.seq, 0), report(b, "B", 3, 50, "empty", ctl.seq, 0)
    a.send(b"garbage", ("c", 5005))
    while hub.poll(0):
        pass
    assert hub.tick().kind == K.EMPTY
    assert hub.counts["bad_crc"] == 1
    assert [x.kind for x in rec.got] == [K.UNKNOWN, K.UNKNOWN, K.CHECKING, K.EMPTY]


def test_old_or_unknown_echo_gives_no_rtt():
    """C가 기억하지 못하는 Control seq(재시작 전 C, 아주 오래된 것)를 echo하면 왕복 시간을 모른다."""
    clock = FakeClock()
    net = LoopbackNetwork(clock)
    a = net.endpoint(("a", 5006))
    a.open()
    hub = Hub(hub_cfg(), clock, transport=net.endpoint(("c", 5005)), outputs=[])
    hub.transport.open()
    hub.tick()
    a.send(encode(Report("A", "x", 1, State.CLEAR, True, 0, "clear", 50, None, None, 123, 0)), ("c", 5005))
    hub.poll(0)
    assert hub.tracker.latest("A", clock.now()).transit_s is None and hub.counts["no_echo"] == 1
