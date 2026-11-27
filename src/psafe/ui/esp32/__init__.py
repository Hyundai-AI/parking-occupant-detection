"""신호등 ESP32 (Pi D에서 사용). 프로토콜 v1, 시리얼 출력, 가짜 ESP32.

    protocol.py    5바이트 패킷 형식과 수신 규칙 (펌웨어와 같은 규칙)
    serial_out.py  USB 시리얼로 ESP32에 송신        (Output "serial", pyserial 필요)
    mock_mcu.py    ESP32 없이 같은 규칙으로 동작     (Output "mock_mcu", 개발·테스트용)

펌웨어: firmware/esp32/psafe_gate/psafe_gate.ino
"""

from psafe.ui.esp32 import mock_mcu, serial_out  # noqa: F401  (구현체 등록용 import)
