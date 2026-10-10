# 海外期货研究子项目

在既有期货仓库中增加 `qfs_global`，使用独立 Python 3.12 环境和 `requirements.lock`。根目录 `qfs_certified` 及其 QDK v0.8.1 / QExec v0.5.1 / QLab v0.3.1 固定依赖继续独立验收，不继承本子项目的市场适用性。

```powershell
cd international
python -m venv .venv
.venv/Scripts/python -m pip install --no-deps -r requirements.lock
.venv/Scripts/python -m pip install --no-deps --no-build-isolation -e .
.venv/Scripts/python -m pip check
.venv/Scripts/python -m quant_data_kit.derivatives.cli demo --kind future --output demo-bundle
.venv/Scripts/python -m qfs_global.cli preflight --bundle demo-bundle --config examples/demo.json
.venv/Scripts/python -m qfs_global.cli run --bundle demo-bundle --config examples/demo.json --output result
.venv/Scripts/python -m qfs_global.cli verify --output result
```

Linux 使用 `.venv/bin/python`。真实输入为 `qdk.derivatives/v1`，必须明确交易所、时区、合约乘数、最小跳动、日期、币种及规则来源。每项研究限定单交易所、单币种；比较不同国家可分别运行后比较原始报告，不假设外汇换算。

`strategy=nearby` 研究单合约换月；`calendar_spread` 研究近月多、次月空。`roll_rule=expiry` 按到期排序，`volume` 按前一观察的成交量选择。选择在观察可用后作出，下一条日线开盘执行；保留原始合约价格，不能把后复权连续价格当成交易价格。期限结构、跨月价差、换月决定、费用、每日结算、持仓、净值和逐笔账本均有输出。

可选 `mode=analysis` 只分析期限结构与价差；`start/end` 限定回放时间，`roll_days` 是距最后交易日的日历天数。实际成交与结算仍要求当前观察存在；缺持仓价、零成交量、缺结算、保证金不足会失败。没有足够合约月份时组合明确空仓；不会凭空补一条腿。

保证金使用逐合约声明金额相加，不做 SPAN 或组合抵消。实物交割前换月，不模拟仓储和配送；当前账户只支持正价。演示数据中的“DEMO”是功能样例，不代表 CME、ICE 或任何交易所历史规则。汇升连续合约、供应商 OHLC 或外部授权覆盖必须分别核对，不能自动通过账户回放预检。

在 Quant Studio 选择“海外期货与换月研究”，运行环境键是 `quant-futures-global`，源码仓库仍为 `quant-futures-spread`；该键不会改动原认证模板的 Python 环境。输出目录不可覆盖，`verify` 校验报告哈希与逐币种过账平衡。
