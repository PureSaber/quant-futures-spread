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
| iShares LQD 自动下载 | 首次拒绝访问，同日重试成功：2026-10-08 的 3,169 条真实持仓；补充验收见下 |

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

## 同日最终组合环境补充验收

共享组件合并后的已安装环境重新通过宏观全部 60 项测试、原 16 条工作台流程。
工作台整套回归为 484 passed、25 skipped；测试使用短临时目录以满足 Windows 路径限制。

LQD 官方公开端点重试成功，保留原文件 SHA-256
`42ff77b3f242f3c7a807073639261bb9eebc198b24a2ed6c0e084d14a7834625`。
2026-10-08 快照含 3,169 条持仓记录，已通过已安装 Studio 的预检、运行和产物重新计算，
因此累计完成 17 条流程，其中 3 条是真实公开参考数据，14 条为合成交易／账户数据。
发行商持仓市场值仍属于供应商参考估值，不能据此构造可成交价格、完整付息条款或历史 PIT。

补充回执位于本机 `SCRATCH/macro-20261011/acceptance-lqd-final/receipt.json`；
最终组合的原 16 条回执位于 `SCRATCH/macro-20261011/acceptance-final-stack/receipts.json`。
