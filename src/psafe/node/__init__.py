"""카메라 Pi(A·B·RGB) 코드 (docs/ARCHITECTURE.md §3, §7).

    sources/     영상 입력: 가상 시나리오, 영상 파일, 웹캠 (picamera2 예정)
    detectors/   사람 검출: 가상 검출기, YOLO ONNX, YOLO TFLite(Coral)
    health.py    화면 상태 검사: 너무 어두움·밝음, 화면 정지
    roi.py       감시 영역(ROI) 판정
    fsm.py       카메라 Pi의 판단 상태 INIT / OCCUPIED / VERIFYING / CLEAR / FAULT
    confirm.py   Pi C의 재확인(CONFIRM) 요청에 답하기: 요청 이후 프레임만으로 판단
    slot.py      최신 프레임 1장만 넘기는 슬롯 (오래된 프레임이 쌓이지 않게)

규칙: hub · ui를 import하지 않는다. 함께 필요한 것은 psafe.common에 둔다.
"""
