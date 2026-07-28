"""
run_backtest.py  ——  统一回测入口（FuturesSpread 契约）

    python run_backtest.py --config config/backtest_example_dom_sub.yaml
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from performance import summarize


def main() -> None:
    ap = argparse.ArgumentParser(description="FuturesSpread 契约回测")
    ap.add_argument("--config", required=True, help="config/backtest_*.yaml")
    ap.add_argument("--config-dir", default=None,
                    help="strategies.yaml 所在目录，默认同 --config 文件目录（通常为 config/）")
    ap.add_argument("--jobs", type=int, default=None,
                    help="并行进程数（默认取 yaml 的 jobs，否则 1=串行）")
    args = ap.parse_args()

    import logging
    from utils.strategy_bootstrap import bootstrap_strategy_path
    bootstrap_strategy_path()
    logging.getLogger("Strategy").setLevel(logging.WARNING)

    from core.io.config_loader import load_backtest_config
    from core.engine.runner import run_backtest
    from core.io.output import write_outputs

    cdir = args.config_dir or os.path.dirname(os.path.abspath(args.config))
    cfg = load_backtest_config(args.config, cdir)
    if args.jobs is not None:
        cfg.jobs = max(1, args.jobs)
    t0 = time.time()
    print(f"[run] run_id={cfg.run_id} products={cfg.products} years={cfg.years} "
          f"strategies={len(cfg.strategies)} jobs={cfg.jobs}", flush=True)
    result = run_backtest(cfg)
    out_root = write_outputs(cfg, result)
    dt = time.time() - t0
    if result.portfolio_daily is not None and len(result.portfolio_daily):
        s = summarize(result.portfolio_daily, cfg.capital)
        print(f"[done] {dt:.1f}s  策略累计收益={s['total_return']:.6f}  "
              f"夏普={s['sharpe']}  最大回撤={s['max_drawdown']:.6f}", flush=True)
    print(f"[out ] {out_root}", flush=True)


if __name__ == "__main__":
    main()
