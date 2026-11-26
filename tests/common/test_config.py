import pytest

from psafe.common.config import ConfigError, parse_config

# 설정 파일(config/*.yaml)을 읽는 검사는 설정 파일과 함께 node 단계에서 추가한다.
MIN = {"source": {"name": "dummy"}, "model": {"name": "dummy"}}


def test_defaults_fill_missing_sections():
    cfg = parse_config(MIN)
    assert cfg.runner.mode == "sync"
    assert cfg.fsm.clear_min_frames == 10


def test_typo_is_an_error():
    with pytest.raises(ConfigError, match="clear_tme_s"):
        parse_config({**MIN, "fsm": {"clear_tme_s": 1.0}})


def test_missing_required_section():
    with pytest.raises(ConfigError):
        parse_config({"source": {"name": "dummy"}})


def test_bad_runner_mode():
    with pytest.raises(ConfigError, match="mode"):
        parse_config({**MIN, "runner": {"mode": "async"}})
