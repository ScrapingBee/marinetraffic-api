"""Track a small fleet on public MarineTraffic vessel pages and log what changed.

Run it from cron. Each run renders every vessel in the watchlist once (75
credits per vessel on stealth, more if a render comes back empty and is retried),
compares the result with the previous run, and appends one line per change
to an events CSV: navigational status, matched destination, reported ETA,
the departure and arrival ports of the current voyage, and the sea area.

The watchlist is a text file with one IMO, MMSI or vessel page URL per line.
IMO and MMSI are resolved through the local index that
`marinetraffic_client.py index` builds from the public sitemaps, so no
search endpoint is ever called.

Usage:
    export SCRAPINGBEE_API_KEY=...
    python voyage_tracker.py watchlist.txt --index vessels.csv \
        --state fleet_state.json --events fleet_events.csv --snapshot fleet_now.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from marinetraffic_client import (
    MarineTrafficClient,
    ScrapeError,
    Vessel,
    lookup,
    read_index,
    write_csv,
)

WATCHED = {
    "nav_status": lambda v: v.nav_status,
    "matched_destination": lambda v: v.matched_destination,
    "reported_eta": lambda v: v.reported_eta,
    "departure": lambda v: v.voyage.departure.code if v.voyage.departure else None,
    "arrival": lambda v: v.voyage.arrival.code if v.voyage.arrival else None,
    "area": lambda v: v.area,
}
EVENT_FIELDS = ["seen_at", "imo", "mmsi", "name", "field", "old", "new", "url"]


def resolve(lines: list[str], index_path: str) -> list[str]:
    urls, index = [], None
    for raw in lines:
        item = raw.split("#", 1)[0].strip()
        if not item:
            continue
        if item.startswith("https://www.marinetraffic.com/"):
            urls.append(item)
            continue
        if index is None:
            index = read_index(index_path)
        key = {"imo": item} if len(item) == 7 else {"mmsi": item}
        hits = lookup(index, **key)
        if hits:
            urls.append(hits[0].url)
        else:
            print(f"skip {item}: not in {index_path}", file=sys.stderr)
    return urls


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def diff(prev: dict | None, vessel: Vessel) -> list[dict]:
    now = {k: f(vessel) for k, f in WATCHED.items()}
    if prev is None:
        return [{"field": "first_seen", "old": "", "new": now.get("nav_status") or ""}]
    events = []
    for key, value in now.items():
        old = prev.get(key)
        if value is not None and value != old:
            events.append({"field": key, "old": old or "", "new": value})
    return events


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Log voyage changes for a watchlist of vessels")
    ap.add_argument("watchlist")
    ap.add_argument("--index", default="vessels.csv")
    ap.add_argument("--state", default="fleet_state.json")
    ap.add_argument("--events", default="fleet_events.csv")
    ap.add_argument("--snapshot", help="also write this run's full rows to a CSV")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--tier", choices=["stealth", "premium"], default="stealth")
    ap.add_argument("--max-credits", type=int, default=1000,
                    help="refuse to start if the watchlist could cost more than this")
    args = ap.parse_args(argv)

    with open(args.watchlist, encoding="utf-8") as fh:
        urls = resolve(fh.readlines(), args.index)
    per_render = 75 if args.tier == "stealth" else 25
    worst_case = len(urls) * per_render * 2  # one retry per vessel
    if worst_case > args.max_credits:
        raise SystemExit(f"{len(urls)} vessels could cost {worst_case} credits; raise --max-credits")

    client = MarineTrafficClient(tier=args.tier)
    state = load_state(args.state)
    seen_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results: list[Vessel] = []
    failures = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(client.vessel, u): u for u in urls}
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                results.append(fut.result())
            except ScrapeError as exc:
                failures += 1
                print(f"failed: {exc}", file=sys.stderr)

    new_events = []
    for vessel in sorted(results, key=lambda v: v.name or ""):
        key = vessel.imo or vessel.mmsi or vessel.url
        for event in diff(state.get(key), vessel):
            new_events.append({"seen_at": seen_at, "imo": vessel.imo or "", "mmsi": vessel.mmsi or "",
                               "name": vessel.name or "", "url": vessel.url, **event})
        state[key] = {k: f(vessel) for k, f in WATCHED.items()}
        state[key]["checked_at"] = seen_at

    write_header = not os.path.exists(args.events)
    with open(args.events, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=EVENT_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(new_events)
    with open(args.state, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
    if args.snapshot:
        write_csv(args.snapshot, [v.flat() for v in results])

    for e in new_events:
        print(f"{e['name']:<22} {e['field']:<20} {e['old'] or '-'} -> {e['new']}")
    print(f"{len(results)} vessels read, {failures} failed, {len(new_events)} events, "
          f"{client.credits_used} credits", file=sys.stderr)
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
