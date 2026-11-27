"""실행 진입점. 설정 파일에 따라 이 Pi의 역할을 정한다 (docs/ARCHITECTURE.md §7.3).

카메라 Pi (A·B·RGB)
    python -m psafe --config config/node_a.yaml      (카메라 노드, C에 보고)
    python -m psafe                                  (config/default.yaml: 보드 1대 단독 실행)
    python -m psafe --mode threaded                  (실시간, 3스레드. Ctrl+C로 종료)

판정 Pi (C): 설정 파일에 `hub:` 절이 있으면 허브로 실행한다.
    python -m psafe --config config/hub_c.yaml

출력 Pi (D): 설정 파일에 `output_client:` 절이 있으면 출력 프로그램으로 실행한다 (판정하지 않음).
    python -m psafe --config config/output_d.yaml

가상 시나리오 검사 (개발 PC · CI)
    python -m psafe --scenario scenarios/flicker.yaml --check
    python -m psafe --scenario "scenarios/*.yaml" --check                    (전체, 즉시 실행)
    python -m psafe --scenario "scenarios/*.yaml" --check --mode threaded    (전체, 실시간)

역할이 정해진 뒤에 그 계층만 import한다. 그래서 Pi C에는 node가 쓰는 OpenCV·YOLO 런타임이
설치되어 있지 않아도 된다.
"""

import argparse
import glob
import logging
import sys

from psafe.common.config import ConfigError, is_hub_config, parse_config, parse_hub_config, read_yaml


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="psafe", description="Parking occupant detection pipeline")
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--mode", choices=["sync", "threaded"], help="override runner.mode")
    ap.add_argument("--scenario", help="scenario file or glob; runs with the dummy source")
    ap.add_argument("--check", action="store_true", help="verify the scenario's expect list (exit 1 on failure)")
    ap.add_argument("--max-frames", type=int, default=None, help="sync mode only")
    ap.add_argument("--seconds", type=float, default=None, help="threaded mode only: stop after N s")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true", help="hide per-decision logs")
    args = ap.parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.WARNING if args.quiet else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s.%(msecs)03d %(levelname)-5s %(threadName)-9s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        if args.scenario:
            return _run_scenarios(args)
        data = read_yaml(args.config)
        if is_hub_config(data):
            return _run_hub(parse_hub_config(data), args)
        if "output_client" in data:
            return _run_output(data, args)
        cfg = parse_config(data)
    except (ConfigError, FileNotFoundError, ValueError) as e:
        logging.error("config error: %s", e)
        return 2
    return _run_node(cfg, args)


def _run_node(cfg, args: argparse.Namespace) -> int:
    """카메라 Pi: 설정에 따라 부품을 만들고 실행기를 돌린다."""
    from psafe.node.app import build
    from psafe.node.runners import SyncRunner, ThreadedRunner

    if args.mode:
        cfg.runner.mode = args.mode
        if cfg.runner.clock == "fake" and args.mode == "threaded":
            cfg.runner.clock = "real"
    try:
        c = build(cfg)
    except (ConfigError, FileNotFoundError, ValueError) as e:
        logging.error("config error: %s", e)
        return 2

    try:
        if cfg.runner.mode == "sync":
            SyncRunner(c, max_frames=args.max_frames).run()
            return 0
        result = ThreadedRunner(c, max_seconds=args.seconds).run()
    except (FileNotFoundError, RuntimeError) as e:  # 모델 / 카메라 / 영상 열기 실패
        logging.error("startup failed: %s", e)
        return 2
    return 1 if result.error else 0


def _run_hub(cfg, args: argparse.Namespace) -> int:
    """판정 Pi (C): hub가 판정하고, 출력은 ui의 구현체를 이름으로 골라 만든다."""
    from psafe.hub import HubRunner, build_hub
    try:
        import psafe.ui  # noqa: F401  (출력 구현체 등록용 import)
    except ModuleNotFoundError:
        logging.warning("ui 계층(psafe.ui)이 없어 등록된 출력이 없습니다")
    try:
        hub = build_hub(cfg)
    except KeyError as e:         # 설정의 출력 이름이 등록되어 있지 않음
        logging.error("config error: %s", e)
        return 2
    try:
        result = HubRunner(hub, max_seconds=args.seconds).run()
    except OSError as e:          # 포트 사용 중 등
        logging.error("startup failed: %s", e)
        return 2
    return 1 if result.error else 0


def _run_output(data, args: argparse.Namespace) -> int:
    """출력 Pi (D): C의 상태를 받아 신호등·음성으로 출력만 한다."""
    from psafe.ui.client import OutputApp, parse_output_config
    try:
        app = OutputApp(parse_output_config(data))
    except (KeyError, ValueError) as e:          # 등록되지 않은 sink 이름, 잘못된 설정
        logging.error("config error: %s", e)
        return 2
    try:
        return app.run(max_seconds=args.seconds)
    except (OSError, RuntimeError) as e:         # 시리얼 포트 열기 실패 등
        logging.error("startup failed: %s", e)
        return 2


def _run_scenarios(args: argparse.Namespace) -> int:
    from psafe.node.scenario_check import run_scenario

    paths = sorted(glob.glob(args.scenario)) or [args.scenario]
    mode = args.mode or "sync"
    failed = 0
    for p in paths:
        report = run_scenario(p, args.config, mode=mode)
        if args.check:
            print(report.summary(), flush=True)
            failed += not report.passed
    if args.check and len(paths) > 1:
        print(f"\n{len(paths) - failed}/{len(paths)} scenarios passed ({mode})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
