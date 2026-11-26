"""판정 Pi(C) 코드 (docs/ARCHITECTURE.md §4, §7).

    runner.py      Hub: 보고 수신 → 판정 주기 실행 → 출력·Control 송신. HubRunner: 스레드 실행·감독
    aggregator.py  종합 규칙: 카메라 Pi들의 보고 → 최종 판정 (순수 로직)

규칙: node를 import하지 않는다. 출력은 common.output_api의 VerdictOutput 틀과 등록부로만 만들고,
구현체(화면·음성·신호등·관리자 알림)는 ui에 둔다.
"""

from psafe.hub.runner import Hub, HubResult, HubRunner, build_hub

__all__ = ["Hub", "HubResult", "HubRunner", "build_hub"]
