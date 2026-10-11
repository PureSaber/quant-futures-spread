import json

from quant_data_kit.derivatives.demo import write_demo
from quant_execution.derivative_replay import verify_artifacts

from qfs_global.workbench import main


def test_portfolio_readonly_preflight_and_run(tmp_path, capsys):
    bundle = write_demo(tmp_path / "bundle", "future")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "account": {},
                "delivery_deadlines": {},
                "legs": [
                    {
                        "product": "DEMO",
                        "direction": 1,
                        "contracts": 1,
                        "strategy": "nearby",
                        "roll_rule": "expiry",
                        "roll_days": 3,
                    }
                ],
            }
        )
    )
    args = ["--kind", "portfolio", "--bundle", str(bundle.root), "--config", str(config)]
    assert main(["preflight", *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["software_preflight"] == "pass" and result["read_only"]
    assert main(["run", *args, "--output", str(tmp_path / "output")]) == 0
    assert verify_artifacts(tmp_path / "output")["status"] == "passed"
