"""
Eksport wyników analizy centralności do pliku GeoPackage.

Moduł zawiera funkcję export_results, która zapisuje trzy warstwy:
  - wezly_krytyczne : stacje i węzły graniczne z miarami centralności,
  - wszystkie_wezly : wszystkie węzły grafu (łącznie z technicznymi),
  - linie_sieci     : uproszczone geometrie linii po kontrakcji.
"""

import geopandas as gpd
import networkx as nx
from shapely.geometry import Point


# ─────────────────────────────────────────────────────────────────────────────
# KROK 7 – EKSPORT WYNIKÓW
# STACJA_OSM_GAP nadal w rankingu.
# ─────────────────────────────────────────────────────────────────────────────

def export_results(G: nx.MultiGraph,
                   node_types: dict,
                   node_attrs: dict,
                   centrality: dict,
                   planar: gpd.GeoDataFrame,
                   crs: str,
                   output_path: str) -> gpd.GeoDataFrame:
    """
    Zapisuje wyniki do pliku GeoPackage i wyświetla ranking TOP 15 stacji.

    Parametry
    ---------
    G           : graf po deduplikacji,
    node_types  : słownik {węzeł: typ_węzła},
    node_attrs  : słownik {węzeł: atrybuty z nodes.shp},
    centrality  : słownik zwrócony przez compute_centrality,
    planar      : GeoDataFrame krawędzi do warstwy linie_sieci,
    crs         : kod EPSG układu wynikowego,
    output_path : ścieżka do pliku .gpkg.

    Zwraca GeoDataFrame warstwy 'wezly_krytyczne'.
    """
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

    # ── Oznaczenie stacji graniczących wyłącznie z węzłami BOUNDARY_DUMMY ────
    # Stacja otoczona tylko węzłami granicznymi leży na obrzeżu obszaru
    # i jej centralność jest artefaktem przycięcia danych, nie rzeczywistą
    # wartością – wyklucz ją z rankingu.
    def _wszyscy_sasiedzi_boundary(node) -> bool:
        """Zwraca True jeśli każdy sąsiad węzła to BOUNDARY_DUMMY."""
        sasiedzi = list(G.neighbors(node))
        if not sasiedzi:
            return False
        return all(node_types.get(s) == "BOUNDARY_DUMMY" for s in sasiedzi)

    boundary_adjacent_nodes: set = set()
    for node, ntype in node_types.items():
        if ntype == "STACJA" and _wszyscy_sasiedzi_boundary(node):
            boundary_adjacent_nodes.add(node)

    # Dopasuj flagę do wierszy GeoDataFrame przez node_id
    boundary_adjacent_ids = {
        f"{G.nodes[n]['x']:.1f}_{G.nodes[n]['y']:.1f}"
        for n in boundary_adjacent_nodes
        if n in G
    }
    main_gdf["boundary_adjacent"] = main_gdf["node_id"].isin(boundary_adjacent_ids)

    # Ranking tylko dla stacji niegranicznych
    ranking_mask = (
        main_gdf["typ_wezla"].isin(RANKING_TYPES) &
        ~main_gdf["boundary_adjacent"]
    )
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

    n_boundary_adj = main_gdf["boundary_adjacent"].sum()

    print(f"\n  Wyeksportowano do: {output_path}")
    print(f"  Warstwa 'wezly_krytyczne': {len(main_gdf)} węzłów")
    print(f"    - STACJA:            {n_stacje}")
    print(f"    - STACJA_OSM_GAP:    {n_gap}")
    print(f"    - BOUNDARY_DUMMY:    {n_boundary}")
    print(f"    - boundary_adjacent: {n_boundary_adj}  [wykluczone z rankingu]")
    print(f"  Warstwa 'wszystkie_wezly': {len(all_gdf)} węzłów")
    print(f"  Warstwa 'linie_sieci':     {len(planar)} segmentów")

    print("\n  TOP 15 STACJI wg Betweenness:")
    print("  " + "-" * 72)
    top15 = main_gdf[main_gdf["typ_wezla"].isin(RANKING_TYPES)].head(15)
    for _, row in top15.iterrows():
        name = row["nazwa"] or row["node_id"]
        rank = row["rank_betweenness"]
        # boundary_adjacent: rank jest NULL – oznacz jako wykluczoną z rankingu
        rank_str = f"#{int(rank):>3}" if rank is not None else "excl"
        tag  = " ⚠OSM_GAP" if row["typ_wezla"] == "STACJA_OSM_GAP" else ""
        tag += " ⚠BOUNDARY_ADJ" if row["boundary_adjacent"] else ""
        print(f"  {rank_str}  BC={row['betweenness']:.6f}  "
              f"CC={row['closeness']:.4f}  deg={row['stopien_grafu']}"
              f"  | {name[:40]}{tag}")

    return main_gdf
