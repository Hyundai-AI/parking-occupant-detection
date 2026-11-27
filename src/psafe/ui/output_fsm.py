"""출력 FSM (Pi C). 최종 판정(Verdict) -> 화면·음성·신호등에 낼 내용 (docs/UI_SCREEN.md §3.2).

판정은 hub가 하고, 이 FSM은 "어떻게 보여 줄지"만 정한다. D는 결과를 그대로 출력한다.

    상태               들어가는 조건                    화면      신호등  운행 허가  음성
    STARTING           첫 판정 전                       BLOCKED   red     X          -
    IDLE               판정 EMPTY                       IDLE      green   O (유일)   idle
    CHECKING           판정 CHECKING                    WARNING   yellow  X          checking
    OCCUPIED           판정 OCCUPIED                    WARNING   yellow  X          occupied
    OCCUPIED_BLOCKED   OCCUPIED가 occupied_block_s 이상  BLOCKED   red     X          blocked
    UNKNOWN            판정 UNKNOWN                     BLOCKED   red     X          unknown
    SHUTDOWN           C 종료 (판정 사유 shutdown)       BLOCKED   red     X          -

규칙
    1. 운행 허가는 **판정이 EMPTY일 때만** 나간다. 표시 설정(looks)을 바꿔도 이 조건은 바뀌지 않는다.
    2. 더 엄격한 쪽으로는 즉시 바뀐다. 풀어 주는 쪽은 판정을 그대로 따른다 (판정이 이미 느리게 푼다).
    3. 음성은 C가 "언제 말할지"까지 정한다. 새로 말할 때마다 voice_seq를 올리고, D는 바뀔 때만 재생한다.
       - 더 엄격해질 때: 즉시
       - 풀어질 때·같은 수준으로 바뀔 때: 마지막 음성에서 voice_gap_s가 지난 뒤 (빠르게 오갈 때 겹치지 않게)
       - 같은 상태가 계속될 때: voice_repeat_s[상태]마다 반복 (설정한 상태만)
"""

from dataclasses import dataclass, field
from enum import Enum

from psafe.common.verdict import Verdict, VerdictKind
from psafe.ui.contract import LEVEL_COLORS, Display


class OutState(str, Enum):
    STARTING = "STARTING"
    IDLE = "IDLE"
    CHECKING = "CHECKING"
    OCCUPIED = "OCCUPIED"
    OCCUPIED_BLOCKED = "OCCUPIED_BLOCKED"
    UNKNOWN = "UNKNOWN"
    SHUTDOWN = "SHUTDOWN"


# 엄격한 정도. 클수록 더 막는 쪽.
SEVERITY: dict[OutState, int] = {
    OutState.IDLE: 0,
    OutState.CHECKING: 1,
    OutState.OCCUPIED: 2,
    OutState.OCCUPIED_BLOCKED: 3,
    OutState.UNKNOWN: 3,
    OutState.STARTING: 3,
    OutState.SHUTDOWN: 3,
}


@dataclass(frozen=True, slots=True)
class Look:
    """한 상태의 표시 내용. 문구는 DEC-006에서 확정되면 여기(또는 설정)만 고친다."""
    level: str
    title: str
    message: str
    action: str | None = None
    voice: str | None = None


# 문구는 초안이다 (DEC-006). "출발하세요"처럼 허가로 읽히는 표현은 쓰지 않는다 (DEC-007).
DEFAULT_LOOKS: dict[OutState, Look] = {
    OutState.STARTING: Look("BLOCKED", "확인할 수 없습니다", "시스템을 시작하는 중입니다."),
    OutState.IDLE: Look("IDLE", "대기 중입니다", "차량 내부 확인이 끝났습니다.", voice="idle"),
    OutState.CHECKING: Look("WARNING", "확인 중입니다", "잠시만 기다려 주세요.", voice="checking"),
    OutState.OCCUPIED: Look("WARNING", "차량 안에 사람이 있습니다", "차량 내부를 확인해 주세요.",
                            action="check_inside", voice="occupied"),
    OutState.OCCUPIED_BLOCKED: Look("BLOCKED", "운행할 수 없습니다", "차량 안에 사람이 있습니다. 관리자를 기다려 주세요.",
                                    action="call_admin", voice="blocked"),
    # action은 비워 둔다: 유리창 개방 / 관리자 호출은 미정 (DEC-004, DEC-007). 실제로 호출하지 않았는데
    # "관리자를 호출했습니다"를 띄우면 안 된다.
    OutState.UNKNOWN: Look("BLOCKED", "확인할 수 없습니다", "차량 내부를 확인할 수 없습니다. 잠시 기다려 주세요.",
                           voice="unknown"),
    OutState.SHUTDOWN: Look("BLOCKED", "확인할 수 없습니다", "시스템이 멈췄습니다. 관리자에게 문의해 주세요."),
}


@dataclass
class OutputFsmConfig:
    # OCCUPIED가 이 시간 이상 계속되면 OCCUPIED_BLOCKED. None = 사용 안 함 (DEC-007 미정 -> WARNING만)
    occupied_block_s: float | None = None
    # 같은 상태가 계속될 때 음성을 반복할 간격 (초). 예: {"OCCUPIED": 10}. 없는 상태는 반복하지 않는다
    voice_repeat_s: dict[str, float] = field(default_factory=dict)
    # 풀어지거나 같은 수준으로 바뀔 때, 마지막 음성 뒤 최소 간격 (초)
    voice_gap_s: float = 3.0

    def __post_init__(self) -> None:
        if self.occupied_block_s is not None and self.occupied_block_s < 0:
            raise ValueError("occupied_block_s must be >= 0 or None")
        if self.voice_gap_s < 0:
            raise ValueError("voice_gap_s must be >= 0")
        for k, v in self.voice_repeat_s.items():
            if k not in OutState.__members__:
                raise ValueError(f"voice_repeat_s: unknown state {k!r}")
            if v <= 0:
                raise ValueError(f"voice_repeat_s[{k}] must be > 0")


@dataclass(frozen=True, slots=True)
class Frame:
    """FSM 한 단계의 결과. contract.StateMessage로 바꿔 D에 보낸다."""
    state: OutState
    display: Display
    voice_name: str | None
    voice_seq: int
    permit: bool


class OutputFsm:
    def __init__(self, cfg: OutputFsmConfig | None = None, looks: dict[OutState, Look] | None = None):
        self.cfg = cfg or OutputFsmConfig()
        self.looks = {**DEFAULT_LOOKS, **(looks or {})}
        self.state = OutState.STARTING
        self.since: float | None = None            # 현재 상태에 들어간 시각
        self._occupied_since: float | None = None
        self._voice_seq = 0
        self._voice_name: str | None = None
        self._voice_at: float | None = None        # 마지막으로 말한 시각
        self._pending = False                      # 간격 때문에 미룬 음성이 있음

    def frame(self, permit: bool = False) -> Frame:
        look = self.looks[self.state]
        display = Display(look.level, LEVEL_COLORS[look.level], look.title, look.message, look.action)
        return Frame(self.state, display, self._voice_name, self._voice_seq, permit)

    def step(self, verdict: Verdict, now: float) -> Frame:
        target = self._target(verdict, now)
        if target != self.state:
            old = self.state
            self.state, self.since = target, now
            self._on_enter(old, now)
        else:
            self._on_stay(now)
        # 규칙 1: 허가는 판정 EMPTY에서만. 상태 이름이나 표시 설정과 무관하게 여기서 고정한다.
        permit = verdict.kind == VerdictKind.EMPTY and self.state == OutState.IDLE
        return self.frame(permit)

    # ---- 상태 결정 ----------------------------------------------------------

    def _target(self, v: Verdict, now: float) -> OutState:
        if v.kind != VerdictKind.OCCUPIED:
            self._occupied_since = None
        if v.kind == VerdictKind.EMPTY:
            return OutState.IDLE
        if v.kind == VerdictKind.CHECKING:
            return OutState.CHECKING
        if v.kind == VerdictKind.UNKNOWN:
            return OutState.SHUTDOWN if v.reason == "shutdown" else OutState.UNKNOWN
        if self._occupied_since is None:
            self._occupied_since = now
        block = self.cfg.occupied_block_s
        if block is not None and now - self._occupied_since >= block:
            return OutState.OCCUPIED_BLOCKED
        return OutState.OCCUPIED

    # ---- 음성 ---------------------------------------------------------------

    def _on_enter(self, old: OutState, now: float) -> None:
        self._pending = False
        if self.looks[self.state].voice is None:
            return
        if SEVERITY[self.state] > SEVERITY[old] or self._gap_ok(now):
            self._speak(now)
        else:
            self._pending = True

    def _on_stay(self, now: float) -> None:
        if self._pending:
            if self._gap_ok(now):
                self._speak(now)
            return
        repeat = self.cfg.voice_repeat_s.get(self.state.value)
        if repeat and self.looks[self.state].voice and self._voice_at is not None and now - self._voice_at >= repeat:
            self._speak(now)

    def _gap_ok(self, now: float) -> bool:
        return self._voice_at is None or now - self._voice_at >= self.cfg.voice_gap_s

    def _speak(self, now: float) -> None:
        self._pending = False
        self._voice_seq += 1
        self._voice_name = self.looks[self.state].voice
        self._voice_at = now

