"""Export the current dataset to data/graph.json for the three.js prototype.

Shape:
  {
    "nodes": [{"id", "name", "kind"}],
    "edges": [{"from", "to", "purpose_category", "purpose_text",
               "first_seen", "last_seen"}]
  }

Phase 1 is one run, so first_seen == last_seen == the run's started_at date.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import config  # noqa: E402
from entities import canonicalize, canonicalize_seed, classify_purpose, is_meta_affiliate  # noqa: E402

META_AFFILIATES_LABEL = "Meta affiliates"
# The canonical seed name Meta uses after canonicalize(). Checked against
# seed_canon when deciding whether to apply the affiliate collapse.
META_SEED_CANONICALS = {"Meta Platforms", "Meta"}


def node_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load_set_csv(csv_path: Path) -> set[str]:
    """Return the set of seed names in the CSV, pre-normalized with the same
    canonicalize() the exporter applies in ensure_node, so membership checks hit.
    """
    out: set[str] = set()
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            raw = canonicalize_seed(row["company_name"].strip())
            out.add(canonicalize(raw))
    return out


def _load_run_date(run_id: str) -> str:
    sp = config.RUNS_DIR / run_id / "state.json"
    state = json.loads(sp.read_text())
    return (state.get("started_at") or "")[:10] or state.get("started_at", "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-ids", required=True, help="Comma-separated run IDs in chronological order.")
    ap.add_argument(
        "--sets",
        default=(
            "nasdaq100=data/nasdaq100_seeds.csv,"
            "cloud100_2025=data/cloud100_2025_seeds.csv,"
            "sp100=data/sp100_seeds.csv"
        ),
        help="Comma-separated label=csv pairs. Each node gets a seed_sets array listing every set it belongs to.",
    )
    ap.add_argument(
        "--manual-dir",
        default=str(config.DATA_DIR / "manual"),
        help="Directory of manual observation JSON files to ingest (one per company).",
    )
    ap.add_argument(
        "--out",
        default=str(config.ROOT / "web" / "graph.json"),
        help="Where to write graph.json. Defaults to web/graph.json so the three.js prototype picks it up automatically.",
    )
    args = ap.parse_args()

    run_ids = [r.strip() for r in args.run_ids.split(",") if r.strip()]
    run_dates = {rid: _load_run_date(rid) for rid in run_ids}

    # Pick the latest transcript per (canonical seed, product_scope) across runs.
    # Keying by product_scope lets multiple product-level lists coexist for the
    # same company (e.g. Meta's company-wide list AND Meta for Work's product list).
    latest: dict[tuple[str, str], tuple[str, dict]] = {}
    for run_id in run_ids:
        tdir = config.TRANSCRIPTS_DIR / run_id
        if not tdir.exists():
            continue
        for p in sorted(tdir.glob("*.json")):
            d = json.loads(p.read_text())
            canon = canonicalize_seed(d["company"])
            rep = (d.get("final") or {}).get("report") or {}
            scope = (rep.get("product_scope") or "").strip() or None
            latest[(canon, scope or "")] = (run_id, d)

    # Build seed-set membership map.
    set_members: dict[str, set[str]] = {}
    for pair in args.sets.split(","):
        if not pair.strip():
            continue
        label, csv_rel = pair.split("=", 1)
        set_members[label.strip()] = load_set_csv(Path(csv_rel.strip()))

    def sets_for(canon_name: str) -> list[str]:
        return [label for label, members in set_members.items() if canon_name in members]

    # Union of all seed sets — used to decide whether a target node is a seed or sub-processor.
    all_seeds: set[str] = set()
    for members in set_members.values():
        all_seeds |= members

    # Nodes keyed by node_id (slug) so canonical names that differ only in
    # punctuation ("Amazon Bedrock" vs "Amazon (Bedrock)", "Microsoft Azure" vs
    # "Microsoft (Azure)") collide to a single graph node rather than appearing
    # as two entries with the same id field.
    nodes: dict[str, dict] = {}

    def ensure_node(name: str, kind: str) -> str:
        canon = canonicalize(name)
        nid = node_id(canon)
        if nid not in nodes:
            nodes[nid] = {
                "id": nid,
                "name": canon,
                "kind": kind,
                "seed_sets": sets_for(canon),
            }
        else:
            if kind == "seed":
                nodes[nid]["kind"] = "seed"
            # Union seed-set membership idempotently (handles targets later discovered as seeds).
            existing = set(nodes[nid].get("seed_sets") or [])
            nodes[nid]["seed_sets"] = sorted(existing | set(sets_for(canon)))
        return canon

    # Sentinel for categories_only disclosures.
    ensure_node("Unnamed sub-processor", "unnamed")

    edges: list[dict] = []
    edge_seen: dict[tuple[str, str, str], dict] = {}

    for (canon_seed, _scope_key), (run_id, t) in latest.items():
        seed_canon = ensure_node(canon_seed, "seed")
        rep = t["final"].get("report") or {}
        cls = rep.get("disclosure_class")
        entries = rep.get("entries") or []
        product_scope = (rep.get("product_scope") or "").strip() or None
        # Observation date rule: for archive-sourced lists, use the capture date,
        # never today. Otherwise use the run's started_at date.
        if (rep.get("source") == "archive") and rep.get("observation_date"):
            obs_date = rep["observation_date"]
        else:
            obs_date = run_dates.get(run_id, "")

        # Emit edges whenever there are entries, even on blocked+archive runs.
        source_sets = sets_for(seed_canon)
        is_meta_seed = seed_canon in META_SEED_CANONICALS
        if entries:
            for e in entries:
                raw_name = (e.get("name") or "").strip()
                if not raw_name:
                    continue
                purpose_text = (e.get("purpose") or "").strip()
                # When the seed is Meta itself, Meta-affiliate rows go to a shared
                # "Meta affiliates" node instead of being dropped as self-references.
                is_affiliate_row = is_meta_seed and is_meta_affiliate(raw_name)
                if is_affiliate_row:
                    target_canon = ensure_node(META_AFFILIATES_LABEL, "affiliate-group")
                else:
                    cand = canonicalize(raw_name, purpose_text=purpose_text)
                    kind = "seed" if cand in all_seeds else "sub-processor"
                    target_canon = ensure_node(cand, kind)
                category = classify_purpose(purpose_text)
                # Dedup key: for affiliate rows, keep the raw entity name as a
                # discriminator so each Meta subsidiary gets its own edge while
                # still pointing at the shared "Meta affiliates" node.
                detail_key = raw_name if is_affiliate_row else ""
                key = (seed_canon, target_canon, category, product_scope or "", detail_key)
                if key in edge_seen:
                    if len(purpose_text) > len(edge_seen[key]["purpose_text"]):
                        edge_seen[key]["purpose_text"] = purpose_text
                    continue
                edge = {
                    "from": node_id(seed_canon),
                    "to": node_id(target_canon),
                    "purpose_category": category,
                    "purpose_text": purpose_text,
                    "first_seen": obs_date,
                    "last_seen": obs_date,
                    "source": rep.get("source", "live"),
                    "seed_sets": source_sets,
                }
                if is_affiliate_row:
                    edge["to_detail"] = raw_name
                if product_scope:
                    edge["product_scope"] = product_scope
                if rep.get("note"):
                    edge["note"] = rep["note"]
                edges.append(edge)
                edge_seen[key] = edge
        elif cls == "categories_only":
            edge = {
                "from": node_id(seed_canon),
                "to": node_id("Unnamed sub-processor"),
                "purpose_category": "unnamed",
                "purpose_text": "(categories_only disclosure)",
                "first_seen": obs_date,
                "last_seen": obs_date,
                "source": "live",
                "seed_sets": source_sets,
            }
            if product_scope:
                edge["product_scope"] = product_scope
            edges.append(edge)

    # Explicit human-labelled categories supported in manual files, mapped onto
    # our normalized vocabulary. Anything else falls through to classify_purpose().
    MANUAL_CATEGORY_MAP = {
        # Long-form labels (Adobe manual).
        "storage and infrastructure": "hosting",
        "security": "security",
        "ai models": "ai_inference",
        "customer support": "support",
        "product functionality and user experience": "other",
        "communications": "communications",
        "payments": "payments",
        "analytics": "analytics",
        # Short-form labels (Microsoft Online Services PDF extraction).
        "cloud": "hosting",
        "ancillary": "other",
        "ai": "ai_inference",
        "contract_staff": "support",
        # Pass-through for already-normalized labels.
        "hosting": "hosting",
    }

    # Manual observations: one JSON file per company under data/manual/.
    manual_dir = Path(args.manual_dir)
    manual_edges = 0
    if manual_dir.exists():
        for p in sorted(manual_dir.glob("*.json")):
            doc = json.loads(p.read_text())
            seed_raw = (doc.get("company") or "").strip()
            if not seed_raw:
                continue
            seed_canon = ensure_node(canonicalize_seed(seed_raw), "seed")
            # Observation date: prefer explicit observation_date, fall back to fetched_at.
            obs = (doc.get("observation_date") or doc.get("fetched_at") or "").strip()
            # Manual files default to paraphrased=True since hand extraction almost
            # always paraphrases. Explicit false disables the flag.
            doc_paraphrased = doc.get("paraphrased")
            default_paraphrased = True if doc_paraphrased is None else bool(doc_paraphrased)
            source_url = doc.get("source_url")
            source_sets = sets_for(seed_canon)
            for e in doc.get("entries") or []:
                raw_name = (e.get("name") or "").strip()
                if not raw_name:
                    continue
                purpose_text = (e.get("purpose") or "").strip()
                target_canon = canonicalize(raw_name, purpose_text=purpose_text)
                kind = "seed" if target_canon in all_seeds else "sub-processor"
                ensure_node(target_canon, kind)
                raw_cat = (e.get("category") or "").strip().lower()
                category = MANUAL_CATEGORY_MAP.get(raw_cat) or classify_purpose(purpose_text)
                entry_paraphrased = e.get("paraphrased")
                if entry_paraphrased is None:
                    is_paraphrased = default_paraphrased
                else:
                    is_paraphrased = bool(entry_paraphrased)
                edge = {
                    "from": node_id(seed_canon),
                    "to": node_id(target_canon),
                    "purpose_category": category,
                    "purpose_text": purpose_text,
                    "purpose_paraphrased": is_paraphrased,
                    "first_seen": obs,
                    "last_seen": obs,
                    "source": "manual",
                    "seed_sets": source_sets,
                }
                if source_url:
                    edge["source_url"] = source_url
                edges.append(edge)
                manual_edges += 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"nodes": list(nodes.values()), "edges": edges}, indent=2))
    print(f"wrote {out_path} — nodes={len(nodes)} edges={len(edges)} (manual edges: {manual_edges})")


if __name__ == "__main__":
    main()
