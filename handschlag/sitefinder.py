"""Find the real website for dealers whose Google profile has none.

Layer 1 (free): guess domains from name + town, keep only candidates whose
homepage/impressum mentions the dealer's phone number or street+PLZ.
Results cached in data/sitefinder.json keyed by place_id.
"""
import json
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import requests
import urllib3

urllib3.disable_warnings()

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
LEGAL = re.compile(r"\b(gmbh|mbh|co|kg|ohg|gbr|ug|e\.?\s?k|ek|inh(aber)?\.?.*$|haftungsbeschränkt|ag|ltd|"
                   r"und|&|\+|der|die|das|the|inhaber.*$)\b", re.I)
GENERIC = {"autohaus", "automobile", "automobil", "autohandel", "auto", "autos", "cars", "car",
           "kfz", "gebrauchtwagen", "handel", "service", "center", "zentrum", "gmbh", "hamburg"}
PARKED = re.compile(r"domain (is|ist) (for sale|zu verkaufen)|sedo|parkingcrew|this domain|"
                    r"diese domain|domain kaufen|bodis|dan\.com|hier entsteht|under construction", re.I)


def translit(s):
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("é", "e"), ("è", "e")):
        s = s.replace(a, b)
    return s


def tokens(name):
    name = re.split(r"[|,(/]", name)[0]          # "Autohaus Tobaben | Ford Buxtehude" -> left part
    name = LEGAL.sub(" ", translit(name))
    return [t for t in re.findall(r"[a-z0-9]+", name) if len(t) > 1]


def town_of(address):
    m = re.search(r"\b\d{5}\s+([^,]+)", address or "")
    return translit(m.group(1).split()[0]) if m else ""


def candidates(name, address):
    t = tokens(name)
    if not t:
        return []
    town = town_of(address)
    core = [x for x in t if x not in GENERIC] or t
    bases = {
        "-".join(t), "".join(t), "-".join(core), "".join(core),
        "-".join(t[:2]), "".join(t[:2]), "-".join(core[:2]), "".join(core[:2]),
    }
    if town and town not in t:
        bases |= {"-".join(core + [town]), "".join(core + [town]), "-".join(t + [town])}
    for prefix in ("autohaus", "auto", "autohandel", "automobile"):
        if core and prefix not in t:
            bases |= {f"{prefix}-{core[0]}", f"{prefix}{core[0]}"}
    if core and "automobile" not in t:
        bases |= {f"{core[0]}-automobile", f"{core[0]}automobile"}
    bases = [b.strip("-") for b in bases if 3 <= len(b.strip("-")) <= 40]
    bases = sorted(bases, key=len, reverse=True)
    out = [f"{b}.de" for b in bases] + [f"{b}.com" for b in bases[:4]]
    return list(dict.fromkeys(out))[:16]


def _resolves(host):
    try:
        socket.getaddrinfo(host, 80)
        return True
    except OSError:
        try:
            socket.getaddrinfo("www." + host, 80)
            return True
        except OSError:
            return False


def _digits(s):
    return re.sub(r"\D", "", s or "")


def evidence(text, place):
    """How well does this page match the Google place? returns (level, reason)."""
    t = translit(text)
    td = _digits(text)
    phone = _digits(place.get("phone"))
    if phone.startswith("0"):
        phone = phone[1:]
    if len(phone) >= 7 and phone[-7:] in td:
        return "high", "phone match"
    addr = place.get("address", "")
    plz = re.search(r"\b(\d{5})\b", addr)
    street = translit(addr.split(",")[0])
    street_word = re.sub(r"\d.*", "", street).replace("strasse", "str").strip()[:10]
    t_norm = t.replace("straße", "str").replace("strasse", "str")
    if plz and plz.group(1) in t and street_word and street_word in t_norm:
        return "high", "street + PLZ match"
    core = [x for x in tokens(place["name"]) if x not in GENERIC]
    if plz and plz.group(1) in t and core and all(c in t for c in core[:2]):
        return "medium", "PLZ + name match"
    return None, ""


def _fetch(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=(4, 6), allow_redirects=True, verify=False)
        return r if r.ok else None
    except Exception:
        return None


def check_domain(dom, place):
    if not _resolves(dom):
        return None
    for base in (f"http://www.{dom}/", f"http://{dom}/"):
        r = _fetch(base)
        if r is not None:
            break
    else:
        return None
    if len(r.text) < 3000 and PARKED.search(r.text):
        return None
    text = r.text
    lvl, why = evidence(text, place)
    if not lvl:
        for p in ("/impressum", "/kontakt"):
            ri = _fetch(urljoin(r.url, p))
            if ri is not None:
                text += ri.text
                lvl, why = evidence(text, place)
                if lvl:
                    break
    if lvl:
        return {"website": r.url, "confidence": lvl, "evidence": why, "method": "domain-guess"}
    return None


def find_site(place):
    for dom in candidates(place["name"], place["address"]):
        hit = check_domain(dom, place)
        if hit and hit["confidence"] == "high":
            return hit
        if hit:
            best = hit
            break
    else:
        best = None
    return best or {"website": "", "confidence": None, "method": "domain-guess", "tried": True}


def run(places, cache_path: Path, workers=32):
    requests.packages.urllib3.disable_warnings()
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = [p for p in places if p["place_id"] not in cache]
    if todo:
        print(f"[sitefinder] guessing domains for {len(todo)} dealers ...")
        with ThreadPoolExecutor(workers) as ex:
            for i, (p, res) in enumerate(zip(todo, ex.map(find_site, todo)), 1):
                cache[p["place_id"]] = res
                if i % 20 == 0 or i == len(todo):
                    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
                    print(f"[sitefinder] {i}/{len(todo)}, found so far: "
                          f"{sum(1 for v in cache.values() if v.get('website'))}", flush=True)
    found = sum(1 for p in places if cache.get(p["place_id"], {}).get("website"))
    print(f"[sitefinder] {found}/{len(places)} websites found")
    return cache


def verify_url(url, place):
    """Verify a URL found by web search against the Google place (phone / address)."""
    r = _fetch(url)
    if r is None:
        return None
    text = r.text
    lvl, why = evidence(text, place)
    if not lvl:
        for p in ("/impressum", "/kontakt", "/impressum/", "/kontakt/"):
            ri = _fetch(urljoin(r.url, p))
            if ri is not None:
                text += ri.text
                lvl, why = evidence(text, place)
                if lvl:
                    break
    if lvl:
        return {"website": r.url, "confidence": lvl, "evidence": why, "method": "web-search"}
    # reachable but no proof: keep as low-confidence so a human can check
    return {"website": r.url, "confidence": "low", "evidence": "search hit, no phone/address match",
            "method": "web-search"}


def ingest_search(result_files, places, cache_path: Path):
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    by_id = {p["place_id"]: p for p in places}
    hits = []
    for f in result_files:
        hits += [h for h in json.loads(Path(f).read_text()) if h.get("url")]
    stats = {"high": 0, "medium": 0, "low": 0, "dead": 0}
    with ThreadPoolExecutor(16) as ex:
        res = list(ex.map(lambda h: (h, verify_url(h["url"], by_id[h["place_id"]])
                                     if h["place_id"] in by_id else None), hits))
    for h, v in res:
        if v is None:
            stats["dead"] += 1
            continue
        stats[v["confidence"]] += 1
        cache[h["place_id"]] = v
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    print(f"[search-ingest] {len(hits)} urls: {stats}")
    return stats


def domain_matches_name(url, name):
    """The dealer's own site almost always carries its name in the domain."""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    label = re.sub(r"[^a-z0-9]", "", translit(host.rsplit(".", 1)[0]))
    toks = [t for t in tokens(name) if t not in GENERIC and len(t) >= 3]
    if not toks:
        toks = [t for t in tokens(name) if len(t) >= 4]
    return any(t in label for t in toks)


def root_of(url):
    from urllib.parse import urlparse
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}/"
