"""판정 Pi(C) -> 출력 Pi(D) 상태 메시지 규격 v1 (docs/UI_SCREEN.md §3).

C의 상태 서버가 만들고, D의 출력 프로그램이 해석한다. 화면 JS도 같은 JSON을 읽는다.
**형식은 이 파일에만 둔다.** 만드는 쪽과 읽는 쪽이 같은 코드를 쓰게 하기 위해서다.

    display   화면(JS)이 그린다       level · color · title · message · action
    voice     음성 출력이 재생한다     name · seq   (seq가 바뀔 때만 재생)
    signal    신호등 출력이 내보낸다   light · permit

출력은 자기 블록만 보고, 판정(verdict)이나 카메라별 상태(nodes)로 다시 계산하지 않는다.

안전 규칙은 해석할 때도 검사한다(from_json). 어긋난 메시지는 ContractError가 되고, D는 그것을
"받지 못한 것"과 같이 빨간 표시로 처리한다:
    - 초록(IDLE)은 판정이 EMPTY일 때만, 운행 허가(permit)는 EMPTY이고 초록일 때만
    - 화면 level과 color, 신호등 light는 서로 맞아야 한다 (IDLE=green, WARNING=yellow, BLOCKED=red)
"""

import json
from dataclasses import dataclass
from typing import Any

VERSION = 1

LEVEL_COLORS: dict[str, str] = {"IDLE": "green", "WARNING": "yellow", "BLOCKED": "red"}
ACTIONS: tuple[str | None, ...] = (None, "check_inside", "open_window", "call_admin")
VERDICT_KINDS = ("EMPTY", "OCCUPIED", "CHECKING", "UNKNOWN")
ROLES = ("interior", "exterior")

# D가 스스로 만드는 표시 (C의 메시지가 없을 때). 화면 JS도 같은 문구를 쓴다 (UI_SCREEN §4).
FAILSAFE_VOICE = "link_lost"


class ContractError(ValueError):
    """규격에 맞지 않는 메시지."""


@dataclass(frozen=True, slots=True)
class Display:
    level: str
    color: str
    title: str
    message: str
    action: str | None = None


@dataclass(frozen=True, slots=True)
class Voice:
    name: str | None      # 재생할 녹음 이름. None = 말하지 않음
    seq: int              # 새로 말해야 할 때마다 C가 1씩 올린다


@dataclass(frozen=True, slots=True)
class Signal:
    light: str            # green | yellow | red
    permit: bool          # 운행 허가 (모의 운행 제한 신호). EMPTY + green일 때만 True


@dataclass(frozen=True, slots=True)
class NodeInfo:
    name: str
    role: str                         # interior | exterior
    alive: bool
    state: str | None                 # 카메라 Pi의 FSM 상태. 무응답이면 None
    count: int
    reason: str
    evidence_age_ms: float | None


@dataclass(frozen=True, slots=True)
class Estimate:
    interior: int | None              # 내부 인원 추정 (표시 전용). 모르면 None
    exterior: int | None              # 외부 인원 추정 (표시 전용). 모르면 None


@dataclass(frozen=True, slots=True)
class StateMessage:
    seq: int
    sent_at: str                      # ISO 8601, C의 벽시계. 표시용 ("마지막 갱신")
    state: str                        # C 출력 FSM의 상태 이름 (디버그용)
    display: Display
    voice: Voice
    signal: Signal
    verdict_kind: str
    verdict_reason: str
    nodes: tuple[NodeInfo, ...] = ()
    estimate: Estimate = Estimate(None, None)

    def __post_init__(self) -> None:
        check(self)


def check(m: StateMessage) -> None:
    """안전 규칙과 값의 범위를 검사한다. 만들 때(C)와 읽을 때(D) 모두 쓴다."""
    d, s = m.display, m.signal
    if d.level not in LEVEL_COLORS:
        raise ContractError(f"display.level: {d.level!r}")
    if d.color != LEVEL_COLORS[d.level]:
        raise ContractError(f"display.color {d.color!r} does not match level {d.level}")
    if s.light != d.color:
        raise ContractError(f"signal.light {s.light!r} does not match display.color {d.color!r}")
    if d.action not in ACTIONS:
        raise ContractError(f"display.action: {d.action!r}")
    if m.verdict_kind not in VERDICT_KINDS:
        raise ContractError(f"verdict.kind: {m.verdict_kind!r}")
    if s.light == "green" and m.verdict_kind != "EMPTY":
        raise ContractError("green is only allowed with verdict EMPTY")
    if s.permit and not (s.light == "green" and m.verdict_kind == "EMPTY"):
        raise ContractError("signal.permit is only allowed with light=green and verdict EMPTY")
    for n in m.nodes:
        if n.role not in ROLES:
            raise ContractError(f"nodes[{n.name}].role: {n.role!r}")


# ---- JSON ------------------------------------------------------------------

def to_dict(m: StateMessage) -> dict[str, Any]:
    d, v, s, e = m.display, m.voice, m.signal, m.estimate
    return {
        "v": VERSION,
        "seq": m.seq,
        "sent_at": m.sent_at,
        "state": m.state,
        "display": {"level": d.level, "color": d.color, "title": d.title, "message": d.message, "action": d.action},
        "voice": {"name": v.name, "seq": v.seq},
        "signal": {"light": s.light, "permit": s.permit},
        "verdict": {"kind": m.verdict_kind, "reason": m.verdict_reason},
        "nodes": [
            {"name": n.name, "role": n.role, "alive": n.alive, "state": n.state, "count": n.count,
             "reason": n.reason, "evidence_age_ms": n.evidence_age_ms}
            for n in m.nodes
        ],
        "estimate": {"interior": e.interior, "exterior": e.exterior},
    }


def to_json(m: StateMessage) -> str:
    """한 줄 JSON (SSE data 한 줄)."""
    return json.dumps(to_dict(m), ensure_ascii=False, separators=(",", ":"))


def from_json(text: str | bytes) -> StateMessage:
    """JSON -> StateMessage. 형식·버전·안전 규칙이 어긋나면 ContractError. 모르는 필드는 무시한다."""
    try:
        o = json.loads(text)
    except (ValueError, UnicodeDecodeError) as e:
        raise ContractError(f"not JSON: {e}") from None
    if not isinstance(o, dict):
        raise ContractError("not an object")
    if o.get("v") != VERSION:
        raise ContractError(f"version {o.get('v')!r} != {VERSION}")
    try:
        d, v, s, vd, e = (_obj(o, k) for k in ("display", "voice", "signal", "verdict", "estimate"))
        return StateMessage(
            seq=_int(o["seq"]),
            sent_at=_str(o["sent_at"]),
            state=_str(o["state"]),
            display=Display(_str(d["level"]), _str(d["color"]), _str(d["title"]), _str(d["message"]),
                            _opt(d.get("action"), str)),
            voice=Voice(_opt(v.get("name"), str), _int(v["seq"])),
            signal=Signal(_str(s["light"]), _bool(s["permit"])),
            verdict_kind=_str(vd["kind"]),
            verdict_reason=_str(vd["reason"]),
            nodes=tuple(
                NodeInfo(_str(n["name"]), _str(n["role"]), _bool(n["alive"]), _opt(n.get("state"), str),
                         _int(n["count"]), _str(n["reason"]), _opt_num(n.get("evidence_age_ms")))
                for n in _list(o.get("nodes", []))
            ),
            estimate=Estimate(_opt(e.get("interior"), int), _opt(e.get("exterior"), int)),
        )
    except (KeyError, TypeError) as e:
        raise ContractError(f"bad field: {e}") from None


def _obj(o: dict, key: str) -> dict:
    x = o[key]
    if not isinstance(x, dict):
        raise TypeError(f"{key} must be an object")
    return x


def _list(x: Any) -> list:
    if not isinstance(x, list):
        raise TypeError("nodes must be a list")
    return [n if isinstance(n, dict) else _raise("node must be an object") for n in x]


def _str(x: Any) -> str:
    return x if isinstance(x, str) else _raise(f"expected string, got {x!r}")


def _int(x: Any) -> int:
    return x if isinstance(x, int) and not isinstance(x, bool) else _raise(f"expected int, got {x!r}")


def _bool(x: Any) -> bool:
    return x if isinstance(x, bool) else _raise(f"expected bool, got {x!r}")


def _opt(x: Any, t: type) -> Any:
    if x is None:
        return None
    return _int(x) if t is int else _str(x)


def _opt_num(x: Any) -> float | None:
    if x is None:
        return None
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        _raise(f"expected number, got {x!r}")
    return float(x)


def _raise(msg: str):
    raise TypeError(msg)
