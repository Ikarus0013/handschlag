"""Stage 0 - Discover: find every car dealer within a radius via Google Places (New).

Nearby Search returns at most 20 results per call, so we tile the area with
squares and split any square that comes back full (adaptive quadtree).
Results are cached in data/places.json so re-runs cost nothing.
"""
import json
import math
import os
import time
from pathlib import Path

import requests

API = "https://places.googleapis.com/v1/places:searchNearby"
FIELDS = ",".join(
    "places." + f
    for f in [
        "id", "displayName", "formattedAddress", "location", "websiteUri",
        "rating", "userRatingCount", "nationalPhoneNumber", "businessStatus",
        "primaryType", "types", "googleMapsUri",
    ]
)
KM_PER_DEG_LAT = 111.32


def _km_per_deg_lon(lat):
    return KM_PER_DEG_LAT * math.cos(math.radians(lat))


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _search(key, lat, lon, radius_m):
    body = {
        "includedTypes": ["car_dealer"],
        "maxResultCount": 20,
        "rankPreference": "DISTANCE",
        "languageCode": "de",
        "locationRestriction": {
            "circle": {"center": {"latitude": lat, "longitude": lon}, "radius": radius_m}
        },
    }
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS}
    for attempt in range(3):
        r = requests.post(API, json=body, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.json().get("places", [])
        if r.status_code in (429, 500, 503):
            time.sleep(2 ** attempt)
            continue
        raise RuntimeError(f"Places API {r.status_code}: {r.text[:300]}")
    raise RuntimeError("Places API kept failing")


def discover(center_lat, center_lon, radius_km, key, cache_path: Path,
             max_calls=600, cell_km=10.0, min_cell_km=0.8):
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    params = f"{float(center_lat)},{float(center_lon)},{float(radius_km)}"
    if cache.get("params") == params:
        print(f"[discover] using cache ({len(cache['places'])} places, 0 API calls)")
        return cache["places"]

    places, calls = {}, 0
    kx = _km_per_deg_lon(center_lat)
    half = cell_km / 2
    n = math.ceil(radius_km / cell_km)
    queue = []
    for i in range(-n, n):
        for j in range(-n, n):
            cx, cy = (i + 0.5) * cell_km, (j + 0.5) * cell_km  # km offsets
            # skip squares entirely outside the circle
            nearest = math.hypot(max(abs(cx) - half, 0), max(abs(cy) - half, 0))
            if nearest <= radius_km:
                queue.append((cx, cy, half))

    while queue:
        if calls >= max_calls:
            print(f"[discover] hit max_calls={max_calls}, {len(queue)} cells unsearched")
            break
        cx, cy, h = queue.pop()
        lat = center_lat + cy / KM_PER_DEG_LAT
        lon = center_lon + cx / kx
        res = _search(key, lat, lon, h * math.sqrt(2) * 1000)
        calls += 1
        for p in res:
            places[p["id"]] = p
        if len(res) >= 20 and h / 2 >= min_cell_km / 2:
            q = h / 2
            queue += [(cx + dx * q, cy + dy * q, q) for dx in (-1, 1) for dy in (-1, 1)]
        if calls % 25 == 0:
            print(f"[discover] {calls} calls, {len(places)} places, {len(queue)} cells left")

    out = []
    for p in places.values():
        loc = p.get("location", {})
        d = haversine_km(center_lat, center_lon, loc.get("latitude", 0), loc.get("longitude", 0))
        if d > radius_km or p.get("businessStatus", "OPERATIONAL") != "OPERATIONAL":
            continue
        out.append({
            "place_id": p["id"],
            "name": p.get("displayName", {}).get("text", ""),
            "address": p.get("formattedAddress", ""),
            "lat": loc.get("latitude"),
            "lon": loc.get("longitude"),
            "distance_km": round(d, 1),
            "website": p.get("websiteUri", ""),
            "phone": p.get("nationalPhoneNumber", ""),
            "rating": p.get("rating"),
            "reviews": p.get("userRatingCount", 0),
            "primary_type": p.get("primaryType", ""),
            "maps_url": p.get("googleMapsUri", ""),
        })
    out.sort(key=lambda x: x["distance_km"])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"params": params, "calls": calls, "places": out},
                                     ensure_ascii=False, indent=1))
    print(f"[discover] done: {calls} API calls, {len(out)} operational dealers in radius")
    return out


def load_key(env_path: Path, name="GOOGLE_PLACES_KEY", required=True):
    key = os.environ.get(name)
    if not key and env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith(name + "="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key and required:
        raise SystemExit(f"No {name} in environment or .env")
    return key
