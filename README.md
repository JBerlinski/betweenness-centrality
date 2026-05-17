# CritGrid — Identification of Critical Nodes in High-Voltage Power Networks Using Graph Analysis and the AHP Method

Weighted betweenness centrality analysis for identifying systemic hot-spots
in regional high-voltage transmission networks.

> 🇵🇱 [Wersja polska](README.pl.md)

**Status:** Work in progress — Student research project, Zachodniopomorskie region, Poland.

---

## Overview

CritGrid builds a topological graph of the 110/220/400 kV power grid from
OpenStreetMap shapefiles and computes node criticality using betweenness
centrality. A composite criticality index `CI` is derived by multiplying BC
by a weighted node score obtained via the Analytic Hierarchy Process (AHP).

---

## Pipeline

```
load_data → snap_to_nodes → snap_endpoints → expand_voltage_circuits
→ build_raw_graph → classify_nodes → simplify_and_merge_edges
→ multiply_circuits → deduplicate_edges → compute_centrality
→ export_results → apply_criticality (AHP)
```

Steps 1–10 build the graph and compute raw centrality measures.
Step 11 multiplies betweenness centrality by the AHP node score to produce
the composite criticality index `CI`, stored in the `ci` column.

---

## Input Data

| File | Description |
|------|-------------|
| `data/raw/lines.shp` | HV power lines (OSM, EPSG:2180) |
| `data/raw/nodes.shp` | Transformer stations and power plants (OSM, EPSG:2180) |

> **Note:** Input shapefiles are manually pre-processed before running the
> pipeline. Pre-processing steps include converting station polygons to point
> centroids, merging stations in close proximity, and correcting line
> geometries to ensure topological continuity. Automation of this step is
> planned for a future release.

---

## Output

Results are written to `output/wyniki_centralnosci.gpkg` with three layers:

| Layer | Contents |
|-------|----------|
| `wezly_krytyczne` | Substations and boundary nodes with BC, CC, degree, ranking and `ci` (Critical Index) |
| `wszystkie_wezly` | All graph nodes including technical junctions |
| `linie_sieci` | Simplified network edges with voltage and circuit count |

---

## Usage

```bash
pip install -r requirements.txt
python src/main.py
```

---

## Configuration

All parameters are centralised in `src/config.py` — edit there instead of
touching pipeline code.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SNAP_TOLERANCE` | 5.0 m | Snap distance: line endpoints → stations |
| `LINE_SNAP_TOLERANCE` | 0.5 m | Gap patching between adjacent line endpoints |
| `BC_EXACT` | `True` | Exact (`True`) vs. approximate (`False`) betweenness |
| `BC_K_APPROX` | 300 | Pivot nodes for approximate BC |
| `WEIGHTED` | `True` | Weight BC by edge length (`True`) or unweighted graph (`False`) |
| `BOUNDARY_MARGIN_PCT` | 4 % | Border zone width as fraction of spatial extent |
| `AHP_CONFIG_PATH` | `data/weights/ahp_matrix.json` | Path to AHP weights and score mappings |

---

## Criticality Score

Each substation receives a composite criticality index:

```
CI = BC × f(voltage, object_type, degree)
```

where `f()` is a weighted sum of normalised node features:

```
f = w₁ · norm_voltage + w₂ · norm_type + w₃ · norm_degree
```

| Symbol | Meaning |
|--------|---------|
| **CI** | Critical Index — final composite score stored in column `ci` |
| **BC** | Betweenness centrality (normalised, 0–1) |
| **norm_voltage** | Voltage level score from `voltage_scores` mapping in JSON |
| **norm_type** | Object type score from `type_scores` mapping in JSON |
| **norm_degree** | Node degree, min-max normalised across the whole dataset |
| **w₁, w₂, w₃** | AHP weights from `data/weights/ahp_matrix.json` |

The `ci` column in the `wezly_krytyczne` layer holds the final score.
When any AHP weight is `null`, the tool falls back to `CI = BC` automatically
and prints a warning — no code changes required.

---

## AHP Configuration

Weights and score mappings are stored in `data/weights/ahp_matrix.json`.

### Filling in weights after the expert survey

Once survey results are available, replace the `null` values in the `weights`
block with the derived priorities (values must sum to 1.0):

```json
{
  "weights": {
    "voltage":     0.5,
    "object_type": 0.3,
    "degree":      0.2
  },
  "voltage_scores": { ... },
  "type_scores":    { ... }
}
```

As long as any weight remains `null`, the pipeline automatically runs in
unweighted mode (`CI = BC`) and logs which weights are missing.

### Default score mappings

**Voltage level** (`voltage_scores`):

| Voltage (V) | Score |
|-------------|-------|
| 110 000 | 0.333 |
| 220 000 | 0.667 |
| 400 000 | 1.000 |

**Object type** (`type_scores`):

| Type | Score |
|------|-------|
| generator | 1.00 |
| substation | 0.66 |
| distribution | 0.33 |

All mappings are loaded exclusively from the JSON file — no values are
hard-coded in the source. Add or rename keys in the JSON to adapt the
model to new voltage levels or object types.

---

## Project Structure

```
betweenness-centrality/
├── data/
│   ├── raw/                        # Input shapefiles (OSM)
│   │   ├── lines.shp               # HV power lines
│   │   └── nodes.shp               # Substations and power plants
│   └── weights/
│       └── ahp_matrix.json         # AHP weights and node score mappings
├── output/                         # Generated results — not tracked by Git
├── src/
│   ├── config.py                   # All parameters and file paths
│   ├── graph_builder.py            # Graph construction pipeline (steps 1–4C)
│   ├── centrality.py               # Betweenness, closeness and degree centrality
│   ├── weights.py                  # AHP Critical Index (CI) scoring (step 11)
│   ├── export.py                   # GeoPackage export (step 10)
│   └── main.py                     # Pipeline entry point
└── requirements.txt
```

---

## Dependencies

| Package | Version |
|---------|---------|
| Python | 3.13 |
| GeoPandas | 1.1 |
| NetworkX | 3.6 |
| Shapely | 2.1 |
| Pandas | 3.0 |
| pyogrio | 0.12 |
| pyproj | 3.7 |
| rtree | 1.4 |
| NumPy | 2.2 |

---

## Authors
```
Jakub Berliński, Bartosz Wróblewski — 2026
```
