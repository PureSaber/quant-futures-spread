# quant-futures-spread

Private futures spread research engine (local path: `future_spread_analysis-team-framework`).

## Commands

```bash
pip install -e ".[dev]"
python run_backtest.py --config config/backtest_example_dom_sub.yaml
python -m pytest tests/ -q
ruff check .
```

## Data paths

Set `QUANT_FUTURES_DATA_ROOT` or copy `config/data_paths.example.yaml` to a local override.
Default data root: `D:/data`.

## Related

- [quant-report-hub](../quant-report-hub) spread adapter reads `output/`
- [quant-lab](../quant-lab) indexes runs from `output/`
