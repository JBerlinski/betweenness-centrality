"""
Obliczanie miar centralności grafu sieci energetycznej.

Moduł udostępnia funkcję compute_centrality, która wyznacza:
  - betweenness centrality (pośrednictwo),
  - closeness centrality (bliskość),
  - degree centrality (stopień węzła – liczba fizycznych obwodów).
"""

import time

import networkx as nx

from config import BC_EXACT, BC_K_APPROX, WEIGHTED


# ─────────────────────────────────────────────────────────────────────────────
# KROK 6 – CENTRALNOŚĆ
# Pełny MultiGraph → stopnie fizyczne.
# Uproszczony Graph → BC i CC (najkrótsze ścieżki).
# ─────────────────────────────────────────────────────────────────────────────

def compute_centrality(G: nx.MultiGraph) -> dict:
    """Oblicza betweenness, closeness i degree centrality dla wszystkich węzłów.

    Buduje uproszczony graf trasowania (bez równoległych krawędzi, zachowując
    najkrótszą), po czym wyznacza miary centralności osobno dla każdego
    komponentu spójnego. Tryb ważenia krawędzi (``weight="weight"`` lub
    ``weight=None``) sterowany jest flagą ``WEIGHTED`` z ``config.py``.

    Args:
        G: Multigraf sieci energetycznej po deduplikacji. Krawędzie muszą
            posiadać atrybut ``weight`` (długość odcinka w metrach).

    Returns:
        Słownik z kluczami:
            ``betweenness`` (dict): BC znormalizowany {węzeł: float}.
            ``closeness`` (dict): CC ważony długością gdy ``WEIGHTED=True``,
            nieważony gdy ``WEIGHTED=False`` {węzeł: float}.
            ``degree_centrality`` (dict): stopień fizyczny węzła
            (liczba obwodów) w MultiGraph {węzeł: int}.
    """
    print("\n" + "=" * 60)
    print("KROK 6: Obliczanie centralności")
    print("=" * 60)

    # Graf trasowania: usuwa wielokrotne krawędzie, zachowując najkrótszą
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

    # "weight" → BC/CC liczone po długości krawędzi; None → graf nieważony
    weight_attr = "weight" if WEIGHTED else None
    print(f"  Tryb ważenia:                 {'ważony (length_m)' if WEIGHTED else 'nieważony'}")

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
            bc_sub = nx.betweenness_centrality(subG, weight=weight_attr, normalized=True)
            if i == 0:
                print(f"gotowe ({time.time() - t0:.1f}s)")
        else:
            k = min(n, BC_K_APPROX)
            bc_sub = nx.betweenness_centrality(subG, k=k, weight=weight_attr, normalized=True)

        bc_all.update(bc_sub)
        cc_all.update(nx.closeness_centrality(subG, distance=weight_attr))

    print("  DC, CC: zakończone")
    return {"betweenness": bc_all, "closeness": cc_all, "degree_centrality": dc_all}
