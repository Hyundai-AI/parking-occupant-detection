from abc import ABC, abstractmethod

from psafe.common.packets import Decision, FrameDetections, FramePacket
from psafe.common.registry import Registry
from psafe.common.verdict import Verdict


class Output(ABC):
    """판단(Decision)을 받아 처리한다.

    send()      하트비트마다 호출된다 (바뀔 때만이 아님). 가볍고, 오래 막히면
                안 된다. ESP32가 받는 신호가 이것이다.
    on_frame()  선택. worker 스레드에서 처리된 프레임마다 1번, 이미지와 검출
                결과와 함께 호출된다. 화면 표시·녹화용. 추론 루프 안에서
                돌기 때문에 빨라야 한다.
    poll()      선택. 메인 스레드에서 초당 ~20번 호출된다. 많은 GUI 백엔드가
                메인 스레드를 요구하므로 GUI 호출(imshow, waitKey)은 여기에
                둔다. False를 반환하면 Runner에 종료를 요청한다 (예: 'q').
    """

    def open(self) -> None: ...

    @abstractmethod
    def send(self, decision: Decision) -> None: ...

    def on_frame(self, pkt: FramePacket, dets: FrameDetections | None, decision: Decision) -> None: ...

    def poll(self) -> bool:
        return True

    def close(self) -> None: ...


OUTPUTS: Registry[Output] = Registry("output")


class VerdictOutput(ABC):
    """Pi C의 최종 판정(Verdict)을 받아 보여 준다. 화면·음성·신호등·관리자 알림이 이 틀을 따른다.

    show()   하트비트마다(200 ms) 호출된다. 바뀔 때만이 아니다. 가볍고 막히면 안 된다.
    poll()   선택. 메인 스레드에서 호출된다 (GUI용). False = 종료 요청.

    hub는 이 틀과 등록부(VERDICT_OUTPUTS)만 알고, 구현체는 ui에 있다 (docs/ARCHITECTURE.md §7.2).
    """

    def open(self) -> None: ...

    @abstractmethod
    def show(self, verdict: Verdict) -> None: ...

    def poll(self) -> bool:
        return True

    def close(self) -> None: ...


VERDICT_OUTPUTS: Registry[VerdictOutput] = Registry("verdict output")
