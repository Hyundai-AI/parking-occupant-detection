"""카메라 Pi의 출력: 판단(Decision)을 로그·화면으로 남긴다.

    console.py   판단이 바뀔 때 로그 기록
    viewer.py    개발용 화면 표시·녹화 (검출 박스, ROI, 상태). Pi에는 화면이 없으므로 PC에서만

출력 인터페이스(Output, OUTPUTS)는 Pi C의 신호등 출력도 쓰므로 psafe.common.output_api에 있다.
"""

from psafe.node.outputs import console, viewer  # noqa: F401  (구현체 등록용 import)
from psafe.common.output_api import OUTPUTS, Output

__all__ = ["OUTPUTS", "Output"]
