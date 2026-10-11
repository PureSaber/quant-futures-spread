import json
from datetime import UTC, datetime

import pytest
from quant_data_kit.derivatives import load_bundle
from quant_execution.derivative_replay import verify_artifacts

from qfs_global.free_reference import collect_references, parse_reference
from qfs_global.research import main


@pytest.fixture
def payload():
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "symbol": "CL=F",
                        "instrumentType": "FUTURE",
                        "currency": "USD",
                        "exchangeTimezoneName": "America/New_York",
                    },
                    "timestamp": [
                        int(datetime(2020, 4, d, 13, tzinfo=UTC).timestamp()) for d in (20, 21, 22)
                    ],
                    "indicators": {
                        "quote": [
                            {
                                "open": [-3, 2, None],
                                "high": [-1, 3, None],
                                "low": [-40, 1, None],
                                "close": [-37, 2, None],
                                "volume": [100, 200, None],
                            }
                        ]
                    },
                }
            ],
        }
    }


def test_reference_preserves_negative_prices_and_null_gaps(payload):
    rows, quality = parse_reference(
        json.dumps(payload).encode(), "CL=F", "2020-04-20", "2020-04-22"
    )
    assert len(rows) == 2 and rows[0]["close"] == "-37"
    assert quality["null_sessions_excluded"] == ["2020-04-22"]
    assert quality["rows"] == 2
    payload["chart"]["result"][0]["meta"].update(symbol="ZC=F", currency="USX")
    rows, _ = parse_reference(json.dumps(payload).encode(), "ZC=F", "2020-04-20", "2020-04-22")
    assert rows[0]["currency"] == "USX"  # never silently coerce cents to dollars


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["chart"].update(error="restricted"),
        lambda p: p["chart"].update(result=[]),
        lambda p: p["chart"]["result"][0]["meta"].update(symbol="OTHER"),
        lambda p: p["chart"]["result"][0].update(timestamp=[]),
        lambda p: p["chart"]["result"][0]["indicators"]["quote"][0]["close"].pop(),
        lambda p: p["chart"]["result"][0]["timestamp"].__setitem__(
            1, p["chart"]["result"][0]["timestamp"][0]
        ),
        lambda p: p["chart"]["result"][0]["timestamp"].__setitem__(0, True),
        lambda p: p["chart"]["result"][0]["indicators"]["quote"][0]["low"].__setitem__(0, 5),
        lambda p: p["chart"]["result"][0]["indicators"]["quote"][0]["volume"].__setitem__(0, -1),
    ],
)
def test_reference_rejects_schema_drift(payload, mutation):
    mutation(payload)
    with pytest.raises(ValueError):
        parse_reference(json.dumps(payload).encode(), "CL=F", "2020-04-20", "2020-04-22")


def test_reference_artifacts_cannot_become_contract_bundle(payload, tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return json.dumps(payload).encode()

    result = collect_references(
        tmp_path / "references", "CL=F", "2020-04-20", "2020-04-22", fetch=fetch
    )
    assert result["allowed_modes"] == ["reference_analysis"]
    assert len(calls) == 1 and "CL%3DF" in calls[0]
    assert verify_artifacts(tmp_path / "references")["status"] == "passed"
    with pytest.raises((ValueError, OSError)):
        load_bundle(tmp_path / "references")


@pytest.mark.parametrize(
    "symbols,start,end",
    [
        ("CL=F,CL=F", "2020-01-01", "2020-01-02"),
        ("OTHER", "2020-01-01", "2020-01-02"),
        ("CL=F", "2020-01-02", "2020-01-01"),
        ("CL=F", "2000-01-01", "2025-01-01"),
        ("CL=F", "2099-01-01", "2099-01-02"),
    ],
)
def test_bad_reference_requests_never_fetch(tmp_path, symbols, start, end):
    def fail(_):
        pytest.fail("invalid input issued a request")

    with pytest.raises(ValueError):
        collect_references(tmp_path / "bad", symbols, start, end, fetch=fail)


def test_reference_empty_and_unknown(payload):
    raw = json.dumps(payload).encode()
    with pytest.raises(ValueError):
        parse_reference(raw, "OTHER", "2020-04-20", "2020-04-22")
    with pytest.raises(ValueError, match="empty"):
        parse_reference(raw, "CL=F", "2021-04-20", "2021-04-22")


def test_reference_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        "qfs_global.research.collect_references",
        lambda *a: {"allowed_modes": ["reference_analysis"]},
    )
    assert (
        main(
            [
                "free-reference",
                "--symbols",
                "CL=F",
                "--start",
                "2020-01-01",
                "--end",
                "2020-02-01",
                "--output",
                str(tmp_path / "out"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["allowed_modes"] == ["reference_analysis"]
