"""모든 Pi가 함께 쓰는 코드 (docs/ARCHITECTURE.md §7).

    packets.py    프레임·검출·판단 자료형, 카메라 Pi 상태(State)
    clock.py      시계 (실제 시계, 시험용 가짜 시계)
    config.py     설정 파일 읽기와 검사
    registry.py   이름으로 부품(Source, Detector, Output)을 고르는 등록부
    verdict.py    Pi C의 최종 판정 종류 (EMPTY / OCCUPIED / CHECKING / UNKNOWN)
    messages.py   보드 간 메시지 Report / Control, encode / decode (순수)
    transport.py  전송 인터페이스, UdpTransport, LoopbackNetwork (I/O)
    peer.py       PeerTracker: 노드별 중복 제거·재시작·무응답 판정 (순수)
    output_api.py 출력 틀: Output(카메라 Pi 판단용), VerdictOutput(Pi C 판정용)과 등록부

규칙: node · hub · ui는 서로 import하지 않고, 함께 필요한 것만 여기에 둔다.
메시지 형식은 보내는 쪽과 받는 쪽이 반드시 같은 코드를 써야 하므로 이곳에만 둔다.
"""
