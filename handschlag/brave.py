"""Layer 2 site finder: Brave Search API, up to 3 queries per dealer.

Every candidate URL is verified against the Google place (phone / street+PLZ)
by sitefinder.verify_url before it is accepted. Queries are cached in
data/brave_cache.json so re-runs don't spend credits.
"""
import json
import re
import time
from pathlib import Path

import requests

from . import sitefinder as sf

API = "https://api.search.brave.com/res/v1/web/search"
SKIP = re.compile(
    r"mobile\.de|autoscout24|facebook|instagram|kleinanzeigen|ebay\.|gelbeseiten|dasoertliche|"
    r"dastelefonbuch|11880|cylex|werkenntdenbesten|golocal|yelp|branchen|stadtbranchenbuch|"
    r"goyellow|meinestadt|wheree|autoplenum|finde-offen|northdata|firmenwissen|creditreform|"
    r"google\.|youtube|tiktok|linkedin|xing|wikipedia|tripadvisor|carwow|heycar|autohero|"
    r"pkw\.de|autouncle|truckscout|kfz-betrieb|onlinestreet|cybo|hotfrog|infobel|tuugo|"
    r"opendi|kennstdueinen|yably|unternehmensregister|bundesanzeiger|moneyhouse|"
    r"guenstig-gebrauchtwagen|deutscheshoponline|autobild|mapquest|apple\.com|bing\.|here\.com|waze", re.I)


class Brave:
    def __init__(self, key, cache_path: Path):
        self.key, self.cache_path = key, cache_path
        self.cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        self.calls, self._last = 0, 0.0

    def search(self, q):
        if q in self.cache:
            return self.cache[q]
        wait = 1.05 - (time.time() - self._last)   # free plan: 1 req/s
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            self._last = time.time()
            r = requests.get(API, params={"q": q, "country": "DE", "search_lang": "de", "count": 10},
                             headers={"X-Subscription-Token": self.key, "Accept": "application/json"},
                             timeout=20)
            if r.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            r.raise_for_status()
            break
        else:
            raise RuntimeError("Brave rate-limited repeatedly")
        self.calls += 1
        urls = [x["url"] for x in r.json().get("web", {}).get("results", [])]
        self.cache[q] = urls
        if self.calls % 10 == 0:
            self.save()
        return urls

    def save(self):
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1))


def queries(p):
    name = re.split(r"[|(]", p["name"])[0].strip()
    town = sf.town_of(p["address"]) or ""
    town_raw = re.search(r"\b\d{5}\s+([^,]+)", p["address"] or "")
    town_raw = town_raw.group(1).strip() if town_raw else town
    street = p["address"].split(",")[0]
    plz = re.search(r"\b\d{5}\b", p["address"] or "")
    qs = [f'"{name}" {town_raw}']
    if p.get("phone"):
        qs.append(f'"{p["phone"]}"')
    qs.append(f'{name} {street} {plz.group(0) if plz else ""}'.strip())
    return qs


def find(p, brave: Brave, seen_domains: set):
    for q in queries(p):
        cands = [sf.root_of(u) for u in brave.search(q)
                 if not SKIP.search(u) and sf.domain_matches_name(u, p["name"])]
        for u in list(dict.fromkeys(cands))[:3]:
            if u in seen_domains:
                continue
            seen_domains.add(u)
            v = sf.verify_url(u, p)
            if v and v["confidence"] in ("high", "medium"):
                v["method"] = "brave"
                v["query"] = q
                return v
    return None


def run(places, key, cache_dir: Path, sitefinder_cache: Path):
    brave = Brave(key, cache_dir / "brave_cache.json")
    found_cache = json.loads(sitefinder_cache.read_text()) if sitefinder_cache.exists() else {}
    hits = 0
    for i, p in enumerate(places, 1):
        prev = found_cache.get(p["place_id"], {})
        if prev.get("website") and prev.get("confidence") in ("high", "medium"):
            continue
        if prev.get("brave_tried"):
            continue
        try:
            v = find(p, brave, set())
        except Exception as e:
            print(f"[brave] error on {p['name']}: {e}")
            break
        if v:
            hits += 1
            found_cache[p["place_id"]] = v
        else:
            found_cache[p["place_id"]] = {**prev, "brave_tried": True}
        if i % 10 == 0:
            sitefinder_cache.write_text(json.dumps(found_cache, ensure_ascii=False, indent=1))
            print(f"[brave] {i}/{len(places)} dealers, {hits} sites found, {brave.calls} queries", flush=True)
    brave.save()
    sitefinder_cache.write_text(json.dumps(found_cache, ensure_ascii=False, indent=1))
    print(f"[brave] done: {hits} sites found, {brave.calls} queries used")
    return found_cache
