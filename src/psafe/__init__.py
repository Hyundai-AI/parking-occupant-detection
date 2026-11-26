"""기계식 주차장 차량 내 잔류 인원 감지 시스템.

    common/  모든 Pi 공통: 메시지 형식, 보드 간 통신, 시계, 설정, 판정 종류
    node/    Pi A·B·RGB (카메라 Pi)
    hub/     Pi C 판정
    ui/      Pi C 출력

구조와 규칙: docs/ARCHITECTURE.md §7
"""

__version__ = "0.1.0"
