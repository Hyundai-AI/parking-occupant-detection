"""출력 계층 (docs/ARCHITECTURE.md §5, docs/UI_SCREEN.md).

Pi C (판정 Pi): 판정을 받아 "무엇을 보여 줄지" 정한다. VerdictOutput 구현체
    output_fsm.py    출력 FSM: 판정 -> 화면·음성·신호등 내용
    state_server.py  출력 상태 서버 (HTTP/SSE)               "state_server"
    console.py       판정 로그                                "console"

Pi D (출력 Pi): 받은 대로 출력만 한다
    client.py        상태 수신 + 1초 끊김 감시 + 출력(sink)    light · voice
    voice.py         음성 재생기                               log · command
    esp32/           신호등 ESP32 프로토콜 v1, 시리얼, 가짜 ESP32

C·D 공통
    contract.py      상태 메시지 규격 v1 (만들기·해석·안전 규칙 검사)

hub는 이 패키지를 import하지 않는다. 설정 파일의 이름으로 구현체를 고른다 (ARCHITECTURE §7.2).
"""

from psafe.ui import client, console, esp32, state_server, voice  # noqa: F401  (구현체 등록용 import)
