"""
=============================================================================
ANALIZA KRYTYCZNOŚCI INFRASTRUKTURY ENERGETYCZNEJ  –  v4
=============================================================================
Dane wejściowe:
  - lines.shp  : linie energetyczne (OSM, EPSG:2180 / ETRF2000-PL CS92)
  - nodes.shp  : stacje transformatorowe i elektrownie (EPSG:2180)

Pipeline:
  load_data
    → snap_lines_to_nodes
    → snap_endpoints_to_endpoints
    → [v4 NOWE] expand_voltage_circuits   ← rozdzielenie wielonapięciowych linii
    → build_raw_graph
    → classify_nodes
    → simplify_and_merge_edges
    → multiply_circuits
    → deduplicate_edges
    → compute_centrality
    → export_results

Zmiany v4 względem v3:
  [v4-1] expand_voltage_circuits()    – NOWA funkcja przed budową grafu.
         Każdy rekord OSM z voltage="110000;220000" jest rozbijany na dwa
         osobne rekordy jednonapięciowe z właściwie rozdzielonymi kablami.
         Eliminuje voltage_str zawierające ";". Graf widzi wyłącznie
         atomowe napięcia (110000, 220000, 400000).

  [v4-2] build_raw_graph()            – usuwa logikę 'circuits'.
         Jedyne źródło informacji o liczbie obwodów: cables // 3.
         Flaga circuit_explicit usunięta jako zbędna.

  [v4-3] simplify_and_merge_edges()   – uproszczone scalanie napięć.
         Po expand_voltage_circuits krawędzie przy tym samym junction mają
         zawsze jedno napięcie. Merging łańcuchowy pozostaje, ale nie buduje
         już łańcuchów "v1;v2".

  [v4-4] deduplicate_edges()          – usunięto circuit_explicit.
         expected_count = max(num_circuits) w grupie (zawsze z cables).

  [v4-5] LINE_COLS_EXPORT             – usunięto 'circuits'.
=============================================================================
"""

import time
import warnings
from collections import Counter, defaultdict

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import Point, LineString
from shapely.ops import linemerge
from shapely.strtree import STRtree

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# KONFIGURACJA
# ─────────────────────────────────────────────────────────────────────────────

INPUT_LINES = "lines.shp"
INPUT_NODES = "nodes.shp"
OUTPUT_GPKG = "wyniki_centralnosci.gpkg"

CRS_PROJECTED = "EPSG:2180"

SNAP_TOLERANCE      = 5.0   # [m] snap linii do stacji
LINE_SNAP_TOLERANCE = 0.5   # [m] łatanie mikro-szczelin OSM
COORD_PRECISION     = 1     # zaokrąglenie klucza węzła (10 cm)

BC_EXACT    = True
BC_K_APPROX = 300

BOUNDARY_MARGIN_PCT  = 0.04   # 4 % rozpiętości X/Y → strefa graniczna
DEDUP_LENGTH_TOL_PCT = 0.02   # 2 % tolerancja długości przy deduplikacji

NODE_COLS_EXPORT = [
    "join_name",
    "join_power",
    "join_volta",
    "join_subst",
    "join_opera",
    "join_ref",
]

# [v4-5] Usunięto 'circuits' – nie używamy tego tagu
LINE_COLS_EXPORT = [
    "osm_id",
    "name",
    "voltage",
    "operator",
    "cables",
    "length",
]


# ─────────────────────────────────────────────────────────────────────────────
# KROK 1 – WCZYTANIE DANYCH
# ─────────────────────────────────────────────────────────────────────────────

def load_data(lines_path: str, nodes_path: str) -> tuple:
    print("=" * 60)
    print("KROK 1: Wczytywanie danych")
    print("=" * 60)

    lines = gpd.read_file(lines_path)
    nodes = gpd.read_file(nodes_path)

    print(f"  lines.shp: {len(lines)} rekordów, CRS: {lines.crs}")
    print(f"  nodes.shp: {len(nodes)} rekordów, CRS: {nodes.crs}")

    lines = lines.set_crs(CRS_PROJECTED) if lines.crs is None else lines.to_crs(CRS_PROJECTED)
    nodes = nodes.set_crs(CRS_PROJECTED) if nodes.crs is None else nodes.to_crs(CRS_PROJECTED)

    nodes = nodes.copy()
    nodes.geometry = nodes.geometry.apply(
        lambda g: g.centroid if g.geom_type != "Point" else g
    )

    lines = lines[lines.geometry.notna() & (lines.geometry.length > 0)].copy().reset_index(drop=True)
    nodes = nodes[nodes.geometry.notna()].copy().reset_index(drop=True)

    print(f"  Po filtracji: {len(lines)} linii, {len(nodes)} stacji")

    if "voltage" in lines.columns:
        print("\n  Napięcia linii (RAW – przed rozdzieleniem):")
        for v, c in lines["voltage"].value_counts().items():
            print(f"    {v or '(brak)'}V: {c} linii")

    if "join_power" in nodes.columns:
        print("\n  Typy węzłów:")
        for p, c in nodes["join_power"].value_counts().items():
            print(f"    {p or '(brak)'}: {c} węzłów")

    return lines, nodes


# ─────────────────────────────────────────────────────────────────────────────
# KROK 2 – SNAP LINII DO STACJI
# ─────────────────────────────────────────────────────────────────────────────

def snap_lines_to_nodes(lines: gpd.GeoDataFrame,
                        nodes: gpd.GeoDataFrame,
                        tolerance: float) -> gpd.GeoDataFrame:
    print("\n" + "=" * 60)
    print("KROK 2: Snap linii do stacji")
    print("=" * 60)
    print(f"  Tolerancja: {tolerance}m")

    node_geoms = list(nodes.geometry)
    node_tree  = STRtree(node_geoms)
    snapped_count = 0
    snapped_geoms = []

    for line in lines.geometry:
        coords = list(line.coords)
        changed = False
        start_snapped = None

        for end_idx in [0, -1]:
            pt = Point(coords[end_idx])
            candidates = node_tree.query(pt.buffer(tolerance))

            best_dist, best_node = float("inf"), None
            for ci in candidates:
                d = pt.distance(node_geoms[ci])
                if d < best_dist and d < tolerance:
                    best_dist, best_node = d, node_geoms[ci]

            if best_node is not None:
                nc = (best_node.x, best_node.y)
                if end_idx == -1 and start_snapped == nc:
                    continue  # zabezpieczenie: nie zwijaj linii do punktu
                coords[end_idx] = nc
                changed = True
                if end_idx == 0:
                    start_snapped = nc

        if changed:
            snapped_count += 1
        snapped_geoms.append(LineString(coords))

    result = lines.copy()
    result.geometry = snapped_geoms
    print(f"  Dosnapowano: {snapped_count}/{len(lines)} linii")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# KROK 2B – ŁATANIE MIKRO-SZCZELIN
# ─────────────────────────────────────────────────────────────────────────────

def snap_endpoints_to_endpoints(lines: gpd.GeoDataFrame, tolerance: float) -> gpd.GeoDataFrame:
    print("\n" + "=" * 60)
    print("KROK 2B: Łatanie mikro-szczelin między liniami")
    print("=" * 60)
    print(f"  Tolerancja: {tolerance}m")

    geoms   = list(lines.copy().geometry)
    snapped = 0

    for i in range(len(geoms)):
        coords_i = list(geoms[i].coords)
        changed  = False

        for end_i in [0, -1]:
            pt_i = Point(coords_i[end_i])
            best_dist, best_coord = float("inf"), None

            for j in range(len(geoms)):
                if i == j:
                    continue
                coords_j = list(geoms[j].coords)
                for end_j in [0, -1]:
                    d = pt_i.distance(Point(coords_j[end_j]))
                    if 0.001 < d < tolerance and d < best_dist:
                        best_dist  = d
                        best_coord = coords_j[end_j]

            if best_coord is not None:
                coords_i[end_i] = best_coord
                changed = True

        if changed:
            geoms[i] = LineString(coords_i)
            snapped += 1

    result = lines.copy()
    result.geometry = geoms
    print(f"  Zespawano mikro-szczeliny w {snapped} liniach.")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# KROK 2C – ROZDZIELENIE WIELONAPIĘCIOWYCH LINII  [NOWE w v4 – v4-1]
#
# Problem źródłowy:
#   OSM taguje wspólną trasę dwóch różnych napięć jako:
#       voltage = "110000;220000", cables = 6
#   co oznacza fizycznie: 3 kable 110 kV + 3 kable 220 kV na tych samych
#   słupach. To nie jest "jedna linia dwunapięciowa" – to DWA osobne obwody.
#
# Co robimy:
#   Każdy rekord z ";" w voltage rozbijamy na n_voltages osobnych rekordów.
#   Każdy nowy rekord ma:
#       voltage = pojedyncze napięcie (np. "110000")
#       cables  = cables_total / n_voltages  (zaokrąglone do wielokrotności 3)
#
# Logika podziału kabli:
#   1. Jeśli cables jest znane i cables // n_voltages >= 3:
#         cables_per_v = (cables // n_voltages) // 3 * 3  (wielokrotność 3)
#         → Standardowy przypadek: 6 kabli, 2 napięcia → 3 kable każde
#   2. Jeśli cables nieznane LUB podział daje < 3:
#         cables_per_v = 3  (zakładamy 1 obwód 3-fazowy na napięcie, log warning)
#
# Po tej funkcji ŻADEN rekord nie ma ";" w voltage.
# build_raw_graph widzi wyłącznie atomowe wartości: "110000", "220000", "400000".
#
# Miejsce w pipeline: po snap_endpoints_to_endpoints, PRZED build_raw_graph.
# ─────────────────────────────────────────────────────────────────────────────

def expand_voltage_circuits(lines: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    print("\n" + "=" * 60)
    print("KROK 2C: Rozdzielenie wielonapięciowych linii  [v4]")
    print("=" * 60)

    rows_single   = []   # rekordy bez ";" w voltage – przechodzą bez zmian
    rows_expanded = []   # rekordy po rozbiciu wielonapięciowych
    ambiguous_cnt = 0    # licznik przypadków z niejasnym podziałem kabli

    for idx, row in lines.iterrows():
        raw_voltage = str(row.get("voltage", "") or "").replace(" ", "")

        # Brak separatora → rekord atomowy, przepuszczamy bez zmian
        if ";" not in raw_voltage:
            rows_single.append(row)
            continue

        # ── Parsowanie napięć ─────────────────────────────────────────────────
        voltage_parts = [v.strip() for v in raw_voltage.split(";") if v.strip()]
        # Odrzuć powtórzenia (np. "110000;110000" to błąd tagowania, nie 2 napięcia)
        voltage_parts = list(dict.fromkeys(voltage_parts))   # zachowuje kolejność
        n_v = len(voltage_parts)

        if n_v < 2:
            # Po odfiltrowaniu duplikatów tylko jedno napięcie – traktuj jako atomowy
            row_copy = row.copy()
            row_copy["voltage"] = voltage_parts[0] if voltage_parts else raw_voltage
            rows_single.append(row_copy)
            continue

        # ── Ustalenie liczby kabli na napięcie ───────────────────────────────
        cables_raw = row.get("cables", None)
        cables_total = None
        if cables_raw is not None and str(cables_raw).strip().isdigit():
            cables_total = int(cables_raw)

        if cables_total is not None and cables_total >= n_v * 3:
            # Podział równomierny – zaokrąglamy w dół do wielokrotności 3
            cables_per_v_raw = cables_total // n_v
            cables_per_v = max(3, (cables_per_v_raw // 3) * 3)
            if cables_per_v_raw % 3 != 0:
                ambiguous_cnt += 1
                print(f"    ⚠ idx={idx}: cables={cables_total} / {n_v} napięcia "
                      f"= {cables_per_v_raw:.1f} – zaokrąglono do {cables_per_v}")
        else:
            # Kable nieznane lub za mało – zakładamy 1 obwód (3 kable) na napięcie
            cables_per_v = 3
            if cables_total is not None and cables_total < n_v * 3:
                ambiguous_cnt += 1
                print(f"    ⚠ idx={idx}: cables={cables_total} < {n_v}×3 – "
                      f"przypisano {cables_per_v} kabli na napięcie (domyślnie)")

        # ── Generowanie nowych rekordów ───────────────────────────────────────
        for volt in voltage_parts:
            new_row = row.copy()
            new_row["voltage"] = volt
            new_row["cables"]  = cables_per_v
            rows_expanded.append(new_row)

    # Złożenie wynikowego GeoDataFrame
    all_rows   = rows_single + rows_expanded
    result_gdf = gpd.GeoDataFrame(all_rows, crs=lines.crs).reset_index(drop=True)

    # Raport
    n_orig     = len(lines)
    n_result   = len(result_gdf)
    n_split    = n_result - n_orig + len(rows_expanded) // max(1, len(rows_expanded)) * 0
    expanded_records = len(rows_expanded)
    original_multi   = n_orig - len(rows_single)

    print(f"  Rekordy wejściowe:        {n_orig}")
    print(f"  Rekordy wielonapięciowe:  {original_multi}")
    print(f"  Rekordy po rozbiciu:      {expanded_records}  "
          f"(+{expanded_records - original_multi} nowych)")
    print(f"  Rekordy wyjściowe łącznie:{n_result}")
    if ambiguous_cnt:
        print(f"  ⚠ Przypadki niejasnego podziału kabli: {ambiguous_cnt} "
              f"(sprawdź logi powyżej)")

    if "voltage" in result_gdf.columns:
        print("\n  Napięcia po rozdzieleniu (atomowe):")
        for v, c in result_gdf["voltage"].value_counts().items():
            semicolon_flag = " ← ⚠ NADAL WIELONAPIĘCIOWE" if ";" in str(v) else ""
            print(f"    {v or '(brak)'}V: {c} linii{semicolon_flag}")

    return result_gdf


def coord_key(x: float, y: float) -> tuple:
    return (round(x, COORD_PRECISION), round(y, COORD_PRECISION))


# ─────────────────────────────────────────────────────────────────────────────
# KROK 3 – BUDOWA SUROWEGO GRAFU
# [v4-2] Usunięto logikę 'circuits'. Jedyne źródło: cables // 3.
#         Usunięto circuit_explicit – wszystkie dane z kabli są równoważne.
#         Jedna krawędź per rekord (po expand), mnożenie po kontrakcji.
# ─────────────────────────────────────────────────────────────────────────────

def build_raw_graph(lines: gpd.GeoDataFrame) -> nx.MultiGraph:
    print("\n" + "=" * 60)
    print("KROK 3: Budowa surowego grafu  [v4: wyłącznie cables, bez circuits]")
    print("=" * 60)

    G = nx.MultiGraph()

    for idx, row in lines.iterrows():
        line   = row.geometry
        coords = list(line.coords)

        start_key = coord_key(*coords[0])
        end_key   = coord_key(*coords[-1])

        if start_key == end_key:
            continue

        if not G.has_node(start_key):
            G.add_node(start_key, x=coords[0][0], y=coords[0][1])
        if not G.has_node(end_key):
            G.add_node(end_key, x=coords[-1][0], y=coords[-1][1])

        edge_attrs = {
            "geometry" : line,
            "weight"   : line.length,
            "length_m" : line.length,
        }
        for col in LINE_COLS_EXPORT:
            if col in lines.columns:
                edge_attrs[col] = row.get(col, None)

        # voltage_str: po expand_voltage_circuits zawsze atomowe (bez ";")
        v = str(row.get("voltage", "")).replace(" ", "")
        edge_attrs["voltage_str"] = v if v and v != "None" else "brak"

        # ── [v4-2] Liczenie obwodów: wyłącznie cables // 3 ──────────────────
        # circuits jest ignorowany zgodnie ze specyfikacją v4.
        # Jeśli cables nieznane → zakładamy 1 obwód (expand już ustawił cables=3
        # dla wielonapięciowych; dla jednonapięciowych z brakiem cables → 1).
        cables_val = row.get("cables", None)
        if cables_val is not None and str(cables_val).strip().isdigit():
            cables_int   = int(cables_val)
            num_circuits = max(1, cables_int // 3)
        else:
            num_circuits = 1

        edge_attrs["num_circuits"] = num_circuits

        # Jedna krawędź per rekord – mnożenie w multiply_circuits po kontrakcji
        G.add_edge(start_key, end_key, **edge_attrs)

    print(f"  Surowy graf: {G.number_of_nodes()} węzłów, {G.number_of_edges()} segmentów.")
    _print_voltage_stats(G)
    return G


def _print_voltage_stats(G: nx.MultiGraph) -> None:
    """Pomocnicza: podsumowanie napięć w grafie (debug)."""
    v_counter: Counter = Counter()
    semicolon_edges = 0
    for _, _, data in G.edges(data=True):
        vs = data.get("voltage_str", "brak")
        v_counter[vs] += 1
        if ";" in vs:
            semicolon_edges += 1
    print(f"  Rozkład napięć w grafie:")
    for v, c in v_counter.most_common():
        flag = "  ← ⚠ WIELONAPIĘCIOWE" if ";" in v else ""
        print(f"    {v}: {c} krawędzi{flag}")
    if semicolon_edges:
        print(f"  ⚠ Krawędzi z ';' w voltage_str: {semicolon_edges} "
              f"– sprawdź dane wejściowe!")
    else:
        print(f"  ✓ Brak krawędzi wielonapięciowych – poprawna atomizacja.")


# ─────────────────────────────────────────────────────────────────────────────
# KROK 4 – KONTRAKCJA WĘZŁÓW TECHNICZNYCH
# [v4-3] Scalanie napięć uproszczone: krawędzie przy TECHNICAL_JUNCTION
#         mają zawsze atomowe voltage_str. Porównujemy przez ==.
#         Jeśli napięcia nie pasują (defensywnie) → łączymy bez ";".
# ─────────────────────────────────────────────────────────────────────────────

def simplify_and_merge_edges(G: nx.MultiGraph, node_types: dict) -> nx.MultiGraph:
    print("\n" + "=" * 60)
    print("KROK 4: Kontrakcja węzłów technicznych  [v4: atomowe napięcia]")
    print("=" * 60)

    G_simple = G.copy()
    junctions = [n for n, t in node_types.items() if t == "TECHNICAL_JUNCTION"]

    contracted_count  = 0
    voltage_mismatch  = 0   # licznik defensywny

    for junc in junctions:
        if junc not in G_simple:
            continue

        unique_neighbors = list(set(G_simple.neighbors(junc)))

        if len(unique_neighbors) != 2:
            continue

        n1, n2 = unique_neighbors[0], unique_neighbors[1]

        edges_n1 = list(G_simple[junc][n1].values())
        edges_n2 = list(G_simple[junc][n2].values())

        # [v4-3] num_circuits: MAX z obu stron (zachowanie z v3)
        max_circuits = max(
            max((e.get("num_circuits", 1) for e in edges_n1), default=1),
            max((e.get("num_circuits", 1) for e in edges_n2), default=1),
        )

        e1 = edges_n1[0]
        e2 = edges_n2[0]

        new_len = e1.get("length_m", 0) + e2.get("length_m", 0)

        # [v4-3] Scalanie napięć – atomowe wartości, proste porównanie
        v1 = e1.get("voltage_str", "brak")
        v2 = e2.get("voltage_str", "brak")
        if v1 == v2:
            new_v = v1
        else:
            # Defensywnie: nie powinno się zdarzyć (różne napięcia → STACJA_OSM_GAP)
            # Jeśli jednak tak jest, logujemy i scala jako "v1;v2" tylko w razie konieczności
            voltage_mismatch += 1
            new_v = v1 if v1 != "brak" else v2  # preferuj sensowne napięcie

        try:
            merged_geom = linemerge([e1["geometry"], e2["geometry"]])
            if merged_geom.geom_type == "MultiLineString":
                merged_geom = LineString(
                    list(e1["geometry"].coords) + list(e2["geometry"].coords)
                )
        except Exception:
            merged_geom = e1["geometry"]

        G_simple.add_edge(
            n1, n2,
            geometry     = merged_geom,
            weight       = new_len,
            length_m     = new_len,
            voltage_str  = new_v,
            num_circuits = max_circuits,
        )

        G_simple.remove_node(junc)
        contracted_count += 1

    print(f"  Skontraktowano: {contracted_count} węzłów technicznych.")
    if voltage_mismatch:
        print(f"  ⚠ Niezgodności napięć przy kontrakcji: {voltage_mismatch} "
              f"– mogą wskazywać na brakujące stacje w nodes.shp")
    print(f"  Graf po kontrakcji: {G_simple.number_of_nodes()} węzłów, "
          f"{G_simple.number_of_edges()} krawędzi.")
    return G_simple


# ─────────────────────────────────────────────────────────────────────────────
# KROK 4B – MULTIPLIKACJA OBWODÓW
# Bez zmian logicznych względem v3 – ale teraz num_circuits pochodzi
# wyłącznie z cables // 3 (żadnego circuit_explicit).
# ─────────────────────────────────────────────────────────────────────────────

def multiply_circuits(G: nx.MultiGraph) -> nx.MultiGraph:
    print("\n" + "=" * 60)
    print("KROK 4B: Multiplikacja fizycznych obwodów")
    print("=" * 60)

    edges_to_add = []
    for u, v, key, data in G.edges(keys=True, data=True):
        n = data.get("num_circuits", 1)
        for _ in range(n - 1):   # mamy już 1, dodajemy n-1 kopii
            edges_to_add.append((u, v, dict(data)))

    for u, v, data in edges_to_add:
        G.add_edge(u, v, **data)

    added = len(edges_to_add)
    print(f"  Dodano {added} krawędzi z multiplikacji (cables // 3 > 1).")
    print(f"  Graf po multiplikacji: {G.number_of_nodes()} węzłów, "
          f"{G.number_of_edges()} fizycznych obwodów.")
    return G


# ─────────────────────────────────────────────────────────────────────────────
# KROK 4C – DEDUPLIKACJA LOGICZNA
# [v4-4] Usunięto circuit_explicit.
#         expected_count = max(num_circuits) w grupie (zawsze z cables//3).
#         Grupowanie wg (para węzłów, voltage_str) – voltage_str jest teraz
#         zawsze atomowe, więc "110000" i "220000" to osobne grupy.
# ─────────────────────────────────────────────────────────────────────────────

def deduplicate_edges(G: nx.MultiGraph,
                      length_tol_pct: float = DEDUP_LENGTH_TOL_PCT
                      ) -> tuple:
    print("\n" + "=" * 60)
    print("KROK 4C: Deduplikacja logicznych duplikatów  [v4: bez circuit_explicit]")
    print("=" * 60)

    groups: dict = defaultdict(list)
    for u, v, key, data in G.edges(keys=True, data=True):
        pair    = tuple(sorted([u, v]))
        voltage = data.get("voltage_str", "brak")
        groups[(pair, voltage)].append((u, v, key, data))

    removed_total = 0

    for (pair, voltage), edge_list in groups.items():
        if len(edge_list) <= 1:
            continue

        # [v4-4] expected_count: ile równoległych krawędzi tego napięcia
        # powinno być między tą parą węzłów?
        # = max deklarowany num_circuits (z cables // 3) w grupie
        expected_count = max(e[3].get("num_circuits", 1) for e in edge_list)

        # Sortuj po długości – najkrótsze są "bazowe"
        edge_list_sorted = sorted(edge_list, key=lambda e: e[3].get("length_m", 0))
        ref_len  = edge_list_sorted[0][3].get("length_m", 1.0)

        kept    = 0
        to_del  = []

        for u_, v_, key_, data_ in edge_list_sorted:
            length   = data_.get("length_m", 0)
            diff_pct = abs(length - ref_len) / ref_len if ref_len > 0 else 0

            if kept < expected_count:
                kept += 1
                continue  # zachowaj tyle krawędzi ile deklarują obwody

            if diff_pct > length_tol_pct:
                # Wyraźnie różna długość → inna trasa fizyczna, nie duplikat
                kept += 1
                continue

            to_del.append((u_, v_, key_))

        for u_, v_, key_ in to_del:
            if G.has_edge(u_, v_, key_):
                G.remove_edge(u_, v_, key_)
                removed_total += 1

    print(f"  Usunięto {removed_total} logicznych duplikatów krawędzi.")
    print(f"  Graf po deduplikacji: {G.number_of_nodes()} węzłów, "
          f"{G.number_of_edges()} obwodów.")
    return G, removed_total


# ─────────────────────────────────────────────────────────────────────────────
# KROK 5 – KLASYFIKACJA WĘZŁÓW
# Bez zmian logicznych. get_primary_voltages() uproszczone:
# voltage_str jest teraz atomowe, split(";")[0] zwróci zawsze całą wartość.
# ─────────────────────────────────────────────────────────────────────────────

def classify_nodes(G: nx.MultiGraph,
                   nodes_gdf: gpd.GeoDataFrame,
                   tolerance: float) -> tuple:
    print("\n" + "=" * 60)
    print("KROK 5: Klasyfikacja węzłów")
    print("=" * 60)

    all_x  = [G.nodes[n]["x"] for n in G.nodes()]
    all_y  = [G.nodes[n]["y"] for n in G.nodes()]
    xmin, xmax = min(all_x), max(all_x)
    ymin, ymax = min(all_y), max(all_y)
    x_span = xmax - xmin
    y_span = ymax - ymin
    bm     = BOUNDARY_MARGIN_PCT

    def is_near_extent_boundary(gx: float, gy: float) -> bool:
        return (
            gx < xmin + x_span * bm or gx > xmax - x_span * bm or
            gy < ymin + y_span * bm or gy > ymax - y_span * bm
        )

    def get_node_voltages(node) -> set:
        """
        Zwraca zbiór atomowych napięć krawędzi węzła.
        Po expand_voltage_circuits voltage_str jest zawsze atomowe,
        więc split nie jest już potrzebny – zostawiamy dla bezpieczeństwa.
        """
        voltages = set()
        for _, _, data in G.edges(node, data=True):
            vs = data.get("voltage_str", "")
            if not vs or vs == "brak":
                continue
            # Defensywnie: split na ";" jeśli wyjątkowo przetrwał separator
            for part in vs.split(";"):
                part = part.strip()
                if part.isdigit():
                    voltages.add(part)
        return voltages

    station_geoms    = list(nodes_gdf.geometry)
    station_tree     = STRtree(station_geoms)
    matched_stations: set = set()
    node_types: dict = {}
    node_attrs: dict = {}

    for graph_node in G.nodes():
        gx  = G.nodes[graph_node]["x"]
        gy  = G.nodes[graph_node]["y"]
        pt  = Point(gx, gy)

        degree           = G.degree(graph_node)
        unique_neighbors = len(set(G.neighbors(graph_node)))

        # ── PRIORYTET 1: Stacja z nodes.shp ──────────────────────────────────
        candidates = station_tree.query(pt.buffer(tolerance))
        best_dist, best_idx = float("inf"), None
        for ci in candidates:
            d = pt.distance(station_geoms[ci])
            if d < best_dist and d < tolerance:
                best_dist, best_idx = d, ci

        if best_idx is not None:
            node_types[graph_node] = "STACJA"
            matched_stations.add(best_idx)
            row   = nodes_gdf.iloc[best_idx]
            attrs = {col: row[col] for col in NODE_COLS_EXPORT if col in nodes_gdf.columns}
            attrs["snap_dist_m"] = round(best_dist, 2)
            node_attrs[graph_node] = attrs
            continue

        # ── PRIORYTET 2: Stopień 1 → BOUNDARY_DUMMY ──────────────────────────
        if degree == 1:
            node_types[graph_node] = "BOUNDARY_DUMMY"
            node_attrs[graph_node] = {}
            continue

        # ── PRIORYTET 3: Strefa graniczna → BOUNDARY_DUMMY ───────────────────
        if is_near_extent_boundary(gx, gy) and unique_neighbors <= 2:
            node_types[graph_node] = "BOUNDARY_DUMMY"
            node_attrs[graph_node] = {"_boundary_heuristic": "geographic"}
            continue

        # ── PRIORYTET 4: Różne napięcia → STACJA_OSM_GAP ────────────────────
        # Po expand_voltage_circuits krawędzie mają atomowe napięcia.
        # Węzeł łączący krawędzie 110kV i 220kV to fizycznie transformator.
        node_voltages = get_node_voltages(graph_node)
        if len(node_voltages) > 1 and unique_neighbors >= 2:
            v_label = "/".join(sorted(node_voltages))
            node_types[graph_node] = "STACJA_OSM_GAP"
            node_attrs[graph_node] = {
                "join_name"  : f"⚠ Stacja niezidentyfikowana [{v_label} kV]",
                "join_power" : "substation_implied",
                "join_volta" : v_label,
                "_boundary_heuristic": "voltage_discontinuity",
            }
            continue

        # ── PRIORYTET 5: Węzeł techniczny (domyślnie) ────────────────────────
        node_types[graph_node] = "TECHNICAL_JUNCTION"
        node_attrs[graph_node] = {}

    counts = Counter(node_types.values())
    print(f"  STACJA:              {counts.get('STACJA', 0):>4}")
    print(f"  STACJA_OSM_GAP:      {counts.get('STACJA_OSM_GAP', 0):>4}  [transformatory niezident.]")
    print(f"  BOUNDARY_DUMMY:      {counts.get('BOUNDARY_DUMMY', 0):>4}")
    print(f"  TECHNICAL_JUNCTION:  {counts.get('TECHNICAL_JUNCTION', 0):>4}  (pomijane w wynikach)")

    unmatched = len(nodes_gdf) - len(matched_stations)
    if unmatched > 0:
        print(f"\n  ⚠  {unmatched}/{len(nodes_gdf)} stacji z nodes.shp nie dopasowano do grafu!")
        print(f"     Rozważ zwiększenie SNAP_TOLERANCE (teraz: {tolerance}m).")

    return node_types, node_attrs


# ─────────────────────────────────────────────────────────────────────────────
# KROK 6 – CENTRALNOŚĆ
# Bez zmian. Pełny MultiGraph → stopnie fizyczne.
# Uproszczony Graph → BC i CC (najkrótsze ścieżki).
# ─────────────────────────────────────────────────────────────────────────────

def compute_centrality(G: nx.MultiGraph) -> dict:
    print("\n" + "=" * 60)
    print("KROK 6: Obliczanie centralności")
    print("=" * 60)

    G_calc = nx.Graph()
    for u, v, data in G.edges(data=True):
        if G_calc.has_edge(u, v):
            if data["weight"] < G_calc[u][v]["weight"]:
                G_calc[u][v].update(data)
        else:
            G_calc.add_edge(u, v, **data)

    components = list(nx.connected_components(G_calc))
    main_comp  = max(components, key=len)

    print(f"  MultiGraph (fizyczne obwody): {G.number_of_nodes()} węzłów, {G.number_of_edges()} obwodów")
    print(f"  Graf trasowania (BC/CC):      {G_calc.number_of_nodes()} węzłów, {G_calc.number_of_edges()} tras")
    print(f"  Komponentów: {len(components)}, główny: {len(main_comp)} węzłów "
          f"({100 * len(main_comp) / G_calc.number_of_nodes():.1f}%)")

    bc_all, cc_all = {}, {}
    dc_all = {node: G.degree(node) for node in G.nodes()}

    for i, comp in enumerate(components):
        subG = G_calc.subgraph(comp).copy()
        n    = len(comp)

        if n < 2:
            for node in comp:
                bc_all[node] = 0.0
                cc_all[node] = 0.0
            continue

        if BC_EXACT:
            if i == 0:
                t0 = time.time()
                print(f"  BC dokładny ({n} węzłów)... ", end="", flush=True)
            bc_sub = nx.betweenness_centrality(subG, weight="weight", normalized=True)
            if i == 0:
                print(f"gotowe ({time.time() - t0:.1f}s)")
        else:
            k = min(n, BC_K_APPROX)
            bc_sub = nx.betweenness_centrality(subG, k=k, weight="weight", normalized=True)

        bc_all.update(bc_sub)
        cc_all.update(nx.closeness_centrality(subG, distance="weight"))

    print("  DC, CC: zakończone")
    return {"betweenness": bc_all, "closeness": cc_all, "degree_centrality": dc_all}


# ─────────────────────────────────────────────────────────────────────────────
# KROK 7 – EKSPORT WYNIKÓW
# Bez zmian logicznych. STACJA_OSM_GAP nadal w rankingu.
# ─────────────────────────────────────────────────────────────────────────────

def export_results(G: nx.MultiGraph,
                   node_types: dict,
                   node_attrs: dict,
                   centrality: dict,
                   planar: gpd.GeoDataFrame,
                   crs: str,
                   output_path: str) -> gpd.GeoDataFrame:
    print("\n" + "=" * 60)
    print("KROK 7: Eksport wyników")
    print("=" * 60)

    EXPORT_TYPES  = {"STACJA", "BOUNDARY_DUMMY", "STACJA_OSM_GAP"}
    RANKING_TYPES = {"STACJA", "STACJA_OSM_GAP"}

    records_main: list = []
    records_all:  list = []

    for node, ntype in node_types.items():
        if node not in G:
            continue

        gx = G.nodes[node]["x"]
        gy = G.nodes[node]["y"]

        rec = {
            "geometry"         : Point(gx, gy),
            "node_id"          : f"{gx:.1f}_{gy:.1f}",
            "typ_wezla"        : ntype,
            "betweenness"      : round(centrality["betweenness"].get(node, 0), 8),
            "closeness"        : round(centrality["closeness"].get(node, 0), 6),
            "degree_centrality": round(centrality["degree_centrality"].get(node, 0), 6),
            "stopien_grafu"    : G.degree(node),
        }

        extra = node_attrs.get(node, {})
        rec["nazwa"]       = extra.get("join_name",  "")
        rec["typ"]         = extra.get("join_power", "")
        rec["napiecie"]    = extra.get("join_volta", "")
        rec["typ_stacji"]  = extra.get("join_subst", "")
        rec["operator"]    = extra.get("join_opera", "")
        rec["ref"]         = extra.get("join_ref",   "")
        rec["snap_dist_m"] = extra.get("snap_dist_m", None)
        rec["heurystyka"]  = extra.get("_boundary_heuristic", "")

        records_all.append(rec.copy())
        if ntype in EXPORT_TYPES:
            records_main.append(rec)

    main_gdf = gpd.GeoDataFrame(records_main, crs=crs)

    ranking_mask = main_gdf["typ_wezla"].isin(RANKING_TYPES)
    main_gdf["rank_betweenness"] = None
    main_gdf["rank_closeness"]   = None

    if ranking_mask.any():
        ridx = main_gdf[ranking_mask].index
        main_gdf.loc[ridx, "rank_betweenness"] = (
            main_gdf.loc[ridx, "betweenness"]
            .rank(ascending=False, method="min").astype(int)
        )
        main_gdf.loc[ridx, "rank_closeness"] = (
            main_gdf.loc[ridx, "closeness"]
            .rank(ascending=False, method="min").astype(int)
        )

    main_gdf = main_gdf.sort_values("betweenness", ascending=False).reset_index(drop=True)
    all_gdf  = gpd.GeoDataFrame(records_all, crs=crs)
    all_gdf  = all_gdf.sort_values("betweenness", ascending=False).reset_index(drop=True)

    main_gdf.to_file(output_path, layer="wezly_krytyczne", driver="GPKG")
    all_gdf.to_file(output_path,  layer="wszystkie_wezly", driver="GPKG")
    planar.to_file(output_path,   layer="linie_sieci",     driver="GPKG")

    n_stacje   = (main_gdf["typ_wezla"] == "STACJA").sum()
    n_gap      = (main_gdf["typ_wezla"] == "STACJA_OSM_GAP").sum()
    n_boundary = (main_gdf["typ_wezla"] == "BOUNDARY_DUMMY").sum()

    print(f"\n  Wyeksportowano do: {output_path}")
    print(f"  Warstwa 'wezly_krytyczne': {len(main_gdf)} węzłów")
    print(f"    - STACJA:            {n_stacje}")
    print(f"    - STACJA_OSM_GAP:    {n_gap}")
    print(f"    - BOUNDARY_DUMMY:    {n_boundary}")
    print(f"  Warstwa 'wszystkie_wezly': {len(all_gdf)} węzłów")
    print(f"  Warstwa 'linie_sieci':     {len(planar)} segmentów")

    print("\n  TOP 15 STACJI wg Betweenness:")
    print("  " + "-" * 72)
    top15 = main_gdf[main_gdf["typ_wezla"].isin(RANKING_TYPES)].head(15)
    for _, row in top15.iterrows():
        name = row["nazwa"] or row["node_id"]
        rank = row["rank_betweenness"]
        tag  = " ⚠OSM_GAP" if row["typ_wezla"] == "STACJA_OSM_GAP" else ""
        print(f"  #{int(rank):>3}  BC={row['betweenness']:.6f}  "
              f"CC={row['closeness']:.4f}  deg={row['stopien_grafu']}"
              f"  | {name[:40]}{tag}")

    return main_gdf


# ─────────────────────────────────────────────────────────────────────────────
# GŁÓWNA PROCEDURA
# ─────────────────────────────────────────────────────────────────────────────

def main():
    t_start = time.time()

    print("\n" + "=" * 60)
    print("ANALIZA KRYTYCZNOŚCI SIECI ENERGETYCZNEJ  –  v4")
    print("=" * 60)
    print(f"  Plik linii:        {INPUT_LINES}")
    print(f"  Plik węzłów:       {INPUT_NODES}")
    print(f"  Wynik:             {OUTPUT_GPKG}")
    print(f"  SNAP_TOLERANCE:    {SNAP_TOLERANCE}m")
    print(f"  BOUNDARY_MARGIN:   {BOUNDARY_MARGIN_PCT * 100:.0f}% obszaru")
    print(f"  DEDUP_LENGTH_TOL:  {DEDUP_LENGTH_TOL_PCT * 100:.0f}%")
    print(f"  BC dokładny:       {BC_EXACT}")

    # 1. Wczytanie
    lines, nodes = load_data(INPUT_LINES, INPUT_NODES)

    # 2. Snap do stacji i łatanie szczelin
    lines_snapped = snap_lines_to_nodes(lines, nodes, SNAP_TOLERANCE)
    lines_fixed   = snap_endpoints_to_endpoints(lines_snapped, LINE_SNAP_TOLERANCE)

    # 2C. [v4] Rozdzielenie wielonapięciowych linii PRZED budową grafu
    lines_expanded = expand_voltage_circuits(lines_fixed)

    # 3. Surowy graf – 1 krawędź/segment, tylko cables [v4]
    G_raw = build_raw_graph(lines_expanded)

    # 4. Klasyfikacja węzłów (na surowym grafie)
    node_types, node_attrs = classify_nodes(G_raw, nodes, SNAP_TOLERANCE)

    # 5. Kontrakcja węzłów technicznych
    G_contracted = simplify_and_merge_edges(G_raw, node_types)

    # 6. Multiplikacja obwodów po kontrakcji
    G_multi = multiply_circuits(G_contracted)

    # 7. Deduplikacja logiczna [v4: bez circuit_explicit]
    G_final, n_removed = deduplicate_edges(G_multi)

    # 8. Centralność
    centrality = compute_centrality(G_final)

    # 9. Przygotowanie linii do eksportu
    merged_lines_data = []
    for u, v, data in G_final.edges(data=True):
        merged_lines_data.append({
            "geometry"    : data.get("geometry"),
            "length_m"    : data.get("length_m"),
            "voltage_str" : data.get("voltage_str", "brak"),
            "num_circuits": data.get("num_circuits", 1),
        })
    export_lines_gdf = gpd.GeoDataFrame(merged_lines_data, crs=CRS_PROJECTED)
    export_lines_gdf = export_lines_gdf[export_lines_gdf.geometry.notna()].reset_index(drop=True)

    # 10. Eksport
    result = export_results(
        G_final, node_types, node_attrs, centrality,
        export_lines_gdf, CRS_PROJECTED, OUTPUT_GPKG
    )

    t_total = time.time() - t_start
    print(f"\n{'=' * 60}")
    print(f"  Zakończono w {t_total:.1f}s")
    print(f"  Wynik: {OUTPUT_GPKG}")
    print(f"  QGIS: warstwy 'wezly_krytyczne' i 'linie_sieci'")
    print(f"{'=' * 60}\n")

    return result, G_final


if __name__ == "__main__":
    result, G = main()