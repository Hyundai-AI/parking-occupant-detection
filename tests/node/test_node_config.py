from psafe.common.config import load_config


def test_default_config_loads():
    cfg = load_config("config/default.yaml")
    assert cfg.source.name == "dummy"
    assert cfg.fsm.clear_time_s == 3.0
    assert cfg.outputs[0].name == "console"


def test_node_configs_have_link_to_hub():
    for name in ("node_a", "node_b"):
        cfg = load_config(f"config/{name}.yaml")
        assert cfg.link is not None, name
        assert cfg.link.node == name[-1].upper()
