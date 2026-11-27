"""출력 상태 서버 (Pi C). 판정 -> 출력 FSM -> 상태 메시지 -> HTTP/SSE (docs/UI_SCREEN.md §1~2).

    GET  /             web/screen/ 파일 (화면 HTML·JS·CSS. 화면 담당이 작성)
    GET  /api/state    현재 상태 JSON 한 번
    GET  /api/events   상태 스트림 (SSE, event: state). 하트비트마다(200 ms) 보낸다. 바뀌지 않아도 보낸다
    POST /api/sos      관리자 호출 (예정, DEC-008). 지금은 501

D의 화면(Chromium)과 D의 출력 프로그램(psafe.ui.client)이 같은 /api/events를 받는다.
Python 표준 라이브러리만 쓴다 (Pi C에 추가 설치 없음).

    outputs:
      - name: state_server
        options:
          bind: 0.0.0.0:8080          # PC 시험은 127.0.0.1:8080
          exterior: [RGB]             # 외부 인원 카메라 이름 (나머지는 내부)
          occupied_block_s: null      # 출력 FSM 설정 (output_fsm.OutputFsmConfig)
"""

import json
import logging
import mimetypes
import queue
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from psafe.common.clock import Clock
from psafe.common.config import parse_address
from psafe.common.output_api import VERDICT_OUTPUTS, VerdictOutput
from psafe.common.packets import State
from psafe.common.verdict import Verdict
from psafe.ui.contract import Estimate, NodeInfo, Signal, StateMessage, Voice, to_json
from psafe.ui.output_fsm import Frame, OutputFsm, OutputFsmConfig

log = logging.getLogger("psafe.ui.state_server")

# 근거가 있는 상태. 이 상태의 노드만 인원 추정에 쓴다 (FAULT·INIT의 count는 의미 없음)
_COUNTED = (State.OCCUPIED, State.VERIFYING, State.CLEAR)
_CLOSE = object()          # 구독자 큐에 넣는 종료 신호


def wall_time() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def compose(frame: Frame, verdict: Verdict | None, seq: int, sent_at: str,
            exterior: frozenset[str] = frozenset()) -> StateMessage:
    """FSM 결과 + 판정 -> 상태 메시지. verdict가 None이면 첫 판정 전 (STARTING)."""
    if verdict is None:
        kind, reason, nodes = "UNKNOWN", "starting", ()
    else:
        kind, reason = verdict.kind.value, verdict.reason
        nodes = tuple(
            NodeInfo(n.node, "exterior" if n.node in exterior else "interior", n.alive,
                     None if n.state is None else n.state.value, n.count, n.reason, n.evidence_age_ms)
            for n in verdict.nodes
        )
    return StateMessage(
        seq=seq, sent_at=sent_at, state=frame.state.value, display=frame.display,
        voice=Voice(frame.voice_name, frame.voice_seq),
        signal=Signal(frame.display.color, frame.permit),
        verdict_kind=kind, verdict_reason=reason, nodes=nodes,
        estimate=_estimate(verdict, exterior),
    )


def _estimate(v: Verdict | None, exterior: frozenset[str]) -> Estimate:
    """역할별 인원 추정 = 근거가 있는 카메라 count의 최댓값 (더하지 않음: 같은 사람을 두 번 셀 수 있다).
    그런 카메라가 없으면 None ("모름"은 0이 아니다)."""
    if v is None:
        return Estimate(None, None)
    def best(ext: bool) -> int | None:
        counts = [n.count for n in v.nodes if (n.node in exterior) == ext and n.alive and n.state in _COUNTED]
        return max(counts) if counts else None
    return Estimate(best(False), best(True))


@VERDICT_OUTPUTS.register("state_server")
class StateServer(VerdictOutput):
    def __init__(self, clock: Clock, bind: str = "127.0.0.1:8080", static_dir: str = "web/screen",
                 exterior: list[str] | None = None, occupied_block_s: float | None = None,
                 voice_repeat_s: dict[str, float] | None = None, voice_gap_s: float = 3.0,
                 max_clients: int = 8):
        self.clock = clock
        self.address = parse_address(bind)
        self.static_dir = Path(static_dir).resolve()
        self.exterior = frozenset(["RGB"] if exterior is None else exterior)
        self.fsm = OutputFsm(OutputFsmConfig(occupied_block_s, dict(voice_repeat_s or {}), voice_gap_s))
        self.max_clients = max_clients
        self._lock = threading.Lock()
        self._seq = 0
        self._latest = to_json(compose(self.fsm.frame(), None, 0, wall_time(), self.exterior))
        self._clients: set[queue.Queue] = set()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.shown = 0

    @property
    def port(self) -> int:
        """실제로 연 포트 (bind 포트가 0이면 OS가 고른 값)."""
        return self._httpd.server_address[1] if self._httpd else self.address[1]

    # ---- VerdictOutput -----------------------------------------------------

    def open(self) -> None:
        self._httpd = ThreadingHTTPServer(self.address, _handler(self))
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.2},
                                        name="state-http", daemon=True)
        self._thread.start()
        log.info("state server on http://%s:%d (static %s)", self.address[0], self.port, self.static_dir)

    def show(self, verdict: Verdict) -> None:
        frame = self.fsm.step(verdict, self.clock.now())
        with self._lock:
            self._seq += 1
            text = to_json(compose(frame, verdict, self._seq, wall_time(), self.exterior))
            self._latest = text
            clients = list(self._clients)
        for q in clients:
            _offer(q, text)
        self.shown += 1

    def close(self) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            _offer(q, _CLOSE)                 # 마지막(SHUTDOWN) 메시지를 보낸 뒤 연결을 닫게 한다
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        log.info("state server closed (%d messages)", self.shown)

    # ---- 구독자 -------------------------------------------------------------

    def latest(self) -> str:
        with self._lock:
            return self._latest

    def subscribe(self) -> queue.Queue | None:
        with self._lock:
            if len(self._clients) >= self.max_clients:
                return None
            q: queue.Queue = queue.Queue(maxsize=16)
            q.put_nowait(self._latest)        # 연결하자마자 현재 상태부터
            self._clients.add(q)
            return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)


def _offer(q: queue.Queue, item) -> None:
    """느린 구독자 때문에 막히지 않게, 가득 차면 가장 오래된 것을 버린다 (옛 상태보다 지금 상태가 중요)."""
    while True:
        try:
            q.put_nowait(item)
            return
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass


def _handler(server: StateServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):     # 요청마다 로그를 남기지 않는다 (200 ms마다 들어옴)
            log.debug("%s - " + fmt, self.address_string(), *args)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/state":
                self._send(HTTPStatus.OK, server.latest().encode(), "application/json; charset=utf-8")
            elif path == "/api/events":
                self._events()
            else:
                self._static(path)

        def do_POST(self):
            if self.path.split("?", 1)[0] == "/api/sos":
                body = json.dumps({"accepted": False, "reason": "not_implemented"}).encode()
                self._send(HTTPStatus.NOT_IMPLEMENTED, body, "application/json; charset=utf-8")
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def _events(self):
            q = server.subscribe()
            if q is None:
                self._send(HTTPStatus.SERVICE_UNAVAILABLE, b"too many clients", "text/plain")
                return
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                while True:
                    try:
                        item = q.get(timeout=1.0)
                    except queue.Empty:
                        continue                  # 판정이 멈추면 아무것도 보내지 않는다 -> D가 1초 뒤 빨간 표시
                    if item is _CLOSE:
                        return
                    self.wfile.write(f"event: state\ndata: {item}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass                              # 구독자가 떠남
            finally:
                server.unsubscribe(q)

        def _static(self, path: str):
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (server.static_dir / rel).resolve()
            if server.static_dir not in target.parents or not target.is_file():
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            self._send(HTTPStatus.OK, target.read_bytes(), ctype)

        def _send(self, status: HTTPStatus, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

    return Handler

