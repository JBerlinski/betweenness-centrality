"""
Budowa grafu sieci energetycznej na podstawie danych geometrycznych.

Moduł zawiera funkcje odpowiedzialne za:
  - wczytanie danych wejściowych (GeoDataFrame linii i węzłów),
  - przyciąganie (snap) geometrii do stacji,
  - rozdzielenie linii wielonapięciowych na atomowe rekordy,
  - budowę surowego grafu, klasyfikację węzłów,
  - kontrakcję węzłów technicznych, multiplikację obwodów i deduplikację krawędzi.
"""

import warnings
from collections import Counter, defaultdict

import geopandas as gpd
import networkx as nx
from shapely.geometry import Point, LineString
from shapely.ops import linemerge
from shapely.strtree import STRtree

from config import (
    CRS_PROJECTED,
    COORD_PRECISION,
    BOUNDARY_MARGIN_PCT,
    DEDUP_LENGTH_TOL_PCT,
    NODE_COLS_EXPORT,
    LINE_COLS_EXPORT,
)

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────────────────────
# KROK 1 – WCZYTANIE DANYCH
# ─────────────────────────────────────────────────────────────────────────────

def load_data(lines_path: str, nodes_path: str) -> tuple:
    """Wczytuje dane przestrzenne linii i węzłów, reprojekcjonuje do CRS_PROJECTED."""
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
    """Przyciąga końce linii do najbliższej stacji w obrębie podanej tolerancji."""
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
    """Łata mikro-szczeliny między końcami sąsiednich linii."""
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
    """Rozbija rekordy wielonapięciowe (voltage z ";") na atomowe rekordy jednonapięciowe."""
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
    n_orig           = len(lines)
    n_result         = len(result_gdf)
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
    """Zwraca zaokrąglony klucz współrzędnych węzła."""
    return (round(x, COORD_PRECISION), round(y, COORD_PRECISION))


# ─────────────────────────────────────────────────────────────────────────────
# KROK 3 – BUDOWA SUROWEGO GRAFU
# [v4-2] Usunięto logikę 'circuits'. Jedyne źródło: cables // 3.
#         Usunięto circuit_explicit – wszystkie dane z kabli są równoważne.
#         Jedna krawędź per rekord (po expand), mnożenie po kontrakcji.
# ─────────────────────────────────────────────────────────────────────────────

def build_raw_graph(lines: gpd.GeoDataFrame) -> nx.MultiGraph:
    """Buduje surowy multigraf z GeoDataFrame linii – jedna krawędź na rekord OSM."""
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
    """Pomocnicza: wyświetla rozkład napięć krawędzi grafu (diagnostyka)."""
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
    """Kontraktuje węzły techniczne (stopień 2) scalając sąsiednie krawędzie."""
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
# KROK 5 – KLASYFIKACJA WĘZŁÓW
# Bez zmian logicznych. get_primary_voltages() uproszczone:
# voltage_str jest teraz atomowe, split(";")[0] zwróci zawsze całą wartość.
# ─────────────────────────────────────────────────────────────────────────────

def classify_nodes(G: nx.MultiGraph,
                   nodes_gdf: gpd.GeoDataFrame,
                   tolerance: float) -> tuple:
    """Klasyfikuje węzły grafu na: STACJA, BOUNDARY_DUMMY, STACJA_OSM_GAP, TECHNICAL_JUNCTION."""
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
        """Zwraca zbiór atomowych napięć krawędzi węzła."""
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
        # Węzeł łączący krawędzie 110 kV i 220 kV to fizycznie transformator.
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
# KROK 4B – MULTIPLIKACJA OBWODÓW
# num_circuits pochodzi wyłącznie z cables // 3 (żadnego circuit_explicit).
# ─────────────────────────────────────────────────────────────────────────────

def multiply_circuits(G: nx.MultiGraph) -> nx.MultiGraph:
    """Dodaje kopie krawędzi reprezentujące dodatkowe fizyczne obwody (cables // 3 > 1)."""
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
    """Usuwa logiczne duplikaty krawędzi przekraczające deklarowaną liczbę obwodów."""
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

        # [v4-4] expected_count: ile równoległych krawędzi tego napięcia powinno być
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
