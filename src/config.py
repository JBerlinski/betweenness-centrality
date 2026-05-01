"""
Parametry konfiguracyjne pipeline'u analizy centralności.
Zmień tutaj ustawienia zamiast modyfikować kod główny.
"""

# ── Ścieżki ──────────────────────────────────────────────────────────────────
INPUT_LINES = "data/raw/lines.shp"
INPUT_NODES = "data/raw/nodes.shp"
OUTPUT_GPKG = "output/wyniki_centralnosci.gpkg"

# ── Układ współrzędnych ───────────────────────────────────────────────────────
CRS_PROJECTED = "EPSG:2180"

# ── Tolerancje geometryczne ───────────────────────────────────────────────────
SNAP_TOLERANCE      = 5.0   # [m] snap linii do stacji
LINE_SNAP_TOLERANCE = 0.5   # [m] łatanie mikro-szczelin OSM
COORD_PRECISION     = 1     # zaokrąglenie klucza węzła (10 cm)

# ── Centralność ───────────────────────────────────────────────────────────────
BC_EXACT    = True   # True = dokładny, False = przybliżony
BC_K_APPROX = 300    # liczba próbek przy przybliżonym BC

# ── Tryb ważenia ──────────────────────────────────────────────────────────────
WEIGHTED = True      # True = wagi AHP, False = graf nieważony

# ── Parametry graniczne ───────────────────────────────────────────────────────
BOUNDARY_MARGIN_PCT  = 0.04  # 4% rozpiętości X/Y → strefa graniczna
DEDUP_LENGTH_TOL_PCT = 0.02  # 2% tolerancja długości przy deduplikacji

# ── Kolumny eksportu ──────────────────────────────────────────────────────────
NODE_COLS_EXPORT = [
    "join_name",
    "join_power",
    "join_volta",
    "join_subst",
    "join_opera",
    "join_ref",
]

LINE_COLS_EXPORT = [
    "osm_id",
    "name",
    "voltage",
    "operator",
    "cables",
    "length",
]