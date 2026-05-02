"""
Główny punkt wejścia pipeline'u analizy krytyczności sieci energetycznej.

Uruchomienie:
    python src/main.py

Pipeline v4:
    load_data
      → snap_lines_to_nodes
      → snap_endpoints_to_endpoints
      → expand_voltage_circuits
      → build_raw_graph
      → classify_nodes
      → simplify_and_merge_edges
      → multiply_circuits
      → deduplicate_edges
      → compute_centrality
      → export_results
"""

import time

import geopandas as gpd

from config import (
    INPUT_LINES,
    INPUT_NODES,
    OUTPUT_GPKG,
    CRS_PROJECTED,
    SNAP_TOLERANCE,
    LINE_SNAP_TOLERANCE,
    BOUNDARY_MARGIN_PCT,
    DEDUP_LENGTH_TOL_PCT,
    BC_EXACT,
    WEIGHTED,
)
from graph_builder import (
    load_data,
    snap_lines_to_nodes,
    snap_endpoints_to_endpoints,
    expand_voltage_circuits,
    build_raw_graph,
    classify_nodes,
    simplify_and_merge_edges,
    multiply_circuits,
    deduplicate_edges,
)
from centrality import compute_centrality
from export import export_results


def main():
    """Uruchamia pełny pipeline analizy centralności sieci energetycznej."""
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
    print(f"  BC ważone:         {WEIGHTED}")

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
