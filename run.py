"""Handschlag lead pipeline - stages 0-3.

    python3 run.py                    # center from HANDSCHLAG_LAT/LON (.env), 50 km
    python3 run.py --lat 53.55 --lon 9.99
    python3 run.py --radius 30
    python3 run.py --refresh          # re-crawl websites (Places stays cached)

Outputs: data/dealers.csv and report.html
"""
import argparse
import csv
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from handschlag import audit, brave, contacts, discover, score, sitefinder

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DEFAULT_CENTER = (53.5511, 9.9937)  # Hamburg city center; override with HANDSCHLAG_LAT/LON


def default_center():
    lat = discover.load_key(ROOT / ".env", "HANDSCHLAG_LAT", required=False)
    lon = discover.load_key(ROOT / ".env", "HANDSCHLAG_LON", required=False)
    return (float(lat) if lat else DEFAULT_CENTER[0], float(lon) if lon else DEFAULT_CENTER[1])


def main():
    center = default_center()
    ap = argparse.ArgumentParser(description="Find car dealers, audit their websites, build report.html")
    ap.add_argument("--lat", type=float, default=center[0], help="search center latitude")
    ap.add_argument("--lon", type=float, default=center[1], help="search center longitude")
    ap.add_argument("--radius", type=float, default=50, help="search radius in km")
    ap.add_argument("--max-calls", type=int, default=600, help="max Google Places API calls")
    ap.add_argument("--workers", type=int, default=8, help="parallel website audits")
    ap.add_argument("--refresh", action="store_true", help="re-crawl websites (Places stays cached)")
    ap.add_argument("--brave-min-dealer", type=int, default=None,
                    help="run Brave search for missing sites of car dealers with dealer score >= N")
    ap.add_argument("--ingest", nargs="*", help="web-search result JSON files to verify + merge")
    args = ap.parse_args()

    DATA.mkdir(exist_ok=True)
    key = discover.load_key(ROOT / ".env")
    places = discover.discover(args.lat, args.lon, args.radius, key,
                               DATA / "places.json", max_calls=args.max_calls)

    # find real websites for listings without one (or only facebook/mobile.de etc.)
    def not_own(url):
        d = audit.domain_of(url)
        return any(d == k or d.endswith("." + k) for k in audit.NOT_OWN_SITE)
    missing = [p for p in places if not p["website"] or not_own(p["website"])]
    found = sitefinder.run(missing, DATA / "sitefinder.json")
    if args.brave_min_dealer is not None:
        bkey = discover.load_key(ROOT / ".env", "BRAVE_API_KEY")
        targets = [p for p in missing if score.category(p["name"]) == "car"
                   and score.dealer_score(p["rating"], p["reviews"] or 0) >= args.brave_min_dealer]
        print(f"[brave] searching {len(targets)} dealers (dealer score >= {args.brave_min_dealer})")
        found = brave.run(targets, bkey, DATA, DATA / "sitefinder.json")
    if args.ingest:
        sitefinder.ingest_search(args.ingest, places, DATA / "sitefinder.json")
        found = json.loads((DATA / "sitefinder.json").read_text())
    for p in places:
        p["website_source"] = "google" if p["website"] else ""
        hit = found.get(p["place_id"], {})
        if p in missing and hit.get("website") and hit.get("confidence") in ("high", "medium", "low"):
            p["google_website"] = p["website"]
            p["website"] = hit["website"]
            p["website_source"] = f"found ({hit['confidence']}: {hit['evidence']})"

    # manual overrides: data/overrides.csv with columns name,website (your corrections win)
    ov_path = DATA / "overrides.csv"
    if not ov_path.exists():
        ov_path.write_text("name,website\n")
    overrides = {r["name"].strip().lower(): r["website"].strip()
                 for r in csv.DictReader(open(ov_path)) if r.get("website")}
    for p in places:
        w = overrides.get(p["name"].strip().lower())
        if w:
            p["google_website"] = p.get("google_website", p["website"])
            p["website"], p["website_source"] = w, "found (manual)"

    # chain detection: same website domain on 3+ Google listings
    doms = Counter(audit.domain_of(p["website"]) for p in places if p["website"])

    to_audit = {p["website"] for p in places if p["website"]}
    audits = {}
    print(f"[audit] {len(to_audit)} websites ...")
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(audit.audit_site, u, DATA / "audits", args.refresh): u for u in to_audit}
        for i, fu in enumerate(as_completed(futs), 1):
            u = futs[fu]
            try:
                audits[u] = fu.result()
            except Exception as e:
                audits[u] = {"ok": False, "site_type": "own", "error": f"crash:{type(e).__name__}"}
            if i % 50 == 0:
                print(f"[audit] {i}/{len(to_audit)}")

    site_urls = {u: (a.get("final_url") or u) for u, a in audits.items()
                 if a.get("ok") and a.get("site_type") == "own"}
    cmap = contacts.run(site_urls.values(), DATA / "contacts")

    rows = []
    for p in places:
        a = audits.get(p["website"]) if p["website"] else {"site_type": "none"}
        c = cmap.get(site_urls.get(p["website"], ""), {}) if p["website"] else {}
        if not p["website"]:
            findings = [("no_website", 100, "No website found (Google profile empty, domain guess failed)")]
        else:
            findings = score.site_findings(a)
            if p["website_source"].startswith("found (low"):
                findings.append(("unverified_match", 0, "Website found by search but not verified - check it's really them"))
            if p["website_source"].startswith("found"):
                findings.append(("gbp_missing_site", 10,
                                 "Website not linked on Google Business Profile - losing local clicks"))
        dom = audit.domain_of(p["website"]) if p["website"] else ""
        chain = (doms.get(dom, 0) >= 3 if dom else False) or a.get("site_type") == "oem"
        s = score.site_score(findings)
        d = score.dealer_score(p["rating"], p["reviews"] or 0)
        rows.append({
            **p,
            "domain": dom,
            "category": score.category(p["name"]),
            "chain": chain,
            "chain_size": doms.get(dom, 0) if dom else 0,
            "site_type": a.get("site_type", "none") if p["website"] else "none",
            "site_ok": bool(a.get("ok")),
            "audit_blocked": bool(a.get("blocked")),
            "https_status": a.get("https_status", ""),
            "phone_kind": contacts.phone_kind(p["phone"]),
            "email": (c.get("emails") or [""])[0],
            "email_alt": ", ".join((c.get("emails") or [])[1:]),
            "contact_person": ", ".join(c.get("people") or []),
            "impressum_url": c.get("impressum_url", ""),
            "site_score": s,
            "dealer_score": d,
            "lead_score": score.lead_score(s, d, chain),
            "generator": a.get("generator", ""),
            "builder": a.get("builder", ""),
            "inventory_provider": a.get("inventory_provider", ""),
            "copyright_year": a.get("copyright_year"),
            "title": a.get("title", ""),
            "findings": [t for _, _, t in sorted(findings, key=lambda x: -x[1])],
        })
    rows.sort(key=lambda r: -r["lead_score"])

    DATA.mkdir(exist_ok=True)
    with open(DATA / "dealers.csv", "w", newline="", encoding="utf-8") as fh:
        cols = list(dict.fromkeys(k for r in rows for k in r if k != "findings")) + ["findings"]
        w = csv.DictWriter(fh, fieldnames=cols, restval="")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "findings": " | ".join(r["findings"])})

    tpl = (ROOT / "handschlag" / "report_template.html").read_text()
    (ROOT / "report.html").write_text(
        tpl.replace("/*DATA*/[]", json.dumps(rows, ensure_ascii=False)), encoding="utf-8")

    cars = [r for r in rows if r["category"] == "car"]
    print(f"\n{len(rows)} dealers | {len(cars)} car dealers | "
          f"{sum(r['site_type']=='none' for r in cars)} without website | "
          f"{sum(r['chain'] for r in cars)} chain listings")
    print("Top 15 leads (car dealers, no chains):")
    for r in [r for r in cars if not r["chain"]][:15]:
        print(f"  {r['lead_score']:>3}  site {r['site_score']:>3}  dealer {r['dealer_score']:>3}  "
              f"{r['distance_km']:>5}km  {r['name'][:40]:<40} {r['domain']}")
    print(f"\nWrote {DATA/'dealers.csv'} and {ROOT/'report.html'}")


if __name__ == "__main__":
    main()
