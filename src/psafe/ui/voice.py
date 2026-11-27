"""음성 재생기 (Pi D). 출력 프로그램의 음성 출력(VoiceSink)이 쓴다.

녹음 파일 이름(contract.Voice.name)만 받아 재생한다. 무엇을 언제 말할지는 C의 출력 FSM이 정한다.

    log       재생하지 않고 로그만 남긴다 (개발 PC · 테스트)
    command   외부 명령으로 재생한다. 기본: aplay -q <directory>/<name>.wav
              새 음성이 오면 재생 중인 이전 음성을 끊는다 (지금 상태가 더 중요하다)
"""

import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from psafe.common.registry import Registry

log = logging.getLogger("psafe.ui.voice")


class Player(ABC):
    @abstractmethod
    def play(self, name: str) -> None:
        """막히지 않아야 한다 (하트비트 루프에서 호출된다)."""

    def close(self) -> None: ...


PLAYERS: Registry[Player] = Registry("voice player")


@PLAYERS.register("log")
class LogPlayer(Player):
    def __init__(self):
        self.played: list[str] = []

    def play(self, name: str) -> None:
        self.played.append(name)
        log.info("voice: %s", name)


@PLAYERS.register("command")
class CommandPlayer(Player):
    def __init__(self, directory: str = "assets/voice", command: list[str] | None = None, ext: str = ".wav"):
        self.directory = Path(directory)
        self.command = list(command or ["aplay", "-q"])
        self.ext = ext
        self._proc: subprocess.Popen | None = None

    def play(self, name: str) -> None:
        path = self.directory / f"{name}{self.ext}"
        if not path.is_file():
            log.warning("voice file missing: %s", path)
            return
        self._stop()
        try:
            self._proc = subprocess.Popen([*self.command, str(path)],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            log.warning("voice player failed (%s): %s", self.command[0], e)

    def _stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None

    def close(self) -> None:
        self._stop()
