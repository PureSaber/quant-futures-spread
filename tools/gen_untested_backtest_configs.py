"""生成未测因子 backtest yaml 配置。"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"

TEMPLATE = """run_id: {sid}_OI_2024
strategy: {sid}

capital: 1000000

universe:
  mode: calendar_dom_sub
  products: [OI]
  exclude: []
  use_trade_flag: false
  dom_table_dir: D:/data/商品期货-主力合约

data:
  source: csv_spread
  data_dir: D:/data
  years: ['2024']
  future_list: config/future_list.csv

roll:
  on_switch: defer_until_flat

output:
  dir: output
"""

UNTESTED = [
    ("depth_imb@15", "factor_depth_imb_15"),
    ("range_pct@15", "factor_range_pct_15"),
    ("vol_ratio@15", "factor_vol_ratio_15"),
    ("breakout_up@15", "factor_breakout_up_15"),
    ("boll_pct_b@15", "factor_boll_pct_b_15"),
    ("z_bid@15", "factor_z_bid_15"),
    ("z_ask@15", "factor_z_ask_15"),
    ("quote_width@15", "factor_quote_width_15"),
    ("realized_vol_20@15", "factor_realized_vol_20_15"),
    ("realized_vol_120@60", "factor_realized_vol_120_60"),
    ("seasonal_dev@15", "factor_seasonal_dev_15"),
    ("mom_15@15", "factor_mom_15_15"),
    ("mom_60@60", "factor_mom_60_60"),
    ("leg_mom_diff@15", "factor_leg_mom_diff_15"),
    ("eff_spread@15", "factor_eff_spread_15"),
]


def main() -> None:
    for _, sid in UNTESTED:
        path = CONFIG / f"backtest_{sid}.yaml"
        path.write_text(TEMPLATE.format(sid=sid), encoding="utf-8")
        print(f"[ok] {path.name}")
    manifest = CONFIG / "untested_factor_manifest.yaml"
    manifest.write_text(
        yaml.safe_dump({"strategies": [{"label": l, "id": s} for l, s in UNTESTED]}, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"[ok] {manifest}")


if __name__ == "__main__":
    main()
