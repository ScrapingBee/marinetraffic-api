// Print the public status line for one or more MarineTraffic vessel pages.
// Node 18+ (global fetch), no dependencies.
//
//   export SCRAPINGBEE_API_KEY=...
//   node vessel_status.js "https://www.marinetraffic.com/en/ais/details/ships/shipid:6144387/mmsi:440326000/imo:9863297/vessel:HMM_ALGECIRAS"
//
// Each page costs 75 credits (stealth_proxy, which renders JavaScript). A render that comes
// back without the AIS table, or that lands on /not-found, is billed too, so
// the script retries it once and reports what it spent.

const API = "https://app.scrapingbee.com/api/v1/";

const scenario = {
  strict: false,
  instructions: [
    { wait_for: "#vesselDetails_aisInfoSection [role=cell]" },
    { wait: 1500 },
  ],
};

const row = (section) => ({
  selector: `#vesselDetails_${section} [role=row]`,
  type: "list",
  output: { label: "[role=rowheader]", value: "[role=cell]" },
});

const rules = {
  general: row("generalSection"),
  ais: row("aisInfoSection"),
  voyage: { selector: "#vesselDetails_voyageSection p", type: "list" },
};

async function scrape(url) {
  const params = new URLSearchParams({
    url,
    render_js: "true",
    stealth_proxy: "true",
    country_code: "us",
    js_scenario: JSON.stringify(scenario),
    extract_rules: JSON.stringify(rules),
  });
  for (let attempt = 0; attempt < 4; attempt++) {
    const res = await fetch(`${API}?${params}`, {
      headers: { Authorization: `Bearer ${process.env.SCRAPINGBEE_API_KEY}` },
    });
    if (res.status === 500) continue; // not billed by ScrapingBee, safe to retry
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
    return {
      cost: Number(res.headers.get("spb-cost") || 0),
      resolved: res.headers.get("spb-resolved-url") || url,
      data: await res.json(),
    };
  }
  throw new Error("ScrapingBee returned 500 four times");
}

function table(rows) {
  const out = {};
  for (const { label, value } of rows || []) {
    out[label] = value === "Upgrade to unlock" ? null : value;
  }
  return out;
}

async function vessel(url) {
  let spent = 0;
  for (let attempt = 0; attempt < 2; attempt++) {
    const { cost, resolved, data } = await scrape(url);
    spent += cost;
    if (resolved.includes("/ais/details/ships/") && data.ais && data.ais.length) {
      const g = table(data.general);
      const a = table(data.ais);
      const v = data.voyage || [];
      return {
        name: g["Name"],
        imo: g["IMO"],
        mmsi: g["MMSI"],
        flag: g["Flag"],
        type: g["Detailed vessel type"],
        status: a["Navigational status"],
        speed: a["Speed"],
        destination: a["Matched destination"],
        eta: a["Reported ETA"],
        from: (v.find((t) => t.startsWith("Departure from")) || "").replace("Departure from ", ""),
        to: (v.find((t) => t.startsWith("Arrival at")) || "").replace("Arrival at ", ""),
        latlon: a["Latitude/Longitude"], // null for logged out visitors
        credits: spent,
      };
    }
  }
  return { url, error: "no vessel data after 2 renders", credits: spent };
}

async function main() {
  const urls = process.argv.slice(2);
  if (!urls.length || !process.env.SCRAPINGBEE_API_KEY) {
    console.error("usage: SCRAPINGBEE_API_KEY=... node vessel_status.js <vessel page url> [...]");
    process.exit(1);
  }
  let total = 0;
  for (const url of urls) {
    const result = await vessel(url);
    total += result.credits;
    console.log(JSON.stringify(result));
  }
  console.error(`${total} credits`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
