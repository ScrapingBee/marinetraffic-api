"""MarineTraffic API client built on ScrapingBee, public pages only.

Two kinds of public URL are read, both allowed for generic crawlers by
marinetraffic.com/robots.txt:

  /vessel_sitemap_index.xml and its 47 gzip files   every vessel page URL (1 credit each)
  /en/ais/details/ships/shipid:<id>/...             one vessel page, rendered (75 credits)

The vessel page is a React app. Without JavaScript it is a 3 KB shell. With
JavaScript on the classic tier the app mounts but its sections stay empty.
Premium rendering worked at first and then landed on /not-found every time,
so the default is stealth_proxy=true (75 credits). The page fills its
sections a few seconds after load, which is why the request waits for a cell in
the "Latest AIS information" table before ScrapingBee returns.

Fields a logged out visitor sees as "Upgrade to unlock" (latitude/longitude,
year built, port of registry and more) are returned as None and listed in
`locked`. Nothing here logs in, replays the site's XHR calls or touches the
live map.

Usage:
    export SCRAPINGBEE_API_KEY=...
    python marinetraffic_client.py index --out vessels.csv          # about 48 credits
    python marinetraffic_client.py find --index vessels.csv --imo 9996991
    python marinetraffic_client.py vessel --imo 9996991 --index vessels.csv --json eugenie.json
    python marinetraffic_client.py vessel --url "https://www.marinetraffic.com/en/ais/details/ships/shipid:10021654"
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Iterator
from urllib.parse import unquote

import requests

API = "https://app.scrapingbee.com/api/v1/"
SITE = "https://www.marinetraffic.com"
LOCKED_TEXT = "Upgrade to unlock"

# Vessel pages need a rendered browser. Stealth (75 credits) is the default
# because it rendered every vessel in testing; premium (25 credits) rendered
# the first few and then sent every page to /not-found. See the README table.
TIERS = {
    "stealth": {"render_js": "true", "stealth_proxy": "true", "country_code": "us"},
    "premium": {"render_js": "true", "premium_proxy": "true", "country_code": "us"},
}

# Wait until the AIS table has at least one filled cell, then give the
# voyage block a moment. strict=false so a missing section does not fail the call:
# the parser checks what came back instead.
VESSEL_SCENARIO = {
    "strict": False,
    "instructions": [
        {"wait_for": "#vesselDetails_aisInfoSection [role=cell]"},
        {"wait": 1500},
    ],
}

# The vessel page lays out its data as ARIA tables: div[role=row] holding one
# div[role=rowheader] (the label) and one div[role=cell] (the value).
VESSEL_RULES = {
    "title": "h1",
    "jsonld": {"selector": "script[type='application/ld+json']", "type": "list", "output": "html"},
    "general": {
        "selector": "#vesselDetails_generalSection [role=row]",
        "type": "list",
        "output": {"label": "[role=rowheader]", "value": "[role=cell]"},
    },
    "ais": {
        "selector": "#vesselDetails_aisInfoSection [role=row]",
        "type": "list",
        "output": {"label": "[role=rowheader]", "value": "[role=cell]"},
    },
    "voyage_text": {"selector": "#vesselDetails_voyageSection p", "type": "list"},
    "voyage_ports": {
        "selector": "#vesselDetails_voyageSection a[href*='/ais/details/ports/']",
        "type": "list",
        "output": {"code": "a", "href": "a@href"},
    },
    "progress": "#vesselDetails_voyageSection [aria-label='Voyage progress bar']@aria-valuenow",
    "summary": "#vesselDetails_summarySection",
}

# Each <url> holds the English <loc>, 13 hreflang alternates, then <lastmod>.
SITEMAP_LOC = re.compile(
    r"<loc>https://www\.marinetraffic\.com/en/ais/details/ships/"
    r"shipid:(\d+)/mmsi:(-?\d+)/imo:(\d+)/vessel:([^<]*)</loc>"
)
SITEMAP_LASTMOD = re.compile(r"<lastmod>([^<]*)</lastmod>")


class ScrapeError(RuntimeError):
    pass


# --------------------------------------------------------------------------- data


@dataclass
class IndexEntry:
    shipid: str
    mmsi: str
    imo: str
    name: str
    lastmod: str
    url: str


@dataclass
class PortRef:
    label: str          # "Departure from Singapore"
    code: str           # "SG SIN"
    port_id: str | None  # "290", from /en/ais/details/ports/290


@dataclass
class Voyage:
    departure: PortRef | None = None
    arrival: PortRef | None = None
    progress_pct: int | None = None
    times: dict[str, str] = field(default_factory=dict)  # "Actual time of departure" -> "2026-11-08 00:50 (UTC+8)"


@dataclass
class Vessel:
    url: str
    fetched_at: str
    name: str | None = None
    imo: str | None = None
    mmsi: str | None = None
    call_sign: str | None = None
    flag: str | None = None
    ais_class: str | None = None
    general_type: str | None = None
    detailed_type: str | None = None
    nav_status: str | None = None
    position_received: str | None = None
    local_time: str | None = None
    area: str | None = None
    speed_kn: float | None = None
    course_deg: float | None = None
    heading_deg: float | None = None
    rate_of_turn: str | None = None
    draught_m: float | None = None
    reported_destination: str | None = None
    matched_destination: str | None = None
    reported_eta: str | None = None
    ais_source: str | None = None
    voyage: Voyage = field(default_factory=Voyage)
    locked: list[str] = field(default_factory=list)
    jsonld_checked: bool = False
    credits: int = 0

    def flat(self) -> dict:
        """One CSV-friendly row: voyage fields flattened, lists joined."""
        row = asdict(self)
        v = row.pop("voyage")
        row["departure"] = (v["departure"] or {}).get("code")
        row["arrival"] = (v["arrival"] or {}).get("code")
        row["departure_port_id"] = (v["departure"] or {}).get("port_id")
        row["arrival_port_id"] = (v["arrival"] or {}).get("port_id")
        row["progress_pct"] = v["progress_pct"]
        row["voyage_times"] = "; ".join(f"{k}={val}" for k, val in v["times"].items())
        row["locked"] = "|".join(row["locked"])
        return row


# ------------------------------------------------------------------------- client


class MarineTrafficClient:
    def __init__(self, api_key: str | None = None, tier: str = "stealth", retries_500: int = 4,
                 retries_empty: int = 1, timeout: int = 150) -> None:
        self.api_key = api_key or os.environ.get("SCRAPINGBEE_API_KEY")
        if not self.api_key:
            raise SystemExit("Set SCRAPINGBEE_API_KEY first.")
        self.tier = TIERS[tier]
        self.retries_500 = retries_500
        self.retries_empty = retries_empty
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        self.credits_used = 0

    def _get(self, url: str, params: dict) -> requests.Response:
        """One ScrapingBee call. HTTP 500 means ScrapingBee could not get the
        page; those are not billed, so they are retried with a backoff."""
        query = {"url": url, **params}
        for attempt in range(self.retries_500 + 1):
            resp = self.session.get(API, params=query, timeout=self.timeout)
            if resp.status_code == 500 and attempt < self.retries_500:
                time.sleep(2 * (attempt + 1))
                continue
            break
        cost = int(resp.headers.get("spb-cost", "0") or 0)
        self.credits_used += cost
        if resp.status_code != 200:
            raise ScrapeError(f"HTTP {resp.status_code} for {url}: {resp.text[:200]}")
        return resp

    # ------------------------------------------------------------- sitemaps

    def sitemap_files(self) -> list[str]:
        resp = self._get(f"{SITE}/vessel_sitemap_index.xml", {"render_js": "false"})
        files = re.findall(r"<loc>([^<]+\.xml\.gz)</loc>", resp.text)
        # numeric order, so a partial run is easy to resume
        return sorted(files, key=lambda u: int(re.search(r"_(\d+)\.xml", u).group(1)))

    def sitemap_entries(self, sitemap_url: str) -> list[IndexEntry]:
        resp = self._get(sitemap_url, {"render_js": "false"})
        body = resp.content
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        text = body.decode("utf-8", "replace")
        out = []
        for chunk in text.split("<url>")[1:]:
            loc = SITEMAP_LOC.search(chunk)
            if not loc:
                continue
            shipid, mmsi, imo, slug = loc.groups()
            mod = SITEMAP_LASTMOD.search(chunk)
            lastmod = mod.group(1) if mod else ""
            out.append(IndexEntry(
                shipid=shipid, mmsi=mmsi, imo=imo,
                name=unquote(slug).replace("_", " ").strip(),
                lastmod=lastmod or "",
                url=f"{SITE}/en/ais/details/ships/shipid:{shipid}/mmsi:{mmsi}/imo:{imo}/vessel:{slug}",
            ))
        return out

    def build_index(self, limit_files: int | None = None, workers: int = 4) -> Iterator[tuple[str, list[IndexEntry]]]:
        """The sitemap files act as the pagination of the vessel list:
        one gzip file per page, about 21,000 vessels each."""
        files = self.sitemap_files()[:limit_files] if limit_files else self.sitemap_files()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for url, entries in zip(files, pool.map(self.sitemap_entries, files)):
                yield url, entries

    # ---------------------------------------------------------- vessel page

    def vessel(self, url: str) -> Vessel:
        params = {
            **self.tier,
            "js_scenario": json.dumps(VESSEL_SCENARIO),
            "extract_rules": json.dumps(VESSEL_RULES),
        }
        spent = 0
        for attempt in range(self.retries_empty + 1):
            resp = self._get(url, params)
            spent += int(resp.headers.get("spb-cost", "0") or 0)
            resolved = resp.headers.get("spb-resolved-url", url)
            if "/ais/details/ships/" in resolved:
                data = resp.json()
                if data.get("ais") and data.get("general"):
                    vessel = parse_vessel(data, resolved)
                    vessel.credits = spent
                    return vessel
            # Two billed failure shapes seen in testing, both on URLs that
            # rendered fine minutes earlier: a 200 whose sections are still
            # empty, and a client-side redirect to /not-found. Both are retried.
            last = resolved
        raise ScrapeError(f"{url}: no vessel data after {self.retries_empty + 1} tries (last: {last})")


# ------------------------------------------------------------------------ parsing


def _num(value: str | None) -> float | None:
    if value is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(m.group(0)) if m else None


def _rows(rows: Iterable[dict], locked: list[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for row in rows:
        label = (row.get("label") or "").strip()
        value = (row.get("value") or "").strip()
        if not label:
            continue
        if value == LOCKED_TEXT or not value:
            locked.append(label)
            out[label] = None
        else:
            out[label] = value
    return out


def _port(label: str, link: dict | None) -> PortRef:
    href = (link or {}).get("href") or ""
    m = re.search(r"/ports/(\d+)", href)
    return PortRef(label=label, code=(link or {}).get("code", "").strip(), port_id=m.group(1) if m else None)


def parse_voyage(texts: list[str], links: list[dict], progress: str | None) -> Voyage:
    voyage = Voyage(progress_pct=int(progress) if progress and progress.isdigit() else None)
    port_labels = [t for t in texts if t.startswith(("Departure from", "Arrival at"))]
    for i, label in enumerate(port_labels):
        ref = _port(label, links[i] if i < len(links) else None)
        if label.startswith("Departure"):
            voyage.departure = ref
        else:
            voyage.arrival = ref
    # "Actual time of departure:" is followed by its value in the next <p>
    for i, text in enumerate(texts[:-1]):
        if text.endswith(":"):
            voyage.times[text.rstrip(":")] = texts[i + 1]
    return voyage


def check_jsonld(blocks: list[str], imo: str | None, mmsi: str | None) -> bool:
    """JSON-LD can describe the wrong entity, so compare its identifiers
    with the ones in the General table before trusting the page."""
    for block in blocks or []:
        raw = re.sub(r"^<script[^>]*>|</script>$", "", block.strip())
        try:
            doc = json.loads(raw)
        except ValueError:
            continue
        if doc.get("@type") != "Thing":
            continue
        ids = {p.get("propertyID"): p.get("value") for p in doc.get("identifier", [])}
        return (not imo or ids.get("IMO") == imo) and (not mmsi or ids.get("MMSI") == mmsi)
    return False


def parse_vessel(data: dict, url: str) -> Vessel:
    locked: list[str] = []
    general = _rows(data.get("general", []), locked)
    ais = _rows(data.get("ais", []), locked)
    if data.get("voyage_text") and "Locked content" in data["voyage_text"]:
        locked.append("Current voyage: past track and route forecast")
    area = None
    m = re.search(r"currently located in the\s+(.+?)\s+\(reported", data.get("summary") or "")
    if m:
        area = m.group(1).strip()
    imo = general.get("IMO")
    mmsi = general.get("MMSI")
    return Vessel(
        url=url,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        name=general.get("Name") or data.get("title"),
        imo=imo if imo and imo != "0" else None,
        mmsi=mmsi,
        call_sign=general.get("Call sign"),
        flag=general.get("Flag"),
        ais_class=general.get("AIS transponder class"),
        general_type=general.get("General vessel type"),
        detailed_type=general.get("Detailed vessel type"),
        nav_status=ais.get("Navigational status"),
        position_received=ais.get("Position received"),
        local_time=ais.get("Vessel's local time"),
        area=area,
        speed_kn=_num(ais.get("Speed")),
        course_deg=_num(ais.get("Course")),
        heading_deg=_num(ais.get("True heading")),
        rate_of_turn=ais.get("Rate of turn"),
        draught_m=_num(ais.get("Draught")),
        reported_destination=ais.get("Reported destination"),
        matched_destination=ais.get("Matched destination"),
        reported_eta=ais.get("Reported ETA"),
        ais_source=ais.get("AIS source"),
        voyage=parse_voyage(data.get("voyage_text") or [], data.get("voyage_ports") or [], data.get("progress")),
        locked=locked,
        jsonld_checked=check_jsonld(data.get("jsonld") or [], imo, mmsi),
    )


# ------------------------------------------------------------------------- export


def write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    keys = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def read_index(path: str) -> list[IndexEntry]:
    with open(path, newline="", encoding="utf-8") as fh:
        return [IndexEntry(**row) for row in csv.DictReader(fh)]


def lookup(index: list[IndexEntry], imo: str | None = None, mmsi: str | None = None,
           name: str | None = None) -> list[IndexEntry]:
    hits = index
    if imo:
        hits = [e for e in hits if e.imo == imo]
    if mmsi:
        hits = [e for e in hits if e.mmsi == mmsi]
    if name:
        needle = name.upper()
        hits = [e for e in hits if needle in e.name.upper()]
    return hits


# ---------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Public MarineTraffic vessel data through ScrapingBee")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="build a local vessel index from the public sitemaps")
    p_index.add_argument("--out", default="vessels.csv")
    p_index.add_argument("--files", type=int, help="only the first N sitemap files (each about 21,000 vessels)")
    p_index.add_argument("--workers", type=int, default=4)

    p_find = sub.add_parser("find", help="look a vessel up in the local index (no credits)")
    p_find.add_argument("--index", default="vessels.csv")
    p_find.add_argument("--imo")
    p_find.add_argument("--mmsi")
    p_find.add_argument("--name")

    p_vessel = sub.add_parser("vessel", help="render one public vessel page (75 credits on stealth)")
    p_vessel.add_argument("--tier", choices=sorted(TIERS), default="stealth")
    p_vessel.add_argument("--url")
    p_vessel.add_argument("--imo")
    p_vessel.add_argument("--mmsi")
    p_vessel.add_argument("--index", default="vessels.csv")
    p_vessel.add_argument("--json", help="write the record to this JSON file")
    p_vessel.add_argument("--csv", help="write a one-row CSV")

    args = ap.parse_args(argv)

    if args.cmd == "find":
        hits = lookup(read_index(args.index), args.imo, args.mmsi, args.name)
        for e in hits[:50]:
            print(f"{e.shipid:>9}  imo {e.imo:>7}  mmsi {e.mmsi:>10}  {e.name}  {e.url}")
        print(f"{len(hits)} match(es)", file=sys.stderr)
        return 0 if hits else 1

    client = MarineTrafficClient(tier=getattr(args, "tier", "stealth"))

    if args.cmd == "index":
        total, rows = 0, []
        for url, entries in client.build_index(args.files, args.workers):
            total += len(entries)
            rows.extend(asdict(e) for e in entries)
            print(f"{url.rsplit('/', 1)[-1]}: {len(entries)} vessels", file=sys.stderr)
        # the same vessel can sit in more than one file; keep one row per shipid
        unique = {r["shipid"]: r for r in rows}
        write_csv(args.out, list(unique.values()))
        print(f"{total} entries, {len(unique)} unique ships -> {args.out} "
              f"({client.credits_used} credits)", file=sys.stderr)
        return 0

    url = args.url
    if not url:
        if not (args.imo or args.mmsi):
            ap.error("vessel needs --url, --imo or --mmsi")
        hits = lookup(read_index(args.index), args.imo, args.mmsi)
        if not hits:
            print("not in the index; rebuild it or pass --url", file=sys.stderr)
            return 1
        url = hits[0].url
    vessel = client.vessel(url)
    record = asdict(vessel)
    print(json.dumps(record, indent=2, ensure_ascii=False))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, ensure_ascii=False)
    if args.csv:
        write_csv(args.csv, [vessel.flat()])
    print(f"{client.credits_used} credits", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
