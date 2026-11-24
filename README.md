# MarineTraffic API

<p align="center">
  <a href="https://www.scrapingbee.com/">
    <img src="REPLACE_WITH_SCREENSHOT_URL" alt="marinetraffic-api" />
  </a>
</p>

[![checks](https://github.com/ScrapingBee/marinetraffic-api/workflows/checks/badge.svg)](https://github.com/ScrapingBee/marinetraffic-api/actions)
[![license](https://img.shields.io/github/license/ScrapingBee/marinetraffic-api.svg)](LICENSE)

This repo is a do-it-yourself [MarineTraffic API](https://www.scrapingbee.com/scrapers/marinetraffic-api/) for the part of marinetraffic.com that anyone can open without an account: the vessel page. Give it an IMO number and it returns the ship's flag, type, navigational status, speed, draught, destination, reported ETA, the departure and arrival ports of the current voyage and the sea area it is in, as typed Python records, CSV or JSON. A second script runs from cron and writes one CSV line every time one of those fields changes for a ship you follow.

What it does not return is latitude and longitude. MarineTraffic shows a logged out visitor "Upgrade to unlock" in that cell, and the code respects that. If you need coordinates, the official API (covered further down) is the product for it.

Everything here goes through [ScrapingBee](https://www.scrapingbee.com/) and was measured on 2026-11-24.

## Files

| File | What it does |
|---|---|
| [`examples/marinetraffic_client.py`](examples/marinetraffic_client.py) | Client module and CLI: builds a vessel index from the public sitemaps, looks ships up offline, renders one vessel page into a `Vessel` dataclass, exports JSON or CSV |
| [`examples/voyage_tracker.py`](examples/voyage_tracker.py) | Cron job for a watchlist: renders each ship, diffs against the last run, appends change events to a CSV |
| [`examples/vessel_status.js`](examples/vessel_status.js) | Node 18 version of the vessel read, one JSON line per ship |

Python needs `requests`. Node needs nothing beyond version 18.

## What robots.txt allows

The file has a `User-agent: *` group that is short and permissive, followed by a much stricter group aimed at named SEO and AI crawlers (AhrefsBot, Amazonbot, Bytespider and others). The lines that matter here:

```
User-agent: *
Allow: /
Disallow: /mob/
Disallow: /users/
Disallow: /wiki/
Disallow: /sog/
...
Sitemap: https://www.marinetraffic.com/sitemap.xml
```

and, from the named-crawler group, a comment that explains the site's intent:

```
# SEO and AI crawlers: HTML pages only, no app data/XHR endpoints, slow down on pages.
...
Disallow: /getData/
Disallow: /*/vessels/
Disallow: /bff/
Disallow: /*/search/searchAsset
Disallow: /api/
```

This repo follows the stricter reading even though a generic client falls under `*`. It requests two kinds of URL only: the sitemap files listed in `sitemap.xml`, and vessel pages under `/en/ais/details/ships/`. It never calls `/getData/`, `/bff/`, the search endpoint, the map tiles or any other XHR path directly. The headless browser loads the vessel page the way a visitor's browser does, and the code reads the DOM it ends up with. There is no login, no cookie reuse and no replayed request. Scraping behind a login is against ScrapingBee's terms of service, and on this site it would also be pointless for public data, because the logged out page already shows what is public.

## Ship tracking API fields on the public vessel page

This is the field ledger for one ship, CMA CGM EUGENIE, as a logged out visitor sees it. "Locked" means the page renders the label with "Upgrade to unlock" in place of the value.

| Field | Section id on the page | Example value | Public? |
|---|---|---|---|
| Name, flag, IMO, MMSI, call sign | `vesselDetails_generalSection` | CMA CGM EUGENIE, Malta, 9996991, 249920000, 9HA6435 | yes |
| AIS transponder class | `vesselDetails_generalSection` | Class A | yes |
| General and detailed vessel type | `vesselDetails_generalSection` | Cargo - Hazard D (Recognizable), Container Ship | yes |
| Service status, port of registry, year built | `vesselDetails_generalSection` | Upgrade to unlock | locked |
| Navigational status | `vesselDetails_aisInfoSection` | At Anchor | yes |
| Position received, vessel's local time | `vesselDetails_aisInfoSection` | 5 mins ago, 2026-11-24 15:26 (UTC+2) | yes |
| Latitude/Longitude | `vesselDetails_aisInfoSection` | Upgrade to unlock | locked |
| Speed, course, true heading, rate of turn | `vesselDetails_aisInfoSection` | 0 kn, 113 °, 286 °, 0 °/min | yes |
| Draught | `vesselDetails_aisInfoSection` | 16 m | yes |
| Reported and matched destination | `vesselDetails_aisInfoSection` | EGZZ1>EGALY, Alexandria Anch, EGYPT | yes |
| Reported ETA, AIS source | `vesselDetails_aisInfoSection` | 2026-11-21 09:00 (UTC+2), Roaming | yes |
| Departure and arrival port, ATD, ATA | `vesselDetails_voyageSection` | Singapore to Alexandria Anch, 2026-11-08 00:50 (UTC+8) | yes |
| Past track, route forecast | `vesselDetails_voyageSection` | Locked content | locked |
| Sea area | `vesselDetails_summarySection` | East Mediterranean | yes |
| Notes | page footer | Locked content | locked |

Across the six ships tested, the locked set was identical every time: `Service Status`, `Port of registry`, `Year built`, `Latitude/Longitude`, and the voyage track and forecast. The client puts those labels in a `locked` list on every record so a downstream job can tell "not public" apart from "missing".

The page also has tabs for port call log, vessel characteristics, ownership and compliance. They are anchors (`#portCallLog`, `#vesselCharacteristics`) on the same React page, and clicking one in a `js_scenario` did not add any of those sections to the DOM in testing, so this repo stays on the overview.

## How the vessel page is built

Without JavaScript, the page is a 3,432 byte shell whose body is just `<div id="app"></div>` and a Cloudflare script. The real content appears only after the app runs and fills each section. Once rendered, the data sits in three readable shapes.

**ARIA tables.** The General and Latest AIS information blocks are not `<table>` elements. Each row is a `div[role=row]` with a `div[role=rowheader]` label and a `div[role=cell]` value, trimmed here from the rendered page:

```html
<section class="MuiStack-root css-pddyh9" id="vesselDetails_aisInfoSection">
  <h2 class="MuiTypography-root MuiTypography-body1 css-aoeep9">Latest AIS information</h2>
  <div role="table" class="w-full text-sm"><div role="rowgroup">
    <div role="row" class="border-muted flex border-b ...">
      <div role="rowheader" class="text-muted-foreground/80 w-2/5 ...">Navigational status</div>
      <div role="cell" class="w-3/5 px-4 py-2 text-xs font-bold">At Anchor</div>
    </div>
    ...
    <div role="row" class="...">
      <div role="rowheader" class="...">Latitude/Longitude</div>
      <div role="cell" class="..."><span class="text-muted-foreground text-xs italic">Upgrade to unlock</span></div>
    </div>
```

The utility classes are generated, so the selectors in this repo use only the section ids and the ARIA roles, which carry meaning and are less likely to churn.

**The voyage block.** Port names come as paragraph text, the port codes as links to port pages, and the progress bar exposes its value in an ARIA attribute (classes and the map stripped):

```html
<section id="vesselDetails_voyageSection"><h2>Current voyage</h2>
  ...
  <div><p>Departure from Singapore</p><a href="/en/ais/details/ports/290">SG SIN</a></div>
  <div><p>Arrival at Alexandria Anch</p><a href="/en/ais/details/ports/22348">ALEXANDRIA ANCH</a></div>
  ...
  <div role="progressbar" aria-label="Voyage progress bar" aria-valuenow="100" aria-valuemin="0" aria-valuemax="100">
  ...
  <p>Actual time of departure:</p><p>2026-11-08 00:50 (UTC+8)</p>
  <p>Actual time of arrival:</p><p>2026-11-21 10:30 (UTC+2)</p>
```

For a ship still at sea, the second time pair reads `Reported ETA:` instead of `Actual time of arrival:`, and the progress bar was absent on two of the three underway ships. The parser reads every `label:` paragraph followed by a value, so both layouts land in `voyage.times`.

**JSON-LD and meta tags.** React Helmet injects two JSON-LD blocks after render. The first is a `Thing`, not a schema.org `Vehicle` or `Ship`:

```json
{"@context":"https://schema.org","@type":"Thing","name":"CMA CGM EUGENIE",
 "url":"https://www.marinetraffic.com/en/ais/details/ships/shipid:10021654/mmsi:249920000/imo:9996991/vessel:CMA_CGM_EUGENIE",
 "description":"CMA CGM EUGENIE is a Container Ship sailing under the flag of Malta.",
 "identifier":[{"@type":"PropertyValue","propertyID":"IMO","value":"9996991"},
               {"@type":"PropertyValue","propertyID":"MMSI","value":"249920000"},
               {"@type":"PropertyValue","propertyID":"CallSign","value":"9HA6435"}]}
```

The second is a `BreadcrumbList` (Home, Ships, vessel name). The meta description repeats the identifiers in one line: `Track CMA CGM EUGENIE Container Ship. IMO 9996991, MMSI 249920000, Call Sign 9HA6435, Flag Malta.` The client compares the JSON-LD IMO and MMSI against the General table and records the result in `jsonld_checked`. It matched on all six ships, but it is the cheap way to notice a page that rendered the wrong vessel.

## The extract_rules

Sending [data extraction rules](https://www.scrapingbee.com/documentation/data-extraction/) with the request means ScrapingBee returns a few kilobytes of JSON instead of the 1.2 MB rendered page. These are the rules both scripts send:

```json
{
  "title": "h1",
  "jsonld": {"selector": "script[type='application/ld+json']", "type": "list", "output": "html"},
  "general": {"selector": "#vesselDetails_generalSection [role=row]", "type": "list",
              "output": {"label": "[role=rowheader]", "value": "[role=cell]"}},
  "ais": {"selector": "#vesselDetails_aisInfoSection [role=row]", "type": "list",
          "output": {"label": "[role=rowheader]", "value": "[role=cell]"}},
  "voyage_text": {"selector": "#vesselDetails_voyageSection p", "type": "list"},
  "voyage_ports": {"selector": "#vesselDetails_voyageSection a[href*='/ais/details/ports/']", "type": "list",
                   "output": {"code": "a", "href": "a@href"}},
  "progress": "#vesselDetails_voyageSection [aria-label='Voyage progress bar']@aria-valuenow",
  "summary": "#vesselDetails_summarySection"
}
```

The JSON-LD rule uses `output: "html"` on purpose. Text output on a `<script>` tag comes back empty, so the client takes the HTML and strips the tag itself.

The rules are paired with a short [JavaScript scenario](https://www.scrapingbee.com/documentation/js-scenario/) that holds the response until the AIS table has a filled cell:

```json
{"strict": false, "instructions": [
  {"wait_for": "#vesselDetails_aisInfoSection [role=cell]"},
  {"wait": 1500}
]}
```

A fixed `wait=8000` instead of `wait_for` returned the General table with the voyage and AIS sections still showing spinners, so waiting on the element is what makes the data arrive. `strict: false` lets a render that never fills the table come back anyway, and the client then decides whether to retry.

## Finding a vessel without the search box

MarineTraffic's own search runs through `/*/search/searchAsset` and `/*/global_search/`, both listed under Disallow for crawlers, so this repo does not use them. The vessel sitemaps are a better source anyway. `vessel_sitemap_index.xml` lists 47 gzip files, each fetched with `render_js=false` for 1 credit, and every `<url>` entry carries the ship's ids in the path:

```xml
<url><loc>https://www.marinetraffic.com/en/ais/details/ships/shipid:5630138/mmsi:353136000/imo:9811000/vessel:EVER_GIVEN</loc>
<xhtml:link rel="alternate" hreflang="ru" href="https://www.marinetraffic.com/ru/ais/details/ships/..."/>
... 12 more hreflang alternates ...
<lastmod>2025-11-10</lastmod><changefreq>weekly</changefreq><priority>0.5</priority></url>
```

The sitemap files work as the pagination of the vessel list. Indexing all of them took 70 seconds and 48 credits (the index file plus 47 sitemaps), and produced 987,849 entries, 910,799 of them unique by `shipid`. Only 92,796 of those carry an IMO number; the rest have `imo:0` and are mostly fishing, leisure and small craft known by MMSI alone. Every `lastmod` in the files read `2025-11`, so treat the index as a directory of page URLs, not as fresh data.

Two things the index taught:

* **The sitemap MMSI can be out of date.** EVER GIVEN is listed with MMSI 353136000, while its rendered page shows MMSI 636026627 and the flag of Liberia. Look ships up by IMO when you have it, and trust the page over the sitemap for anything else.
* **Short URLs are not reliable.** `/en/ais/details/ships/imo:9996991` redirected to the full vessel page once, `/en/ais/details/ships/imo:9811000` landed on `/not-found`, and `/en/ais/details/ships/mmsi:353136000` redirected to the live map. The client therefore always requests the full `shipid:` URL from the index.

## Which tier renders a vessel page

Every test below requested the full `shipid:` URL, most of them with the `wait_for` scenario above. `spb-cost` was read from each response.

| Configuration | Credits | Calls | What came back |
|---|---:|---:|---|
| classic, `render_js=false` | 1 | 1 | the 3,432 byte shell, `<div id="app"></div>` |
| classic, `render_js=true` | 5 | 2 | app chrome and footer, `mainSection` empty, no vessel data |
| premium, `render_js=true` | 25 | 18 | 3 full renders, 1 with only the General table, 3 with empty sections, then 11 in a row on `/not-found` |
| stealth | 75 | 11 | 11 full renders, first try each time |

All of those returned HTTP 200 with `spb-initial-status-code: 200`, including the `/not-found` ones, which show up only in `spb-resolved-url` and as an `<h1>` reading "Nothing to sea here". The status code tells you nothing on this site. A render that has no AIS rows, or a resolved URL outside `/ais/details/ships/`, is billed and is still a failure, which is what the client checks.

Premium worked in the first half hour of testing and then stopped, on URLs it had rendered minutes earlier. That pattern is why the scripts default to [stealth proxies](https://www.scrapingbee.com/documentation/) at 75 credits, with `--tier premium` available if you want to try the cheaper tier and accept retries. Without `country_code`, premium also landed on `/not-found`. HTTP 500 answers from ScrapingBee itself are not billed, so the client retries those for free; failed renders are retried once by default.

A ship read costs 75 credits on stealth, so a daily check of 20 ships runs to about 45,000 credits a month before retries. The 1,000 free credits on signup cover the index build plus a dozen vessel reads. [ScrapingBee pricing](https://www.scrapingbee.com/pricing/) lists the plans.

## marinetraffic_client.py

```bash
pip install requests
export SCRAPINGBEE_API_KEY=...

python examples/marinetraffic_client.py index --out vessels.csv        # 48 credits, once
python examples/marinetraffic_client.py find --index vessels.csv --name "EVER GIVEN"
python examples/marinetraffic_client.py vessel --imo 9811000 --index vessels.csv --json evergiven.json --csv evergiven.csv
```

The index run prints one line per sitemap and a total:

```
vessel_sitemap_0.xml.gz: 21428 vessels
vessel_sitemap_1.xml.gz: 21428 vessels
...
vessel_sitemap_46.xml.gz: 2161 vessels
987849 entries, 910799 unique ships -> vessels.csv (48 credits)
```

`find` is local and free:

```
  5630138  imo 9811000  mmsi  353136000  EVER GIVEN  https://www.marinetraffic.com/en/ais/details/ships/shipid:5630138/mmsi:353136000/imo:9811000/vessel:EVER_GIVEN
1 match(es)
```

`vessel` renders the page and prints the record. This run was made on the premium tier during testing, which is why it took a retry and 50 credits; the first render came back without the AIS table:

```json
{
  "url": "https://www.marinetraffic.com/en/ais/details/ships/shipid:5630138/mmsi:353136000/imo:9811000/vessel:EVER_GIVEN",
  "fetched_at": "2026-11-24T13:43:31+00:00",
  "name": "EVER GIVEN",
  "imo": "9811000",
  "mmsi": "636026627",
  "call_sign": "A8AB9",
  "flag": "Liberia",
  "ais_class": "Class A",
  "general_type": "Cargo - Hazard A (Major)",
  "detailed_type": "Container Ship",
  "nav_status": "At Anchor",
  "position_received": "2 mins ago",
  "local_time": "2026-11-24 21:41 (UTC+8)",
  "area": "South China",
  "speed_kn": 0.1,
  "course_deg": 294.0,
  "heading_deg": 17.0,
  "rate_of_turn": "0 °/min",
  "draught_m": 14.5,
  "reported_destination": "CNYTN",
  "matched_destination": "Yantian Anch, CHINA",
  "reported_eta": "2026-11-28 08:00 (UTC+8)",
  "ais_source": "Roaming",
  "voyage": {
    "departure": {"label": "Departure from Taipei", "code": "TW TPE", "port_id": "447"},
    "arrival": {"label": "Arrival at Yantian Anch", "code": "YANTIAN ANCH", "port_id": "22380"},
    "progress_pct": 100,
    "times": {
      "Actual time of departure": "2026-11-22 18:55 (UTC+8)",
      "Actual time of arrival": "2026-11-24 07:48 (UTC+8)"
    }
  },
  "locked": ["Service Status", "Port of registry", "Year built", "Latitude/Longitude",
             "Current voyage: past track and route forecast"],
  "jsonld_checked": true,
  "credits": 50
}
```

The pieces worth reusing are small. `_get` retries ScrapingBee 500s with a backoff and adds up `spb-cost`. `parse_vessel` turns label/value rows into fields, converts `0.1 kn`, `294 °` and `14.5 m` into floats, and moves every "Upgrade to unlock" label into `locked`. `Vessel.flat()` gives one CSV row with the voyage flattened:

```python
from marinetraffic_client import MarineTrafficClient, read_index, lookup

client = MarineTrafficClient()                     # stealth tier, one retry on an empty render
url = lookup(read_index("vessels.csv"), imo="9863297")[0].url
ship = client.vessel(url)
print(ship.nav_status, ship.speed_kn, ship.matched_destination, ship.voyage.arrival.code)
# Underway using Engine 15.6 Felixstowe, UNITED KINGDOM GB FXT
```

## voyage_tracker.py, the change log

Put IMO numbers (or MMSIs, or full vessel URLs) in a text file, one per line, and run the tracker from cron. It keeps the last known values in a JSON state file and appends only differences to the events CSV, which makes the CSV a history of port calls, status changes and destination edits.

```bash
cat > watchlist.txt <<'EOF'
9811000   # EVER GIVEN
9996991   # CMA CGM EUGENIE
9241061   # QUEEN MARY 2
9838345   # WONDER OF THE SEAS
9863297   # HMM ALGECIRAS
9929429   # MSC IRINA
EOF

python examples/voyage_tracker.py watchlist.txt --index vessels.csv \
    --state fleet_state.json --events fleet_events.csv --snapshot fleet_now.csv
```

The fields it watches are `nav_status`, `matched_destination`, `reported_eta`, `departure`, `arrival` and `area`. Before it starts, it multiplies the watchlist by the worst case (two renders per ship) and refuses to run above `--max-credits`, 1,000 by default. The stealth run over those six ships:

```
CMA CGM EUGENIE        first_seen           - -> At Anchor
EVER GIVEN             first_seen           - -> At Anchor
MSC IRINA              first_seen           - -> Underway using Engine
QUEEN MARY 2           first_seen           - -> Moored
WONDER OF THE SEAS     first_seen           - -> Underway using Engine
6 vessels read, 0 failed, 5 events, 450 credits
```

HMM ALGECIRAS has no line because it was already in the state file from an earlier run and nothing had changed. A second pass 25 minutes later over the three ships at sea read all three (225 credits) and logged `0 events`: speed moves every minute, but none of the watched voyage fields did, which is the point of watching those and not speed. The snapshot CSV from the same run, trimmed to a few columns:

| name | nav_status | area | speed_kn | matched_destination | departure | arrival | reported_eta |
|---|---|---|---:|---|---|---|---|
| EVER GIVEN | At Anchor | South China | 0.2 | Yantian Anch, CHINA | TW TPE | YANTIAN ANCH | 2026-11-28 08:00 (UTC+8) |
| QUEEN MARY 2 | Moored | Caribbean Sea | 0.0 | Philipsburg, SINT MAARTEN | KN BAS | SX PHI | 2026-11-24 05:30 (UTC-4) |
| WONDER OF THE SEAS | Underway using Engine | Caribbean Sea | 8.1 | Nassau, BAHAMAS | US MIA | BS NAS | 2026-11-25 06:15 (UTC-5) |
| MSC IRINA | Underway using Engine | Indonesia | 17.7 | King Abdullah, SAUDI ARABIA | IT GIT | SA KAC | 2026-11-11 03:45 (UTC+3) |
| HMM ALGECIRAS | Underway using Engine | South Indian Ocean | 15.6 | Felixstowe, UNITED KINGDOM | SG SIN | GB FXT | 2026-12-21 20:00 (UTC+0) |

The MSC IRINA row shows why the tracker stores what the page says instead of interpreting it. Its reported ETA to King Abdullah is two weeks in the past while the ship is sailing at 17.7 knots near Indonesia. "Reported" fields are typed in by the crew and go stale; "matched" fields are MarineTraffic's own reading. Keep both.

## vessel_status.js

The Node version sends the same scenario and a smaller rule set, and prints one JSON line per URL:

```bash
node examples/vessel_status.js "https://www.marinetraffic.com/en/ais/details/ships/shipid:6144387/mmsi:440326000/imo:9863297/vessel:HMM_ALGECIRAS"
```

```
{"name":"HMM ALGECIRAS","imo":"9863297","mmsi":"440326000","flag":"Republic of Korea","type":"Container Ship","status":"Underway using Engine","speed":"15.6 kn","destination":"Felixstowe, UNITED KINGDOM","eta":"2026-12-21 20:00 (UTC+0)","from":"Singapore","to":"Felixstowe","latlon":null,"credits":75}
75 credits
```

The request part, for reference:

```javascript
const params = new URLSearchParams({
  url,
  render_js: "true",
  stealth_proxy: "true",
  country_code: "us",
  js_scenario: JSON.stringify(scenario),
  extract_rules: JSON.stringify(rules),
});
const res = await fetch(`https://app.scrapingbee.com/api/v1/?${params}`, {
  headers: { Authorization: `Bearer ${process.env.SCRAPINGBEE_API_KEY}` },
});
```

## Port pages, measured and left out

Port pages are public too (`port_sitemap_0.xml.gz` lists 21,159 of them, for 1 credit), but they gave less for more. On premium, two renders of the Piraeus page both showed "Oops! This content failed to load." On stealth (75 credits) the page rendered with its title, UN/LOCODE `GRPIR`, the sea area, the alias list "ATHENS,PEIRAUS,PIR,PIRAEVS" and a few businesses at the port. Its port counts section still failed to load, and the terminals and wind forecast blocks were locked. That is too little to build a tool on, so the port ids the vessel parser collects (`port_id` in each voyage) are there for joining your own data, not for a port scraper.

## Marine Traffic API from Kpler, and when to pay for it

MarineTraffic sells an official API. Its reference at servicedocs.marinetraffic.com is titled "AIS Data API Reference", notes "Property of Kpler", and authenticates with an API key sent as the `api_key` query parameter. The endpoint groups it lists include vessel positions (single vessel, fleets, areas, ports), historical tracks, port calls and berth calls, vessel information and particulars, search by identifier or name, voyage forecasts and predictive destinations, expected and predictive port arrivals, port congestion, routing, reverse geocoding, fleet management and a credits balance call. MarineTraffic's support article on API services groups the products as AIS Data, Real-Time Events, Past Events, Predictive Events and Ships Database. Prices are not published on either page, so this README does not quote any.

The practical split:

* **Pay for the official API** if you need latitude and longitude, historical tracks, port call history, vessel particulars such as tonnage and build year, or area queries. None of that is public on the logged out page.
* **Use this repo** if what you need is the status line a visitor can read: where a known ship is heading, its declared ETA, whether it is moored, anchored or underway, its speed and draught, and which ports bracket the current voyage, for a watchlist small enough that 75 credits a read makes sense.

## Related ScrapingBee pages

* [Flight API](https://www.scrapingbee.com/scrapers/flight-api/) and [Google Flights scraper](https://www.scrapingbee.com/scrapers/google-flights-scraper/) for the air side of the same tracking problem
* [Amazon Shipping API](https://www.scrapingbee.com/scrapers/amazon-shipping-api/) for delivery estimates on the retail end
* [Bing Maps API](https://www.scrapingbee.com/scrapers/bing-maps-api/) for place and route lookups around ports
* [Alibaba API](https://www.scrapingbee.com/scrapers/alibaba-api/) for supplier and freight terms on traded goods
* [Expedia Scraper API](https://www.scrapingbee.com/scrapers/expedia-scraper-api/), another rendered travel site with measured tiers
* [JavaScript scenario feature](https://www.scrapingbee.com/features/javascript-scenario/), [data extraction feature](https://www.scrapingbee.com/features/data-extraction/) and the [API documentation](https://www.scrapingbee.com/documentation/)

## FAQ

**Is there a free MarineTraffic API?**
MarineTraffic's official API is a paid product and its documentation does not list a free tier. The vessel pages are free to view, and this repo reads them through ScrapingBee, which costs 75 credits per ship on the stealth tier. The 1,000 free ScrapingBee credits cover building the sitemap index and roughly a dozen vessel reads.

**Can I get a ship's latitude and longitude without logging in?**
No. The vessel page shows "Upgrade to unlock" for Latitude/Longitude to logged out visitors, and the past track and route forecast are locked too. You do get the sea area from the summary ("East Mediterranean"), the matched destination and the voyage ports, which is enough for many monitoring jobs. Coordinates need a MarineTraffic account or its API.

**How do I find a vessel's MarineTraffic page from an IMO number?**
Build the local index once with `marinetraffic_client.py index` (48 credits) and run `find --imo <number>`. It searches the public vessel sitemaps offline and returns the full `shipid:` URL. Short `imo:` URLs on the site redirected correctly once and to a not-found page another time, so the index is the dependable route.

**Why does my marine traffic api scraper get a blank page or "Nothing to sea here"?**
Without JavaScript the page is an empty React shell, and on the classic and, after a while, premium tiers the app either never filled its sections or redirected to `/not-found`. Both still return HTTP 200 and are billed. Render with `stealth_proxy=true`, wait for `#vesselDetails_aisInfoSection [role=cell]`, and check `spb-resolved-url` and the AIS rows before trusting the response.

**How often should a ship tracking API job poll?**
The AIS block says how old the last position report is ("5 mins ago"), so polling faster than that gains nothing. For voyage-level changes (departure, arrival, destination, status) a few times a day is plenty, and at 75 credits per read the polling interval is mostly a budget decision.

## License

MIT. See [LICENSE](LICENSE).
