"""tools/audit_dominant_contract.py —— 复查主力合约表清洗结果。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from tools.dominant_contract_clean import contract_yyyymm


def audit_files(data_dir: Path) -> dict:
    files = sorted(data_dir.glob("主力合约表-20*.csv"))
    sub_le_dom: list[dict] = []
    dom_back: list[dict] = []
    sub_back: list[dict] = []
    sub_same_dom_back: list[dict] = []
    unparsed: list[dict] = []
    empty_sub: list[dict] = []

    state_dom: dict[str, int | None] = {}
    state_sub: dict[str, int | None] = {}
    state_sub_at_dom: dict[str, int | None] = {}

    all_days: list[tuple] = []
    for fp in files:
        fy = int(fp.stem.split("-")[-1])
        df = pd.read_csv(fp)
        for (td, prod), g in df.groupby(["tradingday", "product"]):
            piv = g.set_index("contract_type")["contract"].to_dict()
            all_days.append((td, prod, fy, piv, fp.name))

    all_days.sort(key=lambda x: (x[0], x[1]))

    for td, prod, fy, piv, fname in all_days:
        dom = piv.get("主力", "")
        sub = piv.get("次主力", "")
        dk = contract_yyyymm(dom, fy)
        sk = contract_yyyymm(sub, fy)

        if not dom or (isinstance(dom, float) and pd.isna(dom)):
            empty_sub.append({"file": fname, "tradingday": td, "product": prod, "issue": "missing_dom"})
            continue
        if not sub or (isinstance(sub, float) and pd.isna(sub)):
            empty_sub.append({"file": fname, "tradingday": td, "product": prod, "issue": "missing_sub"})
            continue
        if dk is None:
            unparsed.append({"file": fname, "tradingday": td, "product": prod, "contract": dom, "role": "主力"})
        if sk is None:
            unparsed.append({"file": fname, "tradingday": td, "product": prod, "contract": sub, "role": "次主力"})

        if dk and sk and sk <= dk:
            sub_le_dom.append({
                "file": fname, "tradingday": td, "product": prod,
                "主力": dom, "次主力": sub, "dom_k": dk, "sub_k": sk,
            })

        prev_dk = state_dom.get(prod)
        prev_sk_same_dom = state_sub_at_dom.get(prod)
        if dk and prev_dk is not None and dk < prev_dk:
            dom_back.append({
                "file": fname, "tradingday": td, "product": prod,
                "主力": dom, "dom_k": dk, "prev_k": prev_dk,
            })
        if dk and (prev_dk is None or dk >= prev_dk):
            state_dom[prod] = dk

        if (
            dk and sk and prev_dk is not None and dk == prev_dk
            and prev_sk_same_dom is not None and sk < prev_sk_same_dom
        ):
            sub_same_dom_back.append({
                "file": fname, "tradingday": td, "product": prod,
                "主力": dom, "次主力": sub, "sub_k": sk, "prev_sub_k": prev_sk_same_dom,
            })

        if dk and sk:
            if prev_dk is None or dk > prev_dk:
                state_sub_at_dom[prod] = sk
            elif dk == prev_dk and (prev_sk_same_dom is None or sk >= prev_sk_same_dom):
                state_sub_at_dom[prod] = sk

        prev_sk = state_sub.get(prod)
        if sk and prev_sk is not None and sk < prev_sk:
            sub_back.append({
                "file": fname, "tradingday": td, "product": prod,
                "次主力": sub, "sub_k": sk, "prev_k": prev_sk,
                "主力": dom, "dom_k": dk,
            })
        if sk and (prev_sk is None or sk >= prev_sk):
            state_sub[prod] = sk

    return {
        "files": len(files),
        "days": len(all_days),
        "sub_le_dom": pd.DataFrame(sub_le_dom),
        "dom_back": pd.DataFrame(dom_back),
        "sub_back": pd.DataFrame(sub_back),
        "sub_same_dom_back": pd.DataFrame(sub_same_dom_back),
        "unparsed": pd.DataFrame(unparsed),
        "empty": pd.DataFrame(empty_sub),
    }


def main() -> None:
    data_dir = Path("/Volumes/JR/数据文档/商品期货-主力合约")
    out = audit_files(data_dir)
    report = _REPO / "output" / "dominant_contract_clean"
    report.mkdir(parents=True, exist_ok=True)

    for key in ("sub_le_dom", "dom_back", "sub_back", "sub_same_dom_back", "unparsed", "empty"):
        df = out[key]
        path = report / f"recheck_{key}.csv"
        if len(df):
            df.to_csv(path, index=False)
        elif path.is_file():
            path.unlink()

    print("=== 主力合约表复查 ===")
    print(f"files: {out['files']}, product-days: {out['days']}")
    print(f"sub<=dom:                  {len(out['sub_le_dom'])}")
    print(f"dom backward:              {len(out['dom_back'])}")
    print(f"sub backward (global):     {len(out['sub_back'])}")
    print(f"sub backward (同主力):     {len(out['sub_same_dom_back'])}")
    print(f"unparsed contract:         {len(out['unparsed'])}")
    print(f"missing dom/sub:           {len(out['empty'])}")
    print(f"reports: {report}")

    ok = (
        len(out["sub_le_dom"]) == 0
        and len(out["dom_back"]) == 0
        and len(out["sub_same_dom_back"]) == 0
        and len(out["empty"]) == 0
    )
    if not ok:
        raise SystemExit(1)
    print("PASS: 主力单调 + 次主力>主力 + 同主力下次主力不回退 + 无缺失")


if __name__ == "__main__":
    main()
