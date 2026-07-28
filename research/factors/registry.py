"""因子注册表：名称 → 元数据。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FactorMeta:
    name: str
    family: str
    description: str
    interpret_hint: str


FACTOR_REGISTRY: list[FactorMeta] = [
    # A 均值回归
    FactorMeta("z_close", "A_mean_reversion", "价差 close 滚动 z-score", "偏离均值过远后回归"),
    FactorMeta("pct_rank", "A_mean_reversion", "close 在 close_20~80 区间位置", "分位极端→均值回归"),
    FactorMeta("z_bid", "A_mean_reversion", "bid 在 bid 分位带位置", "买盘定价偏离"),
    FactorMeta("z_ask", "A_mean_reversion", "ask 在 ask 分位带位置", "卖盘定价偏离"),
    FactorMeta("boll_pct_b", "A_mean_reversion", "布林带 %B", "通道极端"),
    FactorMeta("half_life_proxy", "A_mean_reversion", "AR(1) 半衰期倒数", "平稳性越强半衰期越短"),
    FactorMeta("coint_gate", "A_mean_reversion", "协整门控 0/1", "仅协整期做 MR"),
    # B 动量
    FactorMeta("mom_5", "B_momentum", "5 分钟动量", "短期趋势延续"),
    FactorMeta("mom_15", "B_momentum", "15 分钟动量", "日内趋势"),
    FactorMeta("mom_60", "B_momentum", "60 分钟动量", "小时趋势"),
    FactorMeta("mom_240", "B_momentum", "240 分钟动量", "半日趋势"),
    FactorMeta("leg_mom_diff_15", "B_momentum", "两腿 15m 收益差", "单腿强弱背离"),
    FactorMeta("breakout_up_60", "B_momentum", "突破 60 日高", "向上突破"),
    FactorMeta("breakout_down_60", "B_momentum", "突破 60 日低", "向下突破"),
    # C 波动
    FactorMeta("realized_vol_20", "C_volatility", "20 期实现波动", "波动水平"),
    FactorMeta("realized_vol_120", "C_volatility", "120 期实现波动", "长期波动"),
    FactorMeta("vol_ratio", "C_volatility", "短/长波动比", "波动聚集"),
    FactorMeta("range_pct", "C_volatility", "(high-low)/close", "振幅"),
    # D 微观结构
    FactorMeta("eff_spread", "D_microstructure", "ask-bid", "流动性成本"),
    FactorMeta("mid_dev", "D_microstructure", "close-中价", "定价偏移"),
    FactorMeta("depth_imb", "D_microstructure", "买卖量失衡", "订单流方向"),
    FactorMeta("quote_width", "D_microstructure", "ask_80-bid_20", "报价带宽度"),
    # E 期限结构
    FactorMeta("carry_ann", "E_term_structure", "年化展期 carry", "期限结构收益"),
    FactorMeta("roll_window", "E_term_structure", "换月窗口标记", "展期噪声"),
    FactorMeta("seasonal_dev", "E_term_structure", "日内分钟季节性偏离", "时段效应"),
    # F 跨品种（单 spread 层面为 z 或残差占位，截面在 eval 补）
    FactorMeta("pair_z", "F_cross_product", "跨品种比价 z", "产业链偏离"),
]


def factor_names() -> list[str]:
    return [f.name for f in FACTOR_REGISTRY]


def family_of(name: str) -> str:
    for f in FACTOR_REGISTRY:
        if f.name == name:
            return f.family
    return "unknown"
