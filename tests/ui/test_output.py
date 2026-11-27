"""출력 계층: 규격(contract), 출력 FSM(C), 상태 서버(C), 출력 프로그램(D)."""

import json
import threading
import urllib.request
from dataclasses import replace

import pytest

from psafe.common.clock import FakeClock, RealClock
from psafe.common.config import ConfigError
from psafe.common.packets import State
from psafe.common.verdict import NodeView, Verdict, VerdictKind
from psafe.ui.client import (
    LightSink, OutputApp, StateReceiver, VoiceSink, parse_output_config, read_events, signal_decision,
)
from psafe.ui.contract import (
    VERSION, ContractError, Display, Estimate, Signal, StateMessage, Voice, from_json, to_dict, to_json,
)
from psafe.ui.esp32.protocol import Receiver
from psafe.ui.output_fsm import OutputFsm, OutputFsmConfig, OutState
from psafe.ui.samples import render, sample_messages
from psafe.ui.state_server import StateServer, compose

K = VerdictKind


def node(name, state=State.CLEAR, count=0, alive=True):
    if not alive:
        return NodeView(name, False, None, 0, "timeout", None, None)
    return NodeView(name, True, state, count, "clear", 200.0, None)


def verdict(kind, reason="r", nodes=None, t=0.0):
    return Verdict(kind, reason, tuple(nodes or (node("A"), node("B"))), None, t)


def msg(kind="EMPTY", level="IDLE", color="green", permit=True, seq=1, voice=None):
    return StateMessage(seq, "t", "S", Display(level, color, "t", "m"), voice or Voice(None, 0),
                        Signal(color, permit), kind, "r")


# ---- 규격 ------------------------------------------------------------------

def test_contract_roundtrip_and_ignores_unknown_fields():
    m = sample_messages()["occupied"]
    o = to_dict(m)
    o["future_field"] = {"x": 1}
    assert from_json(json.dumps(o)) == m
    assert from_json(to_json(m)) == m


@pytest.mark.parametrize("kwargs", [
    {"kind": "OCCUPIED"},                                     # 초록인데 EMPTY가 아님
    {"level": "IDLE", "color": "red", "permit": False},       # level과 color 불일치
    {"kind": "UNKNOWN", "level": "BLOCKED", "color": "red", "permit": True},   # 빨강인데 허가
    {"kind": "CHECKING", "level": "WARNING", "color": "yellow", "permit": True},
])
def test_contract_rejects_unsafe_messages(kwargs):
    with pytest.raises(ContractError):
        msg(**kwargs)


@pytest.mark.parametrize("mutate", [
    lambda o: o.update(v=VERSION + 1),
    lambda o: o["signal"].update(permit="yes"),
    lambda o: o["display"].pop("level"),
    lambda o: o.update(nodes=[{"name": "A"}]),
    lambda o: o["signal"].update(light="yellow"),             # 화면과 신호등 색이 다름
])
def test_from_json_rejects_bad_messages(mutate):
    o = to_dict(sample_messages()["idle"])
    mutate(o)
    with pytest.raises(ContractError):
        from_json(json.dumps(o))
    with pytest.raises(ContractError):
        from_json("not json")


def test_samples_on_disk_are_up_to_date():
    from pathlib import Path
    for name, m in sample_messages().items():
        p = Path("web/screen/samples") / f"{name}.json"
        assert p.read_text(encoding="utf-8") == render(m), f"{p}: run python -m psafe.ui.samples"


# ---- 출력 FSM (C) ---------------------------------------------------------

def test_fsm_maps_verdicts_and_permit_only_on_empty():
    fsm = OutputFsm()
    assert fsm.frame().state == OutState.STARTING and fsm.frame().display.color == "red"
    expect = {K.EMPTY: (OutState.IDLE, "green", True), K.CHECKING: (OutState.CHECKING, "yellow", False),
              K.OCCUPIED: (OutState.OCCUPIED, "yellow", False), K.UNKNOWN: (OutState.UNKNOWN, "red", False)}
    for t, (kind, (state, color, permit)) in enumerate(expect.items()):
        f = fsm.step(verdict(kind), float(t))
        assert (f.state, f.display.color, f.permit) == (state, color, permit)
    assert fsm.step(verdict(K.UNKNOWN, "shutdown"), 9.0).state == OutState.SHUTDOWN


def test_fsm_permit_cannot_be_enabled_by_looks():
    from psafe.ui.output_fsm import Look
    fsm = OutputFsm(looks={OutState.UNKNOWN: Look("IDLE", "x", "y")})   # 잘못된 표시 설정
    f = fsm.step(verdict(K.UNKNOWN), 0.0)
    assert f.permit is False
    with pytest.raises(ContractError):                                  # 초록 UNKNOWN은 규격이 막는다
        compose(f, verdict(K.UNKNOWN), 1, "t")


def test_fsm_occupied_blocks_after_configured_time():
    fsm = OutputFsm(OutputFsmConfig(occupied_block_s=5.0))
    assert fsm.step(verdict(K.OCCUPIED), 0.0).state == OutState.OCCUPIED
    assert fsm.step(verdict(K.OCCUPIED), 4.9).state == OutState.OCCUPIED
    f = fsm.step(verdict(K.OCCUPIED), 5.0)
    assert (f.state, f.display.level, f.display.action) == (OutState.OCCUPIED_BLOCKED, "BLOCKED", "call_admin")
    assert fsm.step(verdict(K.CHECKING), 6.0).state == OutState.CHECKING
    assert fsm.step(verdict(K.OCCUPIED), 7.0).state == OutState.OCCUPIED     # 타이머는 다시 시작


def test_fsm_never_blocks_occupied_by_default():
    fsm = OutputFsm()
    assert fsm.step(verdict(K.OCCUPIED), 0.0).state == OutState.OCCUPIED
    assert fsm.step(verdict(K.OCCUPIED), 3600.0).state == OutState.OCCUPIED


def test_fsm_voice_escalates_now_relaxes_after_gap():
    fsm = OutputFsm(OutputFsmConfig(voice_gap_s=3.0))
    f = fsm.step(verdict(K.EMPTY), 0.0)
    assert (f.voice_name, f.voice_seq) == ("idle", 1)
    f = fsm.step(verdict(K.OCCUPIED), 1.0)                     # 더 엄격 -> 즉시
    assert (f.voice_name, f.voice_seq) == ("occupied", 2)
    f = fsm.step(verdict(K.CHECKING), 1.5)                     # 풀어짐, 간격 전 -> 미룸
    assert f.voice_seq == 2
    f = fsm.step(verdict(K.CHECKING), 4.0)                     # 간격이 지나면 말한다
    assert (f.voice_name, f.voice_seq) == ("checking", 3)
    f = fsm.step(verdict(K.OCCUPIED), 4.2)
    f = fsm.step(verdict(K.CHECKING), 4.4)                     # 빠르게 오감
    f = fsm.step(verdict(K.OCCUPIED), 4.6)                     # 미룬 checking은 버리고 occupied는 즉시
    assert (f.voice_name, f.voice_seq) == ("occupied", 5)


def test_fsm_voice_repeats_only_configured_states():
    fsm = OutputFsm(OutputFsmConfig(voice_repeat_s={"OCCUPIED": 10.0}))
    seqs = [fsm.step(verdict(K.OCCUPIED), float(t)).voice_seq for t in range(0, 31)]
    assert seqs[0] == 1 and seqs[10] == 2 and seqs[20] == 3 and seqs[30] == 4 and seqs[9] == 1
    fsm2 = OutputFsm()
    assert fsm2.step(verdict(K.CHECKING), 0.0).voice_seq == fsm2.step(verdict(K.CHECKING), 100.0).voice_seq


def test_fsm_config_validation():
    with pytest.raises(ValueError):
        OutputFsmConfig(voice_repeat_s={"NOPE": 1.0})
    with pytest.raises(ValueError):
        OutputFsmConfig(occupied_block_s=-1)


def test_compose_estimate_uses_max_per_role_and_none_when_unknown():
    nodes = (node("A", State.OCCUPIED, 2), node("B", State.OCCUPIED, 1), node("RGB", alive=False))
    v = verdict(K.OCCUPIED, nodes=nodes)
    m = compose(OutputFsm().step(v, 0.0), v, 1, "t", frozenset({"RGB"}))
    assert m.estimate == Estimate(2, None)                   # 더하지 않음, 모르면 None
    assert [n.role for n in m.nodes] == ["interior", "interior", "exterior"]
    fault = (node("A", State.FAULT), node("B", alive=False))
    v = verdict(K.UNKNOWN, nodes=fault)
    assert compose(OutputFsm().step(v, 0.0), v, 1, "t").estimate == Estimate(None, None)


# ---- 출력 프로그램 (D) ----------------------------------------------------

def test_signal_to_esp32_packet():
    assert (lambda d: (d.state, d.safe))(signal_decision(Signal("green", True), 0)) == (State.CLEAR, True)
    assert (lambda d: (d.state, d.safe))(signal_decision(Signal("green", False), 0)) == (State.CLEAR, False)
    assert (lambda d: (d.state, d.safe))(signal_decision(Signal("yellow", False), 0)) == (State.VERIFYING, False)
    assert (lambda d: (d.state, d.safe))(signal_decision(Signal("red", True), 0)) == (State.FAULT, False)


def test_receiver_goes_red_when_stale_or_bad():
    clock = FakeClock()
    rx = StateReceiver(clock, stale_ms=1000)
    assert rx.current() == (None, "connecting")
    rx.feed(to_json(msg(seq=5)))
    assert rx.current()[1] == "ok"
    clock.advance(1.01)
    assert rx.current() == (None, "link_lost")
    rx.feed(to_json(msg(seq=4)))                             # 같은 연결에서 오래된 seq -> 무시
    assert rx.current() == (None, "link_lost") and rx.counts["old_seq"] == 1
    rx.new_connection()                                      # C 재시작: 새 연결의 첫 메시지는 받는다
    rx.feed(to_json(msg(seq=1)))
    assert rx.current()[1] == "ok"
    rx.feed('{"v": 99}')
    assert rx.current() == (None, "bad_message")


def test_light_sink_follows_signal_and_fails_safe():
    clock = FakeClock()
    sink = LightSink(clock, output="mock_mcu")
    mcu = sink.inner
    for _ in range(3):
        sink.render(msg(), "ok")
        clock.advance(0.2)
    assert mcu.gate is True
    sink.render(None, "link_lost")
    assert mcu.gate is False
    sink.render(msg(kind="OCCUPIED", level="WARNING", color="yellow", permit=False), "ok")
    assert mcu.gate is False


def test_voice_sink_plays_on_seq_change_and_once_on_loss():
    sink = VoiceSink(player="log")
    played = sink.player.played
    sink.render(None, "connecting")                          # 시작 중에는 말하지 않는다
    sink.render(msg(voice=Voice("idle", 1)), "ok")
    sink.render(msg(voice=Voice("idle", 1)), "ok")
    sink.render(None, "link_lost")
    sink.render(None, "link_lost")
    sink.render(msg(voice=Voice("idle", 1)), "ok")           # 복구: 같은 seq는 다시 말하지 않음
    sink.render(msg(voice=Voice("checking", 2)), "ok")
    assert played == ["idle", "link_lost", "checking"]


def test_output_config_parsing():
    cfg = parse_output_config({"output_client": {"server": "http://10.0.0.3:8080"},
                               "sinks": [{"name": "light"}, {"name": "voice", "options": {"player": "log"}}]})
    assert cfg.client.stale_ms == 1000 and [s.name for s in cfg.sinks] == ["light", "voice"]
    for bad in ({"output_client": {}}, {"output_client": {"server": "10.0.0.3:8080"}},
                {"output_client": {"server": "http://c:1", "typo": 1}},
                {"output_client": {"server": "http://c:1", "heartbeat_ms": 600}},
                {"output_client": {"server": "http://c:1"}, "extra": 1}):
        with pytest.raises(ConfigError):
            parse_output_config(bad)


def test_repo_output_config_loads():
    from psafe.common.config import read_yaml
    cfg = parse_output_config(read_yaml("config/output_d.yaml"))
    app = OutputApp(cfg, FakeClock())
    assert [type(s).__name__ for s in app.sinks] == ["LightSink", "VoiceSink"]


# ---- C 상태 서버 <-> D (실제 HTTP, localhost) -------------------------------

@pytest.fixture
def server(tmp_path):
    (tmp_path / "index.html").write_text("<h1>screen</h1>", encoding="utf-8")
    s = StateServer(RealClock(), bind="127.0.0.1:0", static_dir=str(tmp_path))
    s.open()
    yield s
    s.close()


def _get(server, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}{path}", timeout=2) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def test_server_state_static_and_sos(server):
    status, ctype, body = _get(server, "/api/state")
    m = from_json(body)
    assert status == 200 and "json" in ctype and m.state == "STARTING" and m.signal.permit is False
    server.show(verdict(K.EMPTY))
    assert from_json(_get(server, "/api/state")[2]).signal == Signal("green", True)
    assert _get(server, "/")[2] == b"<h1>screen</h1>"
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(server, "/../secret.txt")
    assert e.value.code == 404
    req = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/sos", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=2)
    assert e.value.code == 501


def test_server_streams_to_client_and_shutdown_is_last(server):
    got: list[StateMessage] = []
    stop = threading.Event()
    rx = threading.Thread(target=read_events, args=(f"http://127.0.0.1:{server.port}", lambda d: got.append(from_json(d)), stop))
    rx.start()
    for _ in range(50):                                      # 구독될 때까지 (첫 메시지 = STARTING)
        if got:
            break
        stop.wait(0.02)
    server.show(verdict(K.OCCUPIED))
    server.show(verdict(K.EMPTY))
    server.show(verdict(K.UNKNOWN, "shutdown"))
    server.close()                                           # 마지막 메시지를 보낸 뒤 스트림을 닫는다
    rx.join(3)
    assert not rx.is_alive()
    assert [m.state for m in got] == ["STARTING", "OCCUPIED", "IDLE", "SHUTDOWN"]
    assert [m.seq for m in got] == [0, 1, 2, 3]
    server.close = lambda: None                              # fixture가 다시 닫지 않게


def test_end_to_end_c_to_d_light_goes_red_when_c_stops(server):
    """C 상태 서버 -> D 출력 프로그램 -> 가짜 ESP32. C가 멈추면 1초 뒤 D가 빨간불."""
    clock = RealClock()
    cfg = parse_output_config({"output_client": {"server": f"http://127.0.0.1:{server.port}", "stale_ms": 300,
                                                 "heartbeat_ms": 50, "reconnect_s": 0.1},
                               "sinks": [{"name": "light"}]})
    app = OutputApp(cfg, clock)
    mcu = app.sinks[0].inner
    t = threading.Thread(target=app.run, kwargs={"max_seconds": 5}, daemon=True)
    t.start()
    stop = threading.Event()
    for _ in range(20):                                      # C: 1초 동안 EMPTY
        server.show(verdict(K.EMPTY))
        stop.wait(0.05)
    assert mcu.gate is True
    stop.wait(0.8)                                           # C가 판정을 멈춤 (서버는 살아 있음)
    assert mcu.gate is False and app.receiver.current()[1] == "link_lost"
    app.stop()
    t.join(3)
    assert mcu.transitions[-1][1] is False
