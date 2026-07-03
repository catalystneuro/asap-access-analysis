# ASAP Dandiset download analysis

A single-page, self-contained dashboard of DANDI download/streaming statistics
for the ASAP-affiliated Dandisets — data volume, reach vs. volume, per-dataset
timelines, a world map, and geographic breakdown.

The output is one file, [`index.html`](index.html), with **all data inlined** and
**no external dependencies** — open it directly or host it as a static page
(GitHub Pages, S3, Netlify, …).

## Rebuild

```bash
pip install -r requirements.txt

# Uses ../access-summaries/content if present; otherwise pass --content or --download
python build.py
```

Common variants:

```bash
python build.py --content /path/to/access-summaries/content   # explicit data dir
python build.py --download                                    # fetch the published tarball
python build.py --offline                                     # no network; use cached API + map data
```

`build.py` writes `index.html`; open it in a browser to view.

## Data sources

| Input | Source |
|-------|--------|
| Download / streaming stats (per day, region, asset) | [`dandi/access-summaries`](https://github.com/dandi/access-summaries) `content/` |
| Dataset storage size + creation date | [DANDI REST API](https://api.dandiarchive.org) |
| World-map land outline | [Natural Earth 110m](https://github.com/nvkelso/natural-earth-vector) (cached in `assets/`) |
| Curated per-dataset metadata (PI, modality, region, species, status) | [`datasets.yaml`](datasets.yaml) |

Only Dandisets with recorded download traffic appear in the data-driven charts.
Those still awaiting publication (embargoed/draft with no public traffic) are
listed in the "Status of datasets" section.

## Files

| File | Role |
|------|------|
| `build.py` | Generator — pulls the data and renders the template |
| `template.html` | HTML/CSS/JS with `__DATA__`-style placeholders |
| `datasets.yaml` | Curated dataset list + metadata (edit this to change datasets/labels) |
| `assets/ne_110m_land.geojson` | Cached world land polygons |
| `.cache/` | Cached DANDI API responses and (optionally) the access-summaries tarball |
| `index.html` | **Generated output** — the dashboard |

## Design

Charts follow a validated, colorblind-safe categorical palette; the page supports
light and dark mode (`prefers-color-scheme`) and includes hover tooltips, a data
table, and per-region detail. The geographic view is modeled on the
[DANDI usage page](https://github.com/dandi/usage-page).
