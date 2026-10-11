# 宏观研究 0.2 本机验收 · 2026-10-11

| 检查 | 实际结果 |
|---|---|
| 宏观应用全部测试 | 60 passed，包括 7 个期货规则入口及真实 QExec 回放 |
| Studio 相关回归 | 55 passed：模板、净值、衍生品、版本引用、预检、设置、资产模板 |
| Ruff lint / format | 两侧本次修改通过 |
| 独立环境依赖闭合 | pip check：No broken requirements found |
| 真实 Studio 预检、执行、收集、校验 | 16 条通过 |
| ECB 真实历史 | 2025-01-02 至 2026-10-09；30 对 EUR 参考序列，13,392 条记录 |
| Fed GSW 真实零息曲线 | 2025-01-02 至 2026-10-02；30 个期限，13,170 条记录 |
| iShares LQD 自动下载 | 来源返回 Access Denied；仅格式导入和合成数据测试通过 |

独立环境为 Windows Python 3.12.14，quant-macro 0.2.0，quant-futures-global 0.1.0，
QDK 0.8.1，QExec 0.6.0，pytest 9.1.1，ruff 0.16.10，PyYAML 6.0.3。
第三方依赖按 international/requirements.lock 安装；核心宏观应用没有第三方依赖。
验收脚本的子进程使用 python -I，不依赖临时 PYTHONPATH。

16 条完整流程：FX spot、FX forward、rates valuation、credit valuation、FX portfolio、
bond replay、ETF relative value、ECB reference、Fed GSW reference、6E、ZN、SR3、TS、TF、T、TL。
工作台脚本 scripts/integration_macro.py --public-root ... --futures 可重跑。
本机回执在 SCRATCH/macro-20261011/acceptance-v2-full/receipts.json。

账户检验覆盖：共享币种只计息一次、未结算本金计入净值、零成本不变价格的现金守恒、
逐币种账本到余额再到净值核对、下一观察成交、共同节假日、结算日应计息、
付息日当日结算不重复领取票息、到期本金仅入账一次、空头借券限制和代付分红、
缺少成交报价、未来数据、杠杆和尾部未履约义务拒绝。

宏观 verify 从冻结输入与配置重新生成所有产物；QExec verify 检查产物哈希和每笔分录平衡，
二者校验范围不同。来源真实性、历史数据权利与原始 PIT 完整性均不因此获得认证。
ECB/Fed 是本次取得的历史参考版本，绝不作为真实 bid/ask 或历史融资成本使用。
期货样例只采用公开报价单位；价格、到期日期和保证金仍为明确合成数据。

远端 CI、合并与组合部署由交付回执单独记录；本文件只陈述已经执行的本机验收。
