"""판정 콘솔 (Pi C). 최종 판정을 로그로 남긴다. 화면이 없을 때·개발할 때 쓴다.

    outputs:
      - name: console
        options: { every: false }     # true면 하트비트마다, false면 (판정, 사유, 노드별 인원)이 바뀔 때만
"""

import logging

from psafe.common.output_api import VERDICT_OUTPUTS, VerdictOutput
from psafe.common.packets import State
from psafe.common.verdict import Verdict, VerdictKind

log = logging.getLogger("psafe.ui.console")

_LABELS = {
    VerdictKind.EMPTY: "비었음 확인",
    VerdictKind.OCCUPIED: "사람 있음",
    VerdictKind.CHECKING: "확인 중",
    VerdictKind.UNKNOWN: "판단 불가",
}


@VERDICT_OUTPUTS.register("console")
class ConsoleVerdictOutput(VerdictOutput):
    def __init__(self, every: bool = False):
        self.every = every
        self._last: tuple | None = None

    def show(self, verdict: Verdict) -> None:
        key = (verdict.kind, verdict.reason, tuple((n.node, n.alive, n.count) for n in verdict.nodes))
        if not self.every and key == self._last:
            return
        self._last = key
        log.info("%s", format_verdict(verdict))


def format_verdict(v: Verdict) -> str:
    """한 줄 요약. 노드별 인원은 더하지 않는다 (겹치는 구역에서 두 번 세어질 수 있다)."""
    parts = []
    for n in v.nodes:
        if not n.alive or n.state is None:
            parts.append(f"{n.node}: 무응답")
        else:
            age = "-" if n.evidence_age_ms is None else f"{n.evidence_age_ms:.0f}ms"
            why = "" if n.state in (State.CLEAR, State.VERIFYING, State.OCCUPIED) else f"({n.reason}) "
            parts.append(f"{n.node}: {n.count}명 {n.state.value} {why}{age}")
    cid = "-" if v.confirm_id is None else str(v.confirm_id)
    return f"[{v.kind.value:<8}] {_LABELS[v.kind]} ({v.reason}) | {' | '.join(parts)} | confirm={cid} t={v.decided_at:.2f}"
