"""화면 개발용 예시 JSON (web/screen/samples/). 화면 담당이 판정 Pi 없이 화면만 만들 때 쓴다.

실제 코드(출력 FSM + compose)로 만들기 때문에 규격과 어긋나지 않는다. 규격이 바뀌면 다시 만든다:
    python -m psafe.ui.samples            (web/screen/samples/ 에 쓴다)
tests/ui/test_samples.py가 파일이 최신인지 검사한다.
"""

import sys
from pathlib import Path

from psafe.common.packets import State
from psafe.common.verdict import NodeView, Verdict, VerdictKind
from psafe.ui.contract import StateMessage, to_dict
from psafe.ui.output_fsm import OutputFsm
from psafe.ui.state_server import compose

SENT_AT = "2026-12-03T14:03:21.120+09:00"
EXTERIOR = frozenset({"RGB"})
DEFAULT_DIR = Path("web/screen/samples")


def _node(name: str, state: State | None, count: int = 0, reason: str = "clear", age: float | None = 200.0) -> NodeView:
    if state is None:
        return NodeView(name, False, None, 0, "timeout", None, None)
    return NodeView(name, True, state, count, reason, age, None)


def _msg(kind: VerdictKind, reason: str, nodes: tuple[NodeView, ...], seq: int) -> StateMessage:
    fsm = OutputFsm()
    v = Verdict(kind, reason, nodes, None, 0.0)
    return compose(fsm.step(v, 0.0), v, seq, SENT_AT, EXTERIOR)


def sample_messages() -> dict[str, StateMessage]:
    clear = (_node("A", State.CLEAR), _node("B", State.CLEAR), _node("RGB", State.CLEAR, 1, age=340.0))
    starting = compose(OutputFsm().frame(), None, 0, SENT_AT, EXTERIOR)
    return {
        "starting": starting,
        "idle": _msg(VerdictKind.EMPTY, "confirmed", clear, 101),
        "checking": _msg(VerdictKind.CHECKING, "A_verifying",
                         (_node("A", State.VERIFYING), *clear[1:]), 102),
        "occupied": _msg(VerdictKind.OCCUPIED, "person_B",
                         (clear[0], _node("B", State.OCCUPIED, 1, "person_in_roi"), clear[2]), 103),
        "unknown": _msg(VerdictKind.UNKNOWN, "A_timeout", (_node("A", None), *clear[1:]), 104),
        "rgb_lost": _msg(VerdictKind.UNKNOWN, "RGB_timeout", (*clear[:2], _node("RGB", None)), 105),
    }


def render(m: StateMessage) -> str:
    import json
    return json.dumps(to_dict(m), ensure_ascii=False, indent=2) + "\n"


def write(directory: Path = DEFAULT_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for name, m in sample_messages().items():
        p = directory / f"{name}.json"
        p.write_text(render(m), encoding="utf-8", newline="\n")
        out.append(p)
    return out


if __name__ == "__main__":
    for p in write(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR):
        print(p)
