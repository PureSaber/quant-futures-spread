"""Content-addressed inputs and reproducible, independently hashable outputs."""

import csv
import hashlib
import html
import io
import re
from pathlib import Path

from . import __version__, bond_replay, bonds, fx, fx_portfolio, public_data
from .common import canonical, keys, nonempty, read_json, stamp

LIMITS = (
    "Offline research; modeled fills, no broker or live orders. No market-data or release "
    "certification. Single-pair FX uses same-day settlement; FX portfolios use supplied "
    "joint calendars and T+n settlement, shared currency cash and daily funding. "
    "No collateral model; "
    "cash-settled forwards only. Bonds: explicit fixed bullet cash flows per 100 face, "
    "payment=end, no default/call/put/ex-coupon model; continuous ACT/365F zero curve "
    "anchored at settlement, linear zero interpolation, no extrapolation; z-spread is "
    "not OAS or pure default risk. ETF baskets are indicative valuations, not official NAV "
    "or executable creation/redemption baskets. Treasury DV01 is a CTD approximation; "
    "gross basis excludes carry and delivery options. STIR quotes use 100 minus rate."
    " Bond/ETF replay uses fractional units, explicit settlement calendars, secondary-market "
    "trades, coupon/redemption and ETF distribution events; no defaults, calls or AP creations. "
    "Public reference histories are latest-vintage diagnostics, never executable bid/ask "
    "or original PIT vintages."
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_bundle(path):
    root = Path(path).resolve()
    for filename in ("manifest.json", "data.json"):
        child = root / filename
        if child.is_symlink() or not child.is_file() or child.resolve().parent != root:
            raise ValueError("bundle files must be regular files inside the bundle")
    manifest = read_json(root / "manifest.json")
    data_bytes = (root / "data.json").read_bytes()
    keys(
        manifest,
        (
            "schema_version",
            "evidence_kind",
            "source",
            "license",
            "retrieved_at",
            "data_sha256",
            "quote_use",
            "limits",
        ),
    )
    if manifest["schema_version"] != "quant.macro.bundle/v1":
        raise ValueError("unsupported macro bundle")
    if manifest["evidence_kind"] not in {"synthetic", "retrospective"}:
        raise ValueError("explicit synthetic/retrospective evidence kind required")
    if manifest["quote_use"] not in {"observed_bid_ask", "reference_only"}:
        raise ValueError("quote_use must identify reference or bid/ask data")
    for key in ("source", "license", "limits"):
        nonempty(manifest[key], key)
    stamp(manifest["retrieved_at"])
    if manifest["data_sha256"] != digest(data_bytes):
        raise ValueError("bundle data hash mismatch")
    data = read_json(root / "data.json")
    keys(data, (), ("fx", "fixed_income", "fx_portfolio", "bond_replay", "reference"))
    if not data:
        raise ValueError("empty bundle")
    return manifest, data


def calculate(manifest, data, config):
    if not isinstance(config, dict):
        raise ValueError("configuration object required")
    if config.get("line") == "fx":
        if manifest["quote_use"] != "observed_bid_ask":
            raise ValueError("reference-only FX rates cannot be replayed as tradable quotes")
        c = fx.configuration(config)
        tables = fx.replay(data["fx"], c)
        ids = [data["fx"]["base"] + "/" + data["fx"]["quote"]]
        rows = len(data["fx"]["quotes"])
    elif config.get("line") in {"fx_portfolio", "bond_replay"}:
        if manifest["quote_use"] != "observed_bid_ask":
            raise ValueError("account replay requires observed bid/ask")
        line = config["line"]
        module = fx_portfolio if line == "fx_portfolio" else bond_replay
        c = module.configuration(config)
        tables = module.replay(data[line], c)
        terms = data[line]["pairs" if line == "fx_portfolio" else "instruments"]
        ids = [r["instrument_id"] for r in terms]
        rows = len(data[line]["observations"])
    elif config.get("line") == "reference_history":
        if manifest["quote_use"] != "reference_only":
            raise ValueError("reference history must be labeled reference_only")
        c = public_data.configuration(config)
        tables = public_data.analyze(data["reference"], c)
        ids = [r["instrument_id"] for r in tables["coverage"]]
        rows = len(tables["reference"])
    else:
        c = bonds.configuration(config)
        tables = bonds.analyze(data["fixed_income"], c)
        ids = [r["instrument_id"] for r in tables["valuations"]]
        rows = len(tables["cashflows"])
    return c, tables, ids, rows


def preflight(path, config):
    manifest, data = load_bundle(path)
    c, _, ids, rows = calculate(manifest, data, config)
    return {
        "schema_version": "quant.macro.preflight/v1",
        "software_preflight": "pass",
        "read_only": True,
        "investable": False,
        "rows": rows,
        "contracts": len(ids),
        "instrument_ids": ids,
        "evidence_kind": manifest["evidence_kind"],
        "input_sha256": manifest["data_sha256"],
        "line": c["line"],
        "limits": LIMITS,
    }


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(stream, fieldnames=fields or ["empty"], lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def render_report(study, tables):
    title = {
        "fx": "外汇现货与远期研究",
        "rates": "国债、曲线与利率期货研究",
        "credit": "公司债与债券 ETF 研究",
        "fx_portfolio": "多币种外汇组合账户",
        "bond_replay": "债券与 ETF 账户回放",
        "reference_history": "公开参考数据历史与风险分析",
    }[study["config"]["line"]]
    chunks = [
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>",
        f"<title>{title}</title><style>body{{font:16px system-ui;margin:32px;",
        "color:#182c3a;background:#fafcff}table{border-collapse:collapse;font-size:13px}",
        "td,th{padding:8px;border:1px solid #c7d7e2;text-align:right}",
        ".table{overflow:auto}p{max-width:1000px;line-height:1.6}</style>",
        f"<h1>{title}</h1><p>数据性质：{html.escape(study['evidence_kind'])}；",
        "研究状态：离线研究，未经市场数据认证。</p>",
        f"<p>来源：{html.escape(study['source'])}</p>",
        f"<p>数据边界：{html.escape(study.get('source_limits', ''))}</p>",
        f"<p>{html.escape(LIMITS)}</p>",
    ]
    for name, rows in tables.items():
        chunks.append(f"<h2>{html.escape(name)} · {len(rows)} rows</h2>")
        if not rows:
            continue
        fields = list(dict.fromkeys(key for row in rows for key in row))
        chunks.append(
            "<div class='table'><table><thead><tr>"
            + "".join(f"<th>{html.escape(key)}</th>" for key in fields)
            + "</tr></thead><tbody>"
        )
        for row in rows[:200]:
            chunks.append(
                "<tr>"
                + "".join(f"<td>{html.escape(str(row.get(k, '')))}</td>" for k in fields)
                + "</tr>"
            )
        chunks.append("</tbody></table></div>")
        if len(rows) > 200:
            chunks.append("<p>仅展示前 200 行；完整结果在对应 CSV 文件。</p>")
    chunks.append("</html>")
    return "".join(chunks).encode("utf-8")


def output_files(manifest, data, config):
    c, tables, ids, _ = calculate(manifest, data, config)
    study = {
        "schema_version": "quant.macro.study/v1",
        "engine_version": __version__,
        "evidence_kind": manifest["evidence_kind"],
        "input_sha256": manifest["data_sha256"],
        "source": manifest["source"],
        "source_limits": manifest["limits"],
        "config": c,
        "instrument_ids": ids,
        "market_data_certified": False,
        "release_verified": False,
        "investable": False,
        "limits": LIMITS,
        "table_rows": {k: len(v) for k, v in tables.items()},
    }
    payloads = {name + ".csv": csv_bytes(rows) for name, rows in tables.items()}
    payloads.update(
        {
            "study.json": canonical(study),
            "config.json": canonical(c),
            "input-data.json": canonical(data),
            "input-manifest.json": canonical(manifest),
            "report.html": render_report(study, tables),
        }
    )
    return study, payloads


def run(path, config, output):
    manifest, data = load_bundle(path)
    study, payloads = output_files(manifest, data, config)
    # Preserve original bytes as well as normalized inputs to prove the input identity.
    payloads["input-raw.json"] = (Path(path) / "data.json").read_bytes()
    if digest(payloads["input-raw.json"]) != manifest["data_sha256"]:
        raise ValueError("bundle changed during calculation")
    # Recheck the full pair, including manifest, before publishing any files.
    if load_bundle(path) != (manifest, data):
        raise ValueError("bundle changed during calculation")
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    for name, content in payloads.items():
        (root / name).write_bytes(content)
    artifact = {
        "schema_version": "quant.macro.artifacts/v1",
        "files": {name: digest(content) for name, content in sorted(payloads.items())},
    }
    (root / "artifacts.json").write_bytes(canonical(artifact))
    return study


def verify(output):
    root = Path(output).resolve()
    manifest_file = root / "artifacts.json"
    if manifest_file.is_symlink():
        raise ValueError("symlink artifact manifest is not allowed")
    artifact = read_json(manifest_file)
    keys(artifact, ("schema_version", "files"))
    if artifact["schema_version"] != "quant.macro.artifacts/v1":
        raise ValueError("unsupported artifact schema")
    if not isinstance(artifact["files"], dict) or not artifact["files"]:
        raise ValueError("artifact file list missing")
    required = {"input-data.json", "input-manifest.json", "input-raw.json", "config.json"}
    if not required <= artifact["files"].keys():
        raise ValueError("artifact input file list incomplete")
    for name, expected in artifact["files"].items():
        if not re.fullmatch(r"[a-z_-]+\.(csv|json|html)", name):
            raise ValueError("unsafe artifact path")
        file = root / name
        if file.is_symlink() or file.resolve().parent != root or not file.is_file():
            raise ValueError("artifact file escapes output or is missing")
        if digest(file.read_bytes()) != expected:
            raise ValueError(f"artifact hash mismatch: {name}")
    inputs = read_json(root / "input-data.json")
    manifest = read_json(root / "input-manifest.json")
    if digest((root / "input-raw.json").read_bytes()) != manifest["data_sha256"]:
        raise ValueError("original input identity mismatch")
    if read_json(root / "input-raw.json") != inputs:
        raise ValueError("normalized inputs disagree with original input")
    config = read_json(root / "config.json")
    _, expected = output_files(manifest, inputs, config)
    expected["input-raw.json"] = (root / "input-raw.json").read_bytes()
    if set(artifact["files"]) != set(expected):
        raise ValueError("artifact file set does not match research output")
    for name, value in expected.items():
        if (root / name).read_bytes() != value:
            raise ValueError(f"artifact fails deterministic replay: {name}")
    return {
        "verified": True,
        "reproduced": True,
        "files": len(expected),
        "market_data_certified": False,
    }
