import json

import pytest
from quant_data_kit.derivatives.demo import write_demo

from qfs_global.cli import main


def test_cli_preflight_run_verify_and_failure_receipts(tmp_path, capsys):
    bundle = write_demo(tmp_path / "bundle", "future")
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    args = ["--bundle", str(bundle.root), "--config", str(config)]
    assert main(["preflight", *args]) == 0
    preflight = json.loads(capsys.readouterr().out)
    assert preflight["software_preflight"] == "pass" and preflight["investable"] is False
    output = tmp_path / "result"
    assert main(["run", *args, "--output", str(output)]) == 0
    capsys.readouterr()
    assert main(["verify", "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"
    for bad in (["run"], ["verify"], ["run", *args]):
        with pytest.raises(SystemExit) as exc:
            main(bad)
        assert exc.value.code == 2
    (output / "report.html").write_text("tampered", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["verify", "--output", str(output)])
    assert exc.value.code == 2
