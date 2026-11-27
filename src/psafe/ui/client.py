"""출력 프로그램 (Pi D). C의 상태 스트림을 받아 신호등·음성으로 그대로 출력한다 (docs/UI_SCREEN.md §4).

    python -m psafe --config config/output_d.yaml

D는 판정하지 않는다. 스스로 정하는 것은 하나뿐이다:
    C의 메시지가 stale_ms(1초) 넘게 없거나, 아직 한 번도 못 받았거나, 규격에 맞지 않으면
    -> 빨간 신호등(허가 없음) + "확인할 수 없습니다" 음성 1번.
D 프로그램 자체가 죽으면 ESP32 워치독(500 ms)이 빨간불로 바꾼다.

    rx    C의 /api/events(SSE)를 읽어 최신 메시지를 저장한다. 끊기면 reconnect_s마다 다시 연결
    main  heartbeat_ms마다 모든 출력(sink)에 현재 메시지(또는 None = 빨간 표시)를 넘긴다

화면(Chromium)은 이 프로그램을 거치지 않고 C에 직접 접속한다. 같은 규칙을 JS가 지킨다.
"""

import http.client
import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from psafe.common.clock import Clock, RealClock
from psafe.common.config import ComponentConfig, ConfigError
from psafe.common.output_api import OUTPUTS, Output
from psafe.common.packets import Decision, State
from psafe.common.registry import Registry
from psafe.ui.contract import FAILSAFE_VOICE, ContractError, Signal, StateMessage, from_json
from psafe.ui.voice import PLAYERS, Player

log = logging.getLogger("psafe.ui.client")

FAILSAFE_SIGNAL = Signal("red", False)


# ---- 출력 (sink) -------------------------------------------------------------

class StateSink(ABC):
    """D의 출력 하나. msg가 None이면 C의 상태를 믿을 수 없다는 뜻 -> 빨간 표시.

    render()는 heartbeat_ms마다 호출된다 (바뀔 때만이 아님). 가볍고 막히면 안 된다.
    reason: ok | connecting | link_lost | bad_message | shutdown
    """

    def open(self) -> None: ...

    @abstractmethod
    def render(self, msg: StateMessage | None, reason: str) -> None: ...

    def close(self) -> None: ...


SINKS: Registry[StateSink] = Registry("output sink")


def signal_decision(signal: Signal, now: float) -> Decision:
    """신호등 상태 -> ESP32 프로토콜 v1 (STATE, SAFE). 전송 형식은 바꾸지 않는다.
        green + 허가 -> CLEAR + Y    green(허가 없음) -> CLEAR + N
        yellow       -> VERIFYING + N
        red          -> FAULT + N
    지금 펌웨어는 Y/N만 켠다 (노랑은 빨강처럼 보인다). 3색은 펌웨어가 STATE로 색을 고르면 된다."""
    state = {"green": State.CLEAR, "yellow": State.VERIFYING}.get(signal.light, State.FAULT)
    safe = signal.permit and state == State.CLEAR
    return Decision(safe, state, f"light_{signal.light}", -1, now, now)


@SINKS.register("light")
class LightSink(StateSink):
    """신호등. ESP32 Output(serial / mock_mcu)을 감싸 하트비트마다 패킷을 보낸다.

        - name: light
          options: { output: serial, options: { port: /dev/ttyUSB0 } }
    """

    def __init__(self, clock: Clock, output: str = "mock_mcu", options: dict[str, Any] | None = None):
        self.clock = clock
        self.inner: Output = OUTPUTS.create(output, {"clock": clock}, **(options or {}))

    def open(self) -> None:
        self.inner.open()

    def render(self, msg: StateMessage | None, reason: str) -> None:
        signal = FAILSAFE_SIGNAL if msg is None else msg.signal
        self.inner.send(signal_decision(signal, self.clock.now()))

    def close(self) -> None:
        self.inner.close()


@SINKS.register("voice")
class VoiceSink(StateSink):
    """음성. C가 올린 voice.seq가 바뀔 때만 재생한다. 연결이 끊기면 link_lost를 1번.

        - name: voice
          options: { player: command, options: { directory: assets/voice } }
    """

    def __init__(self, player: str = "log", options: dict[str, Any] | None = None):
        self.player: Player = PLAYERS.create(player, None, **(options or {}))
        self._last_seq: int | None = None
        self._lost = False

    def render(self, msg: StateMessage | None, reason: str) -> None:
        if msg is None:
            if not self._lost and reason not in ("connecting", "shutdown"):
                self._lost = True
                self.player.play(FAILSAFE_VOICE)
            return
        self._lost = False
        if msg.voice.seq != self._last_seq:
            self._last_seq = msg.voice.seq
            if msg.voice.name:
                self.player.play(msg.voice.name)

    def close(self) -> None:
        self.player.close()


# ---- 상태 수신 --------------------------------------------------------------

class StateReceiver:
    """마지막으로 받은 메시지와 받은 시각(D 시계)을 들고 있다. 판단은 current()의 신선도 검사뿐이다."""

    def __init__(self, clock: Clock, stale_ms: float = 1000.0):
        self.clock = clock
        self.stale_s = stale_ms / 1000.0
        self._lock = threading.Lock()
        self._msg: StateMessage | None = None
        self._at: float | None = None
        self._bad = False
        self._fresh = True            # 새 연결의 첫 메시지 (C가 재시작했으면 seq가 다시 작아진다)
        self.counts = {"ok": 0, "old_seq": 0, "bad_message": 0}

    def new_connection(self) -> None:
        with self._lock:
            self._fresh = True

    def feed(self, data: str) -> None:
        now = self.clock.now()
        try:
            msg = from_json(data)
        except ContractError as e:
            with self._lock:
                self._bad = True
                self.counts["bad_message"] += 1
            log.warning("bad state message: %s", e)
            return
        with self._lock:
            # 같은 연결 안에서 seq가 줄었으면 오래된 메시지다. 새 연결의 첫 메시지는 받아들인다
            # (C가 재시작하면 seq가 0부터 다시 시작한다).
            if not self._fresh and self._msg is not None and msg.seq <= self._msg.seq:
                self.counts["old_seq"] += 1
                return
            self._msg, self._at, self._bad, self._fresh = msg, now, False, False
            self.counts["ok"] += 1

    def current(self) -> tuple[StateMessage | None, str]:
        now = self.clock.now()
        with self._lock:
            if self._bad:
                return None, "bad_message"
            if self._msg is None:
                return None, "connecting"
            if now - self._at > self.stale_s:
                return None, "link_lost"
            return self._msg, "ok"


def read_events(url: str, on_data, stop: threading.Event, timeout_s: float = 2.0) -> None:
    """SSE 연결 1번. 끊기거나 timeout_s 동안 아무것도 안 오면 돌아온다 (호출한 쪽이 다시 연결)."""
    u = urlsplit(url)
    conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(u.hostname, u.port, timeout=timeout_s)
    try:
        conn.request("GET", (u.path.rstrip("/") or "") + "/api/events", headers={"Accept": "text/event-stream"})
        resp = conn.getresponse()
        if resp.status != 200:
            raise ConnectionError(f"HTTP {resp.status}")
        data: list[str] = []
        while not stop.is_set():
            raw = resp.fp.readline()
            if not raw:
                return                                  # 서버가 연결을 닫음
            line = raw.decode("utf-8").rstrip("\r\n")
            if line == "":
                if data:
                    on_data("\n".join(data))
                    data = []
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
            # event:, id:, 주석(:)은 쓰지 않는다
    finally:
        conn.close()


# ---- 설정 ------------------------------------------------------------------

@dataclass
class OutputClientConfig:
    server: str                       # C 상태 서버 주소 http://<C>:8080
    stale_ms: float = 1000.0          # 이보다 오래 메시지가 없으면 빨간 표시
    heartbeat_ms: float = 200.0       # sink에 넘기는 주기 (ESP32 워치독 500 ms보다 짧게)
    reconnect_s: float = 1.0
    metrics_interval_s: float = 5.0


@dataclass
class OutputAppConfig:
    client: OutputClientConfig
    sinks: list[ComponentConfig] = field(default_factory=list)


def is_output_config(data: dict[str, Any]) -> bool:
    """설정 파일에 `output_client:` 절이 있으면 출력 Pi(D)로 실행한다."""
    return isinstance(data, dict) and "output_client" in data


def parse_output_config(data: dict[str, Any]) -> OutputAppConfig:
    unknown = set(data) - {"output_client", "sinks"}
    if unknown:
        raise ConfigError(f"config: unknown keys {sorted(unknown)}")
    c = data.get("output_client") or {}
    known = set(OutputClientConfig.__dataclass_fields__)
    if set(c) - known:
        raise ConfigError(f"config.output_client: unknown keys {sorted(set(c) - known)}")
    if "server" not in c:
        raise ConfigError("config.output_client.server is required")
    client = OutputClientConfig(**c)
    u = urlsplit(client.server)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ConfigError(f"config.output_client.server: expected http://host:port, got {client.server!r}")
    for k in ("stale_ms", "heartbeat_ms", "reconnect_s"):
        if getattr(client, k) <= 0:
            raise ConfigError(f"config.output_client.{k}: must be > 0")
    if client.heartbeat_ms >= 500:
        raise ConfigError("config.output_client.heartbeat_ms: must be < 500 (ESP32 watchdog)")
    sinks = []
    for i, s in enumerate(data.get("sinks") or []):
        if not isinstance(s, dict) or "name" not in s or set(s) - {"name", "options"}:
            raise ConfigError(f"config.sinks[{i}]: expected {{name, options}}")
        sinks.append(ComponentConfig(s["name"], dict(s.get("options") or {})))
    return OutputAppConfig(client, sinks)


# ---- 실행 ------------------------------------------------------------------

class OutputApp:
    def __init__(self, cfg: OutputAppConfig, clock: Clock | None = None, sinks: list[StateSink] | None = None):
        self.cfg = cfg
        self.clock = clock or RealClock()
        self.receiver = StateReceiver(self.clock, cfg.client.stale_ms)
        inject = {"clock": self.clock}
        self.sinks = sinks if sinks is not None else [SINKS.create(s.name, inject, **s.options) for s in cfg.sinks]
        self._stop = threading.Event()
        self.ticks = 0

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> str:
        msg, reason = self.receiver.current()
        self.render_all(msg, reason)
        self.ticks += 1
        return reason

    def render_all(self, msg: StateMessage | None, reason: str) -> None:
        for s in self.sinks:
            try:
                s.render(msg, reason)
            except Exception:
                log.exception("sink %s failed", type(s).__name__)

    def run(self, max_seconds: float | None = None) -> int:
        c = self.cfg.client
        opened: list[StateSink] = []
        try:
            for s in self.sinks:
                s.open()
                opened.append(s)
            rx = threading.Thread(target=self._rx_loop, name="state-rx", daemon=True)
            rx.start()
            log.info("output client: %s, sinks %s", c.server, [type(s).__name__ for s in self.sinks])
            start = next_t = next_metrics = self.clock.now()
            last_reason = None
            while not self._stop.is_set():
                reason = self.tick()
                if reason != last_reason:
                    (log.info if reason == "ok" else log.warning)("state: %s", reason)
                    last_reason = reason
                now = self.clock.now()
                if now >= next_metrics:
                    log.info("rx %s", self.receiver.counts)
                    next_metrics = now + c.metrics_interval_s
                if max_seconds is not None and now - start >= max_seconds:
                    break
                next_t += c.heartbeat_ms / 1000.0
                self._stop.wait(max(0.0, next_t - self.clock.now()))
        except KeyboardInterrupt:
            log.info("interrupted")
        finally:
            self._stop.set()
            self.render_all(None, "shutdown")
            for s in reversed(opened):
                try:
                    s.close()
                except Exception:
                    log.exception("sink %s close failed", type(s).__name__)
        return 0

    def _rx_loop(self) -> None:
        c = self.cfg.client
        while not self._stop.is_set():
            self.receiver.new_connection()
            try:
                read_events(c.server, self.receiver.feed, self._stop)
            except Exception as e:
                log.debug("state stream: %s", e)
            self._stop.wait(c.reconnect_s)
