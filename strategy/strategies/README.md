# strategies

每个子目录是一个可移植策略包，复制到 FuturesSpread 后改 config 即可。

本仓库只包含两个示例：

| 目录 | 用途 |
|------|------|
| `example_dom_sub` | 主力×次主力，`calendar_dom_sub` |
| `example_cross_product` | 跨品种，`manual` + instances |

注册：`config/strategies.yaml`，`module: strategies.<name>.strategy`。

约定：

- 不 `import core`（截面走 `utils.panel_registry`）
- 不依赖 `data_sources`（runner 读数据）
