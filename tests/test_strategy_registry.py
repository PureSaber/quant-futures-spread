"""tests/test_strategy_registry.py —— 策略注册表两层展开（仅 pyyaml）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.strategy_registry import expand_registry, load_strategies, slug

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "config"


def ok(cond, msg):
    assert cond, msg


def _write_dir(strategies: dict, instances: dict[str, dict]) -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "strategies.yaml").write_text(yaml.safe_dump(strategies, allow_unicode=True), encoding="utf-8")
    inst_dir = d / "strategy_instances"
    inst_dir.mkdir()
    for name, doc in instances.items():
        (inst_dir / name).write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return d


def _load_yaml_registry(d: Path) -> list:
    data = yaml.safe_load((d / "strategies.yaml").read_text(encoding="utf-8"))
    return data["strategies"]


def test_slug():
    ok(slug("A2003&A2005") == "a2003_a2005", "slug 规范化")


def test_defaults_and_override():
    d = _write_dir(
        {"strategies": [{"id": "g", "module": "m", "enabled": True,
                         "instances": "strategy_instances/g.yaml"}]},
        {"g.yaml": {
            "defaults": {"source": "comb", "type": "long", "close_mode": 0},
            "instances": [
                {"symbol": "A2003&A2005"},
                {"symbol": "A2005&A2007", "close_mode": 3, "close_args": {"close_price": 12.0}},
                {"symbol": "A2007&A2009", "enabled": False},
            ],
        }},
    )
    out = expand_registry(_load_yaml_registry(d), d)
    ok(len(out) == 3, f"展开 3 实例 (got {len(out)})")
    a = next(s for s in out if s["params"]["symbol"] == "A2003&A2005")
    ok(a["params"]["close_mode"] == 0, "未配字段走 defaults")
    ok(a["id"] == "g__a2003_a2005", f"自动 id (got {a['id']})")
    c = next(s for s in out if s["params"]["symbol"] == "A2005&A2007")
    ok(c["params"]["close_mode"] == 3, "实例覆盖 close_mode")
    e = next(s for s in out if s["params"]["symbol"] == "A2007&A2009")
    ok(e["enabled"] is False, "实例 enabled=false")


def test_strategy_disabled_cascades():
    d = _write_dir(
        {"strategies": [{"id": "g", "module": "m", "enabled": False,
                         "instances": "strategy_instances/g.yaml"}]},
        {"g.yaml": {"defaults": {}, "instances": [{"symbol": "A2003&A2005"}]}},
    )
    out = expand_registry(_load_yaml_registry(d), d)
    ok(out[0]["enabled"] is False, "策略 enabled=false 关闭全部实例")


def test_id_collision():
    d = _write_dir(
        {"strategies": [{"id": "g", "module": "m", "enabled": True,
                         "instances": "strategy_instances/g.yaml"}]},
        {"g.yaml": {"defaults": {}, "instances": [
            {"symbol": "A2003&A2005"}, {"symbol": "A2003&A2005"},
        ]}},
    )
    try:
        expand_registry(_load_yaml_registry(d), d)
        ok(False, "应抛出 ValueError")
    except ValueError:
        pass


def test_legacy_single_instance():
    out = expand_registry(
        [{"id": "old", "module": "m", "enabled": True, "params": {"symbol": "A2003&A2005"}}],
        Path("/tmp"),
    )
    ok(len(out) == 1 and out[0]["params"]["symbol"] == "A2003&A2005", "旧单实例写法")


def test_repo_config():
    out = load_strategies(_CONFIG)
    xprod = [s for s in out if s["id"].startswith("example_cross_product__")]
    ok(len(xprod) == 3, f"example_cross_product 展开 3 实例 (got {len(xprod)})")
    ok(all(s["enabled"] for s in xprod), "默认全部启用")
    one = xprod[0]
    ok(one["module"] == "strategies.example_cross_product.strategy", "module 来自注册表")
    ok(one["params"]["source"] == "comb", "defaults source=comb")
    dom = [s for s in out if s["id"] == "example_dom_sub"]
    ok(len(dom) == 1 and dom[0]["params"]["lookback"] == 60, "dom_sub 单实例 params")


if __name__ == "__main__":
    test_slug()
    test_defaults_and_override()
    test_strategy_disabled_cascades()
    test_id_collision()
    test_legacy_single_instance()
    test_repo_config()
    print("ALL STRATEGY-REGISTRY TESTS PASSED")
