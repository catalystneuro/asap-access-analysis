#!/usr/bin/env python3
"""Generate index.html — a standalone dashboard of DANDI download statistics
for the ASAP-affiliated Dandisets.

Data sources
------------
1. DANDI ``access-summaries`` (per-day / per-region / per-asset download stats).
   Point ``--content`` at a local checkout's ``content/`` directory, or let the
   script download the published tarball from the ``dist`` branch.
2. The DANDI REST API (dataset storage size + creation date).
3. Natural Earth 110m land polygons (world-map outline), cached under ``assets/``.

Curated per-dataset metadata (PI, modality, region, species, status) lives in
``datasets.yaml`` — edit that file to change the dataset list or labels.

Usage
-----
    python build.py                     # build using ../access-summaries/content
    python build.py --content PATH      # use a specific access-summaries content dir
    python build.py --download          # fetch the access-summaries tarball if needed
    python build.py --offline           # use cached DANDI API / land data, no network
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import tarfile
import urllib.request
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
ASSETS = ROOT / "assets"

CONTENT_TARBALL = "https://raw.githubusercontent.com/dandi/access-summaries/dist/content.tar.gz"
LAND_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_land.geojson"
DANDI_API = "https://api.dandiarchive.org/api/dandisets/{}/"

# Map projection canvas (equirectangular).
MAP_W, MAP_H = 1000, 500
# Regions that carry no meaningful geographic location.
SKIP_REGIONS = {"missing", "undetermined", "VPN", "bogon"}
CLOUD_PREFIXES = ("AWS", "GCP", "GitHub")
TOP_N_TIMELINE = 6


# ────────────────────────── helpers ──────────────────────────
def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def fetch(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def suppressed(v):
    """DANDI hides small counts as '<50'. Return int, or None if suppressed."""
    if isinstance(v, str) and v.startswith("<"):
        return None
    return int(v)


def month_range(first: str, last: str) -> list[str]:
    y, m = int(first[:4]), int(first[5:7])
    ye, me = int(last[:4]), int(last[5:7])
    out = []
    while (y, m) <= (ye, me):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


# ────────────────────── access-summaries ──────────────────────
def resolve_content(content_arg: str | None, download: bool) -> Path:
    candidates = []
    if content_arg:
        candidates.append(Path(content_arg))
    if os.environ.get("ASAP_CONTENT"):
        candidates.append(Path(os.environ["ASAP_CONTENT"]))
    candidates.append(ROOT.parent / "access-summaries" / "content")
    candidates.append(CACHE / "content")
    for c in candidates:
        if (c / "totals.json").exists():
            log(f"• access-summaries content: {c}")
            return c
    if download:
        dest = CACHE / "content"
        log(f"• downloading access-summaries tarball → {dest}")
        CACHE.mkdir(exist_ok=True)
        blob = fetch(CONTENT_TARBALL, timeout=180)
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            tar.extractall(CACHE, filter="data")
        if (dest / "totals.json").exists():
            return dest
    sys.exit(
        "ERROR: could not find access-summaries content/. Pass --content PATH, set "
        "ASAP_CONTENT, clone dandi/access-summaries next to this repo, or use --download."
    )


def read_tsv(path: Path):
    with open(path, newline="") as f:
        yield from csv.DictReader(f, delimiter="\t")


def build_access(content: Path, order: list[str]):
    totals = json.loads((content / "totals.json").read_text())
    present = [d for d in order if (content / "summaries" / d).is_dir()]

    per, monthly, region_tot = {}, defaultdict(lambda: [0, 0]), defaultdict(int)
    for d in present:
        t = totals[d]
        days = [r["date"] for r in read_tsv(content / "summaries" / d / "by_day.tsv")]
        per[d] = {
            "bytes": t["total_bytes_sent"],
            "dl": suppressed(t["total_number_of_downloads"]),
            "req": t["total_number_of_requests"],
            "users": t["number_of_requesters"],
            "countries": t["number_of_unique_countries"],
            "regions": t["number_of_unique_regions"],
            "first": min(days),
            "last": max(days),
        }
        for r in read_tsv(content / "summaries" / d / "by_day.tsv"):
            m = r["date"][:7]
            monthly[m][0] += int(r["bytes_sent"])
            dl = suppressed(r["number_of_downloads"])
            monthly[m][1] += dl or 0
        for r in read_tsv(content / "summaries" / d / "by_region.tsv"):
            region_tot[r["region"]] += int(r["bytes_sent"])

    data = {
        "per": per,
        "monthly": [{"m": m, "bytes": v[0], "dl": v[1]} for m, v in sorted(monthly.items())],
        "regions": [
            {"region": k, "bytes": v}
            for k, v in sorted(region_tot.items(), key=lambda x: -x[1])
        ],
    }
    return present, data


# ────────────────────────── DANDI API ──────────────────────────
def dandi_meta(present: list[str], offline: bool) -> dict:
    cache_path = CACHE / "dandi_meta.json"
    cached = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    if offline:
        if not cached:
            sys.exit("ERROR: --offline but no cached DANDI metadata at " + str(cache_path))
        log("• DANDI API: using cache (offline)")
        return cached

    meta = dict(cached)
    for d in present:
        try:
            j = json.loads(fetch(DANDI_API.format(d), timeout=45))
            ver = j.get("most_recent_published_version") or j.get("draft_version") or {}
            meta[d] = {"size": ver.get("size"), "created": (j.get("created") or "")[:10]}
            log(f"  {d}  size={ (meta[d]['size'] or 0)/1e9:8.1f} GB  created={meta[d]['created']}")
        except Exception as e:  # noqa: BLE001 — keep any prior cache value on failure
            log(f"  {d}  API error ({e}); {'using cache' if d in cached else 'skipping'}")
    CACHE.mkdir(exist_ok=True)
    cache_path.write_text(json.dumps(meta, indent=2))
    return meta


# ─────────────────────────── world map ───────────────────────────
def load_land(offline: bool) -> str:
    ASSETS.mkdir(exist_ok=True)
    local = ASSETS / "ne_110m_land.geojson"
    if not local.exists():
        if offline:
            sys.exit("ERROR: --offline but " + str(local) + " is missing")
        log("• downloading Natural Earth land polygons")
        local.write_bytes(fetch(LAND_URL, timeout=120))
    geo = json.loads(local.read_text())

    def project(lon, lat):
        return (lon + 180) / 360 * MAP_W, (90 - lat) / 180 * MAP_H

    def ring_path(ring):
        pts, last = [], None
        for lon, lat in ring:
            x, y = project(lon, lat)
            p = (round(x, 1), round(y, 1))
            if p != last:
                pts.append(p)
                last = p
        if len(pts) < 3:
            return ""
        return "M" + "L".join(f"{x},{y}" for x, y in pts) + "Z"

    paths = []
    for feat in geo["features"]:
        g = feat["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            for ring in poly:
                d = ring_path(ring)
                if d:
                    paths.append(d)
    return " ".join(paths)


def _region_points(regions: list[dict], coords: dict) -> list[dict]:
    """Project a list of {region, bytes} onto map coordinates, dropping
    non-geographic regions and any without known coordinates."""
    points = []
    for r in regions:
        name = r["region"]
        if name in SKIP_REGIONS:
            continue
        c = coords.get(name)
        if not c or c.get("latitude") in (None, "None"):
            continue
        points.append(
            {
                "n": name,
                "lat": float(c["latitude"]),
                "lon": float(c["longitude"]),
                "bytes": r["bytes"],
                "cloud": name.startswith(CLOUD_PREFIXES),
            }
        )
    return points


def build_map(content: Path, regions: list[dict], present: list[str], offline: bool) -> dict:
    coords = yaml.safe_load((content / "region_codes_to_coordinates.yaml").read_text())
    combined = _region_points(regions, coords)
    by_id = {}
    for d in present:
        rows = [
            {"region": r["region"], "bytes": int(r["bytes_sent"])}
            for r in read_tsv(content / "summaries" / d / "by_region.tsv")
        ]
        rows.sort(key=lambda x: -x["bytes"])
        by_id[d] = _region_points(rows, coords)
    return {"land": load_land(offline), "W": MAP_W, "H": MAP_H, "points": combined, "byId": by_id}


# ───────────────────────────── timeline ─────────────────────────────
def build_timeline(content: Path, per: dict, meta: dict) -> dict:
    top = sorted(per, key=lambda d: -per[d]["bytes"])[:TOP_N_TIMELINE]

    series, all_months = {}, set()
    for d in top:
        mm = defaultdict(int)
        for r in read_tsv(content / "summaries" / d / "by_day.tsv"):
            m = r["date"][:7]
            mm[m] += int(r["bytes_sent"])
            all_months.add(m)
        series[d] = mm

    months = month_range(min(all_months), max(all_months))
    matrix = [{"m": m, **{d: series[d].get(m, 0) for d in top}} for m in months]
    created = {d: (meta.get(d, {}) or {}).get("created") for d in top}
    return {"top6": top, "months": months, "matrix": matrix, "created": created}


# ─────────────────────────── metadata (JS) ───────────────────────────
KEY_MAP = {"pi": "pi", "modality": "mod", "region": "reg",
           "species": "sp", "year": "yr", "status": "st", "release": "rel"}


def build_meta(datasets: list[dict]):
    order = [d["id"] for d in datasets]
    meta_js = {}
    for d in datasets:
        entry = {}
        for src, dst in KEY_MAP.items():
            if src in d and d[src] is not None:
                entry[dst] = d[src]
        meta_js[d["id"]] = entry
    return order, meta_js


# ─────────────────────────────── render ───────────────────────────────
def render(template: str, subs: dict) -> str:
    out = template
    for key, val in subs.items():
        out = out.replace(key, val)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--content", help="path to access-summaries content/ directory")
    ap.add_argument("--download", action="store_true", help="download access-summaries tarball if content/ not found")
    ap.add_argument("--offline", action="store_true", help="use cached DANDI API + land data; no network")
    ap.add_argument("--datasets", default=str(ROOT / "datasets.yaml"))
    ap.add_argument("--template", default=str(ROOT / "template.html"))
    ap.add_argument("--out", default=str(ROOT / "index.html"))
    args = ap.parse_args()

    datasets = yaml.safe_load(Path(args.datasets).read_text())["datasets"]
    order, meta_js = build_meta(datasets)

    content = resolve_content(args.content, args.download)
    present, data = build_access(content, order)
    log(f"• {len(present)}/{len(order)} datasets have access data")

    dmeta = dandi_meta(present, args.offline)
    sizes = {d: dmeta[d]["size"] for d in present if dmeta.get(d, {}).get("size") is not None}
    timeline = build_timeline(content, data["per"], dmeta)
    mapdata = build_map(content, data["regions"], present, args.offline)

    template = Path(args.template).read_text()
    html = render(
        template,
        {
            "__DATA__": json.dumps(data),
            "__SIZES__": json.dumps(sizes),
            "__TIMELINE__": json.dumps(timeline),
            "__MAP__": json.dumps(mapdata),
            "__META__": json.dumps(meta_js, ensure_ascii=False),
            "__ORDER__": json.dumps(order),
        },
    )
    for placeholder in ("__DATA__", "__SIZES__", "__TIMELINE__", "__MAP__", "__META__", "__ORDER__"):
        if placeholder in html:
            sys.exit(f"ERROR: placeholder {placeholder} left unfilled in template")

    Path(args.out).write_text(html)
    log(f"✓ wrote {args.out} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
