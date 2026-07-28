"""utils/strategy_registry.py —— 策略注册表展开：策略层(开关) → 实例层(品种参数)。

分两层：
    config/strategies.yaml           策略注册表，一条 = 一种策略的总开关
    config/strategy_instances/*.yaml 实例表，defaults + instances[]（每个品种一条）

展开规则：
    实例参数 = {**defaults, **instance}（实例覆盖 defaults，浅合并）
    实例 id  = 实例显式 id；缺省则 "<策略id>__<symbol slug>"
    实例 enabled = 策略 enabled 且 实例 enabled
注册表条目直接带 params（无 instances）时仍按单实例返回。

无 logger 依赖，供 ConfigLoader 与 logger 共用，避免循环导入。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml


def slug(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(text or "").lower()).strip("_")


def _read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def read_registry(config_dir: Path) -> list[dict]:
    data = _read_yaml(config_dir / "strategies.yaml")
    return list(data.get("strategies") or [])


def _expand_instances(sid: str, module: str, strat_enabled: bool,
                      inst_ref: str, config_dir: Path) -> list[dict]:
    data = _read_yaml(config_dir / str(inst_ref))
    defaults = dict(data.get("defaults") or {})
    out: list[dict] = []
    for raw in data.get("instances") or []:
        inst = dict(raw)
        inst_enabled = bool(inst.pop("enabled", True))
        explicit_id = str(inst.pop("id", "") or "").strip()
        params = {**defaults, **inst}
        symbol = str(params.get("symbol") or "").strip()
        out.append({
            "id": explicit_id or f"{sid}__{slug(symbol)}",
            "module": module,
            "enabled": strat_enabled and inst_enabled,
            "params": params,
        })
    return out


def expand_registry(registry: list[dict], config_dir: Path) -> list[dict]:
    """注册表 → 扁平实例列表（每个实例 = {id, module, enabled, params}）。"""
    out: list[dict] = []
    seen: set[str] = set()
    for entry in registry:
        sid = str(entry.get("id") or "").strip()
        module = str(entry.get("module") or "").strip()
        enabled = bool(entry.get("enabled", True))
        inst_ref = entry.get("instances")
        if inst_ref:
            expanded = _expand_instances(sid, module, enabled, inst_ref, config_dir)
        else:
            params = dict(entry.get("params") or {})
            expanded = [{"id": sid, "module": module, "enabled": enabled, "params": params}]
        for item in expanded:
            iid = item["id"]
            if iid in seen:
                raise ValueError(f"策略实例 id 冲突: {iid!r}（请在实例上显式指定 id）")
            seen.add(iid)
            out.append(item)
    return out


def load_strategies(config_dir: Path) -> list[dict]:
    return expand_registry(read_registry(config_dir), config_dir)
