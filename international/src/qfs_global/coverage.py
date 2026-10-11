"""Evidence inventory for original contracts; never infer history from a product list."""

from collections import defaultdict
from dataclasses import asdict, replace
from datetime import date
from itertools import pairwise

from quant_data_kit.derivatives import load_bundle, write_bundle
from quant_data_kit.derivatives.bundle import digest, json_bytes


def merge_bundles(paths, output, *, reconcile_first_observed=False):
    """Merge compatible immutable chunks; conflicting overlaps fail closed."""
    bundles = [load_bundle(p) for p in paths]
    if not bundles or len({b.manifest["evidence_kind"] for b in bundles}) != 1:
        raise ValueError("nonempty chunks with the same evidence kind required")
    contracts, quotes, providers = {}, {}, {}
    modes = {"analysis", "replay"}
    for bundle in bundles:
        modes &= set(bundle.allowed_modes)
        for c in bundle.contracts:
            if c.kind != "future":
                raise ValueError("future-only history required")
            if c.instrument_id in contracts and asdict(c) != asdict(contracts[c.instrument_id]):
                previous = contracts[c.instrument_id]
                fields = lambda x: {
                    k: v for k, v in asdict(x).items() if k not in {"listed_at", "known_at"}
                }
                if not (
                    reconcile_first_observed
                    and bundle.manifest["provider"] in {"nse-fo", "cboe-vix"}
                    and providers[c.instrument_id] == bundle.manifest["provider"]
                    and previous.known_at == previous.listed_at
                    and c.known_at == c.listed_at
                    and fields(previous) == fields(c)
                ):
                    raise ValueError("conflicting contract rules; split rule versions")
                first = min(previous.listed_at, c.listed_at)
                c = replace(c, listed_at=first, known_at=first)
            contracts[c.instrument_id] = c
            providers[c.instrument_id] = bundle.manifest["provider"]
        for q in bundle.quotes:
            key = (q.instrument_id, q.at)
            if key in quotes and q != quotes[key]:
                raise ValueError("conflicting overlapping quote")
            quotes[key] = q
    if not modes:
        raise ValueError("chunks have no common allowed research mode")
    for b in bundles:
        b.verify_unchanged()
    return write_bundle(
        output,
        sorted(contracts.values(), key=lambda c: c.instrument_id),
        sorted(quotes.values(), key=lambda q: (q.at, q.instrument_id)),
        provider="merged-contract-history",
        evidence_kind=bundles[0].manifest["evidence_kind"],
        rights_note="Source rights remain applicable: "
        + " | ".join(dict.fromkeys(b.manifest["rights_note"] for b in bundles)),
        limits="Merged unadjusted contract observations; no missing sessions, settlements or margins inferred. "
        + " | ".join(dict.fromkeys(b.manifest["limits"] for b in bundles)),
        source={
            "allowed_modes": sorted(modes),
            "lifecycle_reconciliation": "earliest observed lifecycle of identical free-source rules, NOT actual listing/PIT evidence"
            if reconcile_first_observed
            else "strict identical contract rules",
            "chunks": [
                {
                    "input_sha256": b.identity,
                    "provider": b.manifest["provider"],
                    "source": b.manifest.get("source", {}),
                }
                for b in bundles
            ],
        },
    )


def coverage_report(bundles, requirements):
    """Audit against explicitly declared exchange sessions and research thresholds.

    A pass is structural coverage, not exchange licensing, PIT certification,
    a statement of liquidity, or proof of a successful strategy replay.
    """
    if not requirements or not isinstance(requirements, list):
        raise ValueError("nonempty requirements list required")
    all_contracts, all_quotes = {}, defaultdict(dict)
    identities = sorted(b.identity for b in bundles)
    evidence = defaultdict(set)
    restrictions = defaultdict(set)
    for b in bundles:
        for c in b.contracts:
            if c.kind != "future":
                raise ValueError("futures inventory cannot contain options")
            key = (c.venue, c.instrument_id)
            if key in all_contracts and all_contracts[key] != c:
                raise ValueError("conflicting contract definitions")
            all_contracts[key] = c
        lookup = {c.instrument_id: c for c in b.contracts}
        for q in b.quotes:
            c = lookup[q.instrument_id]
            key, obs = (c.venue, c.product), (c.instrument_id, q.at)
            if obs in all_quotes[key] and all_quotes[key][obs] != q:
                raise ValueError("conflicting history overlap")
            all_quotes[key][obs] = q
            evidence[key].add(b.manifest["evidence_kind"])
            if "replay" not in b.allowed_modes:
                restrictions[key].add("source_disallows_replay")
    rows, seen = [], set()
    for req in requirements:
        required = {
            "venue",
            "product",
            "expected_sessions",
            "calendar_source",
            "min_sessions",
            "min_contracts",
            "min_roll_overlaps",
            "min_overlap_sessions",
        }
        if not isinstance(req, dict) or set(req) != required:
            raise ValueError(
                "coverage requirements must declare venue/product, calendar, sessions and four thresholds"
            )
        key = (req["venue"], req["product"])
        if key in seen:
            raise ValueError("duplicate product coverage requirement")
        seen.add(key)
        sessions = req["expected_sessions"]
        if not sessions or sessions != sorted(set(sessions)) or not req["calendar_source"]:
            raise ValueError(
                "explicit sorted unique exchange sessions and calendar_source required"
            )
        for s in sessions:
            if date.fromisoformat(s).isoformat() != s:
                raise ValueError("sessions must be ISO dates")
        for name in ("min_sessions", "min_contracts", "min_roll_overlaps", "min_overlap_sessions"):
            n = req[name]
            if (
                isinstance(n, bool)
                or not isinstance(n, int)
                or n < (0 if name == "min_roll_overlaps" else 1)
            ):
                raise ValueError("invalid coverage threshold")
        expected = set(sessions)
        quotes = [q for q in all_quotes[key].values() if q.session in expected]
        observed = {q.session for q in quotes}
        contract_sessions = defaultdict(set)
        for q in quotes:
            if q.volume > 0 and q.settlement is not None:
                contract_sessions[q.instrument_id].add(q.session)
        ids = {q.instrument_id for q in quotes}
        contracts = sorted(
            (
                c
                for c in all_contracts.values()
                if (c.venue, c.product) == key and c.instrument_id in ids
            ),
            key=lambda c: (c.expiry, c.instrument_id),
        )
        overlaps = sum(
            len(contract_sessions[a.instrument_id] & contract_sessions[b.instrument_id])
            >= req["min_overlap_sessions"]
            for a, b in pairwise(contracts)
            if a.expiry < b.expiry
        )
        missing = sorted(expected - observed)
        reasons = []
        if evidence[key] != {"retrospective"}:
            reasons.append("no_exclusively_real_retrospective_evidence")
        if missing:
            reasons.append("missing_expected_sessions")
        if len(observed) < req["min_sessions"]:
            reasons.append("insufficient_sessions")
        if len(contracts) < req["min_contracts"]:
            reasons.append("insufficient_contracts")
        if overlaps < req["min_roll_overlaps"]:
            reasons.append("insufficient_roll_overlaps")
        missing_settlement = sum(q.settlement is None for q in quotes)
        nonpositive = sum(
            min(q.open, q.low, q.close) <= 0 or (q.settlement is not None and q.settlement <= 0)
            for q in quotes
        )
        blockers = sorted(restrictions[key])
        if missing_settlement:
            blockers.append("missing_settlement")
        if nonpositive:
            blockers.append("nonpositive_price_unsupported_by_account")
        if any(c.initial_margin is None for c in contracts):
            blockers.append("missing_margin")
        if any(c.settlement == "physical" for c in contracts):
            blockers.append("delivery_deadlines_must_be_checked_before_replay")
        rows.append(
            {
                "venue": key[0],
                "product": key[1],
                "status": "gap" if reasons else "structural_coverage_pass",
                "sessions": len(observed),
                "contracts": len(contracts),
                "roll_overlaps": overlaps,
                "first_session": min(observed) if observed else None,
                "last_session": max(observed) if observed else None,
                "missing_sessions": missing,
                "missing_settlement_rows": missing_settlement,
                "nonpositive_rows": nonpositive,
                "coverage_gaps": reasons,
                "replay_checks": blockers,
                "calendar_source": req["calendar_source"],
                "evidence_kind": sorted(evidence[key]),
            }
        )
    for b in bundles:
        b.verify_unchanged()
    return {
        "schema": "qfs.coverage/v1",
        "products": rows,
        "input_sha256": digest(json_bytes({"bundles": identities, "requirements": requirements})),
        "input_bundles": identities,
        "limits": "Declared calendar and thresholds; product-level sessions do not prove a complete chain on every date. Roll overlaps are candidate observations, not validated executions. A structural pass is not replay readiness, PIT certification or licensing verification.",
    }
