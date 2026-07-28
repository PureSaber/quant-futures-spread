"""tools/dominant_contract_clean.py —— 主力合约表清洗。

规则（主对次 spread 用）：
1. 次主力合约月份必须严格晚于主力（同一 tradingday 内往后不往前）。
2. 次次主力必须严格晚于次主力（若可解析）。
3. 时间轴上主力交割月只进不退：曾出现主力=09 后，不得再出现主力=05；
   若原始数据回退，锁定为历史最高主力，次主力同步前移（如 09&11）。
4. 同一主力月份下，次主力也不得比历史最高次主力更靠前。

用法：
    python tools/dominant_contract_clean.py --data-dir "/Volumes/JR/数据文档/商品期货-主力合约"
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

_CONTRACT_RE = re.compile(r"^([A-Z]+)(\d+)$")
_REPO = Path(__file__).resolve().parent.parent
_DEFAULT_FUTURE_LIST = _REPO / "config" / "future_list.csv"
_DELIVERY_MONTHS: dict[str, list[int]] | None = None


@dataclass
class ProductState:
    max_dom_k: int | None = None
    max_dom: str = ""
    max_sub_k: int | None = None
    max_sub: str = ""


@dataclass
class AuditRow:
    file: str
    tradingday: str
    product: str
    status: str
    主力_before: str
    次主力_before: str
    次次主力_before: str
    主力_after: str
    次主力_after: str
    次次主力_after: str


def contract_yyyymm(contract: str, file_year: int) -> int | None:
    if contract is None or (isinstance(contract, float) and pd.isna(contract)):
        return None
    s = str(contract).strip().upper()
    m = _CONTRACT_RE.match(s)
    if not m:
        return None
    digits = m.group(2)
    if len(digits) == 4:
        yy, mm = int(digits[:2]), int(digits[2:])
        yyyy = 2000 + yy if yy < 80 else 1900 + yy
    elif len(digits) == 3:
        decade = (file_year // 10) * 10
        yyyy = decade + int(digits[0])
        mm = int(digits[1:])
    else:
        return None
    if mm < 1 or mm > 12:
        return None
    return yyyy * 100 + mm


def product_of(contract: str) -> str:
    m = _CONTRACT_RE.match(str(contract).strip().upper())
    return m.group(1) if m else ""


def _load_delivery_months(path: Path) -> dict[str, list[int]]:
    df = pd.read_csv(path, index_col=0)
    out: dict[str, list[int]] = {}
    for prod, row in df.iterrows():
        raw = row.get("main_month")
        if pd.isna(raw) or not str(raw).strip():
            raw = row.get("spspd_month")
        if pd.isna(raw) or not str(raw).strip():
            continue
        try:
            months = [int(x) for x in ast.literal_eval(str(raw))]
        except (ValueError, SyntaxError):
            continue
        if months:
            out[str(prod).upper()] = sorted(set(months))
    return out


def get_delivery_months(product: str) -> list[int] | None:
    global _DELIVERY_MONTHS
    if _DELIVERY_MONTHS is None:
        if _DEFAULT_FUTURE_LIST.is_file():
            _DELIVERY_MONTHS = _load_delivery_months(_DEFAULT_FUTURE_LIST)
        else:
            _DELIVERY_MONTHS = {}
    return _DELIVERY_MONTHS.get(product.upper())


def next_delivery_yyyymm(dom_yyyymm: int, product: str) -> int | None:
    months = get_delivery_months(product)
    if not months:
        return None
    y, m = divmod(dom_yyyymm, 100)
    for _ in range(36):
        later = [mm for mm in months if mm > m]
        if later:
            return y * 100 + later[0]
        y += 1
        m = 0
    return None


def format_contract(product: str, yyyymm: int, template: str) -> str:
    yy, mm = divmod(yyyymm, 100)
    yy2 = yy % 100
    tmpl = str(template).strip().upper()
    m = _CONTRACT_RE.match(tmpl)
    if m and len(m.group(2)) == 3:
        return f"{product}{yy % 10}{mm:02d}"
    return f"{product}{yy2:02d}{mm:02d}"


def _unique_pool(*contracts: str, file_year: int) -> list[tuple[int, str]]:
    seen: set[str] = set()
    out: list[tuple[int, str]] = []
    for c in contracts:
        if not c or (isinstance(c, float) and pd.isna(c)):
            continue
        s = str(c).strip().upper()
        if s in seen:
            continue
        k = contract_yyyymm(s, file_year)
        if k is None:
            continue
        seen.add(s)
        out.append((k, s))
    out.sort(key=lambda x: x[0])
    return out


def fix_trio(
    dom: str,
    sub: str,
    sub3: str,
    file_year: int,
    min_sub_yyyymm: int | None = None,
) -> tuple[str, str, str, str]:
    """同日：次主力严格晚于主力；min_sub_yyyymm 用于跨日单调（不得低于历史次主力）。"""
    dom_k = contract_yyyymm(dom, file_year)
    if dom_k is None:
        return dom, sub, sub3, "unparsed_dom"

    floor_k = min_sub_yyyymm if min_sub_yyyymm is not None else dom_k

    pool = _unique_pool(dom, sub, sub3, file_year=file_year)
    forward = [c for k, c in pool if k > dom_k and k >= floor_k]
    if not forward:
        sub_k = contract_yyyymm(sub, file_year)
        if sub_k is not None and sub_k > dom_k and sub_k >= floor_k:
            new_sub, new_sub3 = sub, sub3
        else:
            prod = product_of(dom)
            if floor_k > dom_k:
                nxt = floor_k
            else:
                nxt = next_delivery_yyyymm(dom_k, prod)
            if nxt is None or nxt <= dom_k or nxt < floor_k:
                return dom, sub, sub3, "no_forward_in_pool"
            new_sub = format_contract(prod, nxt, dom)
            nxt2 = next_delivery_yyyymm(nxt, prod)
            new_sub3 = format_contract(prod, nxt2, dom) if nxt2 else ""
    else:
        new_sub = forward[0]
        sub_k = contract_yyyymm(new_sub, file_year)
        forward2 = [c for k, c in pool if k > sub_k]
        if forward2:
            new_sub3 = forward2[0]
        else:
            prod = product_of(dom)
            nxt2 = next_delivery_yyyymm(sub_k, prod)
            new_sub3 = format_contract(prod, nxt2, dom) if nxt2 else ""
            sub3_k = contract_yyyymm(sub3, file_year)
            if not new_sub3 and sub3_k is not None and sub3_k > sub_k:
                new_sub3 = sub3

    if str(new_sub3) == str(new_sub):
        new_sub3 = ""

    sub_k = contract_yyyymm(new_sub, file_year)
    sub3_k = contract_yyyymm(new_sub3, file_year) if new_sub3 else None
    if sub_k is None or sub_k <= dom_k:
        return dom, sub, sub3, "fix_failed"
    if new_sub3 and sub3_k is not None and sub3_k <= sub_k:
        new_sub3 = ""

    status = "ok" if (new_sub == sub and (str(new_sub3 or "") == str(sub3 or ""))) else "fixed"
    return dom, new_sub, new_sub3, status


def _sub_not_before(state: ProductState, dom_k: int, sub_k: int | None) -> int | None:
    """次主力不得低于历史最高次主力（且必须 > 主力）。"""
    prod = product_of(state.max_dom or "")
    floor_k = next_delivery_yyyymm(dom_k, prod) if prod else None
    target = floor_k
    if state.max_sub_k is not None:
        if target is None or state.max_sub_k > target:
            target = state.max_sub_k
    if sub_k is not None and target is not None and sub_k >= target:
        return sub_k
    return target


def enforce_time_monotone(
    dom: str,
    sub: str,
    sub3: str,
    state: ProductState,
    file_year: int,
) -> tuple[str, str, str, str | None]:
    """跨日：主力只进不退；次主力不得回退到更早期次主力。"""
    if state.max_dom_k is None:
        return dom, sub, sub3, None

    dk = contract_yyyymm(dom, file_year)
    sk = contract_yyyymm(sub, file_year)
    if dk is None:
        return dom, sub, sub3, None

    changed = False
    if dk < state.max_dom_k:
        dom = state.max_dom
        dk = state.max_dom_k
        changed = True

    target_sk = _sub_not_before(state, dk, sk)
    if target_sk is not None and (sk is None or sk < target_sk):
        prod = product_of(dom)
        sub = format_contract(prod, target_sk, dom)
        changed = True

    if changed:
        min_sk = _sub_not_before(state, dk, contract_yyyymm(sub, file_year))
        dom, sub, sub3, _ = fix_trio(dom, sub, sub3, file_year, min_sub_yyyymm=min_sk)
        return dom, sub, sub3, "dom_time_monotone"

    if sk is not None and state.max_sub_k is not None and sk < state.max_sub_k:
        prod = product_of(dom)
        sub = format_contract(prod, state.max_sub_k, dom)
        min_sk = state.max_sub_k
        dom, sub, sub3, _ = fix_trio(dom, sub, sub3, file_year, min_sub_yyyymm=min_sk)
        return dom, sub, sub3, "sub_time_monotone"

    return dom, sub, sub3, None


def _update_state(state: ProductState, dom: str, sub: str, file_year: int) -> None:
    dk = contract_yyyymm(dom, file_year)
    sk = contract_yyyymm(sub, file_year)
    if dk is not None and (state.max_dom_k is None or dk >= state.max_dom_k):
        state.max_dom_k = dk
        state.max_dom = dom
    if sk is not None and (state.max_sub_k is None or sk >= state.max_sub_k):
        state.max_sub_k = sk
        state.max_sub = sub


def clean_all_files(
    files: list[Path],
    dry_run: bool,
    backup_dir: Path | None,
) -> pd.DataFrame:
    """按全时间轴（跨年）清洗，保证主力序列单调。"""
    entries: list[tuple[pd.Timestamp, str, Path, int, dict]] = []
    for fp in files:
        file_year = int(fp.stem.split("-")[-1])
        df = pd.read_csv(fp)
        df["tradingday"] = pd.to_datetime(df["tradingday"])
        for (td, product), g in df.groupby(["tradingday", "product"], sort=False):
            piv = {r.contract_type: r.contract for r in g.itertuples()}
            entries.append((pd.Timestamp(td), str(product), fp, file_year, piv))
    entries.sort(key=lambda x: (x[0], x[1]))

    states: dict[str, ProductState] = {}
    audits: list[AuditRow] = []
    out_by_file: dict[Path, list[dict]] = {fp: [] for fp in files}

    for td, product, fp, file_year, piv in entries:
        dom = piv.get("主力", "")
        sub = piv.get("次主力", "")
        sub3 = piv.get("次次主力", "")
        before = (dom, sub, sub3)

        state = states.setdefault(product, ProductState())

        dom, sub, sub3, st1 = fix_trio(dom, sub, sub3, file_year)
        dom, sub, sub3, st2 = enforce_time_monotone(dom, sub, sub3, state, file_year)

        _update_state(state, dom, sub, file_year)

        status = st2 or st1
        if status not in ("ok", None):
            audits.append(AuditRow(
                file=fp.name,
                tradingday=str(td.date()),
                product=product,
                status=status,
                主力_before=before[0], 次主力_before=before[1], 次次主力_before=before[2],
                主力_after=dom, 次主力_after=sub, 次次主力_after=sub3,
            ))
        elif before != (dom, sub, sub3):
            audits.append(AuditRow(
                file=fp.name,
                tradingday=str(td.date()),
                product=product,
                status=st1 or "fixed",
                主力_before=before[0], 次主力_before=before[1], 次次主力_before=before[2],
                主力_after=dom, 次主力_after=sub, 次次主力_after=sub3,
            ))

        for ctype in ("主力", "次主力", "次次主力"):
            val = {"主力": dom, "次主力": sub, "次次主力": sub3}[ctype]
            if ctype == "次次主力" and not val:
                continue
            out_by_file[fp].append({
                "tradingday": td.strftime("%Y-%m-%d"),
                "product": product,
                "contract_type": ctype,
                "contract": val,
            })

    for fp in files:
        rows = out_by_file[fp]
        out_df = pd.DataFrame(rows).sort_values(
            ["tradingday", "product", "contract_type"],
        ).reset_index(drop=True)
        if not dry_run:
            if backup_dir:
                backup_dir.mkdir(parents=True, exist_ok=True)
                dest = backup_dir / fp.name
                if not dest.exists():
                    shutil.copy2(fp, dest)
            out_df.to_csv(fp, index=False)

    return pd.DataFrame([a.__dict__ for a in audits])


def audit_dom_backward(all_files: list[Path]) -> pd.DataFrame:
    rows = []
    state_prev: dict[str, tuple[int, str]] = {}
    for path in sorted(all_files):
        file_year = int(path.stem.split("-")[-1])
        df = pd.read_csv(path)
        for product, g in df[df.contract_type == "主力"].groupby("product"):
            g = g.sort_values("tradingday")
            prev_k, prev_c = state_prev.get(product, (None, None))
            for r in g.itertuples():
                k = contract_yyyymm(r.contract, file_year)
                if k is None:
                    continue
                if prev_k is not None and k < prev_k:
                    rows.append({
                        "file": path.name,
                        "product": product,
                        "tradingday": r.tradingday,
                        "主力": r.contract,
                        "prev_主力": prev_c,
                        "dom_k": k,
                        "prev_k": prev_k,
                    })
                if prev_k is None or k >= prev_k:
                    prev_k, prev_c = k, r.contract
            state_prev[product] = (prev_k, prev_c)
    return pd.DataFrame(rows)


def audit_sub_backward(all_files: list[Path]) -> pd.DataFrame:
    """次主力相对主力的回退（同日 sub<=dom）。"""
    rows = []
    for path in all_files:
        file_year = int(path.stem.split("-")[-1])
        df = pd.read_csv(path)
        for (td, product), g in df.groupby(["tradingday", "product"]):
            piv = g.set_index("contract_type")["contract"]
            dk = contract_yyyymm(piv.get("主力"), file_year)
            sk = contract_yyyymm(piv.get("次主力"), file_year)
            if dk and sk and sk <= dk:
                rows.append({
                    "file": path.name,
                    "product": product,
                    "tradingday": td,
                    "主力": piv.get("主力"),
                    "次主力": piv.get("次主力"),
                })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="清洗主力合约表")
    ap.add_argument(
        "--data-dir",
        default="/Volumes/JR/数据文档/商品期货-主力合约",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    files = sorted(data_dir.glob("主力合约表-20*.csv"))
    if not files:
        raise SystemExit(f"未找到 CSV: {data_dir}")

    backup_dir = None if args.no_backup or args.dry_run else data_dir / "backup_before_clean"
    report_dir = _REPO / "output" / "dominant_contract_clean"
    report_dir.mkdir(parents=True, exist_ok=True)

    audit_all = clean_all_files(files, dry_run=args.dry_run, backup_dir=backup_dir)
    audit_all.to_csv(report_dir / "fix_log.csv", index=False)

    dom_back = audit_dom_backward(files)
    sub_bad = audit_sub_backward(files)
    for name, df in (("dom_backward_audit", dom_back), ("sub_backward_audit", sub_bad)):
        path = report_dir / f"{name}.csv"
        if len(df):
            df.to_csv(path, index=False)
        elif path.is_file():
            path.unlink()

    print(f"fix rows logged: {len(audit_all)}")
    print(f"remaining sub<=dom: {len(sub_bad)}")
    print(f"remaining dom backward (cross-year): {len(dom_back)}")
    print(f"reports: {report_dir}")
    if backup_dir and not args.dry_run:
        print(f"backup (first run only): {backup_dir}")


if __name__ == "__main__":
    main()
