# Betweenness Centrality Analysis of HV Transmission Networks

Weighted betweenness centrality analysis for identifying systemic hot-spots
in regional high-voltage transmission networks.

> 🇵🇱 [Wersja polska](README.pl.md)

**Status:** Work in progress — Student research project, Zachodniopomorskie region, Poland.

---

## Overview

This tool builds a topological graph of the 110/220/400 kV power grid
from OpenStreetMap shapefiles and computes node criticality using
betweenness centrality. Node weights are derived from expert surveys
via the Analytic Hierarchy Process (AHP).

---
### Pipeline

load_data → snap_to_nodes → snap_endpoints → expand_voltage_circuits
→ build_raw_graph → classify_nodes → simplify_and_merge_edges
→ multiply_circuits → deduplicate_edges → compute_centrality → export

---
## Input Data

| File | Description |
|------|-------------|
| `data/raw/lines.shp` | HV power lines (OSM, EPSG:2180) |
| `data/raw/nodes.shp` | Transformer stations and power plants (OSM, EPSG:2180) |

---
## Output

Results are written to `output/wyniki_centralnosci.gpkg` with three layers:

- `wezly_krytyczne` — critical nodes with centrality scores and rankings
- `wszystkie_wezly` — all graph nodes
- `linie_sieci` — simplified network edges

---
## Usage

```bash
pip install -r requirements.txt
python src/main.py
```

---
## Configuration

Key parameters in `src/config.py`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SNAP_TOLERANCE` | 5.0 m | Snap distance: lines → stations |
| `BC_EXACT` | True | Exact vs. approximate betweenness |
| `WEIGHTED` | True | Use AHP weights or unweighted graph |

---
## AHP Weighting

Node criticality weights are estimated via expert survey (Saaty 9-point scale).
Three criteria: voltage level, object type (power plant / substation / GPZ),
node degree.

AHP matrix is loaded from an external file — see `weights/ahp_matrix.json` (coming soon).

---
## Project Structure

```
betweenness-centrality/
├── data/
│   └── raw/          # Input shapefiles (OSM)
├── output/           # Generated results — not tracked by Git
├── src/              # Source code
│   ├── config.py     # Parameters and flags
│   ├── graph_builder.py
│   ├── centrality.py
│   ├── weights.py    # AHP logic
│   ├── export.py
│   └── main.py
└── weights/
    └── ahp_matrix.json  # Expert survey results
```
---
## Dependencies
- Python 3.13
- GeoPandas
- NetworkX
- Shapely
- Pandas

---
## Authors
```Jakub Berliński, Bartosz Wróblewski — 2026```
