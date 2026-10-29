"""Print a batch report for one run: disclosure breakdown, Haiku vs Sonnet
hit rates, total cost, five miss transcripts, and the top 20 most-named
sub-processors so far.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import config  # noqa: E402
from entities import canonicalize, canonicalize_seed  # noqa: E402


CLASSES = ["named_list", "categories_only", "trust_portal", "blocked", "nothing_found"]
MISS_CLASSES = {"categories_only", "trust_portal", "blocked", "nothing_found"}


def load_latest_transcripts(run_ids: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    """Union transcripts across runs. For each canonical seed, keep the latest run's
    transcript. Returns (seed_canonical -> transcript, seed_canonical -> run_id).
    """
    latest: dict[str, dict] = {}
    source_run: dict[str, str] = {}
    for run_id in run_ids:  # chronological order
        tdir = config.TRANSCRIPTS_DIR / run_id
        if not tdir.exists():
            continue
        for p in sorted(tdir.glob("*.json")):
            d = json.loads(p.read_text())
            canon = canonicalize_seed(d["company"])
            latest[canon] = d
            source_run[canon] = run_id
    return latest, source_run


def _normalize_seed(name: str) -> str:
    """Normalize a seed name the same way everywhere: canonicalize_seed (dual-class
    collapse) then canonicalize (alias + legal-suffix strip) so "Broadcom Inc.",
    "Broadcom", and "Broadcom, Inc." all collide to one canonical.
    """
    return canonicalize(canonicalize_seed(name))


def load_seed_set(csv_path: Path) -> set[str]:
    out: set[str] = set()
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            out.add(_normalize_seed(row["company_name"].strip()))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-ids", required=True, help="Comma-separated run IDs in chronological order.")
    ap.add_argument(
        "--sets",
        help="Comma-separated label=csv pairs to group results by seed set, e.g. "
        "'nasdaq100=data/nasdaq100_seeds.csv,cloud100_2025=data/cloud100_2025_seeds.csv'. "
        "Omit for a single ungrouped report.",
    )
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--misses", type=int, default=5)
    args = ap.parse_args()

    run_ids = [r.strip() for r in args.run_ids.split(",") if r.strip()]
    total_cost = 0.0
    for run_id in run_ids:
        sp = config.RUNS_DIR / run_id / "state.json"
        if not sp.exists():
            raise SystemExit(f"no state.json at {sp}")
        total_cost += json.loads(sp.read_text()).get("total_cost_usd", 0.0)
    latest, source_run = load_latest_transcripts(run_ids)
    transcripts = list(latest.values())
    state = {"total_cost_usd": total_cost}

    # Build seed-set membership (company canonical name -> set label).
    set_defs: list[tuple[str, set[str]]] = []
    if args.sets:
        for pair in args.sets.split(","):
            label, csv_rel = pair.split("=", 1)
            set_defs.append((label.strip(), load_seed_set(Path(csv_rel.strip()))))
    else:
        set_defs.append(("all", {_normalize_seed(t["company"]) for t in transcripts}))

    # Per-set grouping. For each seed set, group its member transcripts by class.
    by_set: dict[str, dict[str, list[str]]] = {label: defaultdict(list) for label, _ in set_defs}
    for t in transcripts:
        canon = _normalize_seed(t["company"])
        cls = (t["final"].get("report") or {}).get("disclosure_class") or "nothing_found"
        for label, members in set_defs:
            if canon in members:
                by_set[label][cls].append(canon)
    # Keep a flattened view for the Haiku/Sonnet accounting + top-20 (across sets).
    by_class: dict[str, list[str]] = defaultdict(list)
    for label_buckets in by_set.values():
        for cls, names in label_buckets.items():
            by_class[cls].extend(names)

    # Haiku vs Sonnet accounting.
    haiku_tried = sonnet_tried = 0
    haiku_hits = sonnet_rescues = 0
    for t in transcripts:
        attempts = t.get("attempts", [])
        if not attempts:
            continue
        haiku = next((a for a in attempts if a["model"] == config.MODEL_HAIKU), None)
        sonnet = next((a for a in attempts if a["model"] == config.MODEL_SONNET), None)
        if haiku:
            haiku_tried += 1
            haiku_ok = _is_hit(haiku.get("report"))
            if haiku_ok:
                haiku_hits += 1
        if sonnet:
            sonnet_tried += 1
            sonnet_ok = _is_hit(sonnet.get("report"))
            if sonnet_ok and (haiku is None or not _is_hit(haiku.get("report"))):
                sonnet_rescues += 1

    # Top sub-processors by distinct seed companies naming them. Count archive-sourced
    # entries too (the list came from Wayback but the companies named these processors).
    seeds_naming: dict[str, set[str]] = defaultdict(set)
    for t in transcripts:
        rep = t["final"].get("report") or {}
        entries = rep.get("entries") or []
        if not entries:
            continue
        seed = _normalize_seed(t["company"])
        for e in entries:
            canon = canonicalize(e.get("name") or "", purpose_text=e.get("purpose") or "")
            if canon:
                seeds_naming[canon].add(seed)
    top = Counter({name: len(seeds) for name, seeds in seeds_naming.items()}).most_common(args.top)

    # Miss transcripts to surface.
    miss_paths = []
    for t in transcripts:
        rep = t["final"].get("report") or {}
        cls = rep.get("disclosure_class") or "nothing_found"
        if cls in MISS_CLASSES:
            canon = _normalize_seed(t["company"])
            run_id = source_run.get(canon, run_ids[-1])
            miss_paths.append((cls, canon, config.TRANSCRIPTS_DIR / run_id / f"{_slug(t['company'])}.json"))
    miss_paths = miss_paths[: args.misses]

    # Render.
    print()
    print("=" * 78)
    print(f" Run report — {', '.join(run_ids)}")
    print("=" * 78)
    print(f" Unique seeds covered: {len(transcripts)}")
    print(f" Total cost (USD)    : ${state['total_cost_usd']:.4f}")
    print(f" Hard cap            : ${config.HARD_SPEND_CAP_USD:.2f}")
    for label, _ in set_defs:
        buckets = by_set[label]
        covered = sum(len(v) for v in buckets.values())
        print()
        print(f"-- Disclosure breakdown — {label}  ({covered} seeds) --")
        for cls in CLASSES:
            names = sorted(buckets.get(cls, []))
            print(f"  {cls}  ({len(names)})")
            for n in names:
                print(f"    - {n}")
    print()
    haiku_rate = (haiku_hits / haiku_tried * 100.0) if haiku_tried else 0.0
    rescue_rate = (sonnet_rescues / sonnet_tried * 100.0) if sonnet_tried else 0.0
    print("-- Haiku vs Sonnet --")
    print(f"  Haiku first-pass hits : {haiku_hits}/{haiku_tried}  ({haiku_rate:.1f}%)")
    print(f"  Sonnet retries        : {sonnet_tried}")
    print(f"  Sonnet rescues        : {sonnet_rescues}  ({rescue_rate:.1f}% of retries)")
    print()
    print(f"-- Top {args.top} most-named sub-processors --")
    for i, (name, n) in enumerate(top, 1):
        print(f"  {i:>2}. {name:40s}  named by {n} lists")
    print()
    print(f"-- {len(miss_paths)} miss transcripts --")
    for cls, company, path in miss_paths:
        print(f"  [{cls}]  {company}")
        print(f"    {path}")
    print()


def _is_hit(report: dict | None) -> bool:
    if report is None:
        return False
    cls = report.get("disclosure_class")
    if cls == "nothing_found":
        return False
    if cls == "named_list" and len(report.get("entries") or []) < 3:
        return False
    return True


def _slug(name: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()


if __name__ == "__main__":
    main()
