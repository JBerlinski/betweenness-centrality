"""
Ważenie węzłów metodą AHP (Analytic Hierarchy Process).

Moduł dostarcza trzy funkcje:
  - load_ahp_config    – wczytuje plik konfiguracyjny JSON z wagami AHP,
  - compute_node_score – oblicza znormalizowany wynik węzła,
  - apply_criticality  – wyznacza Critical Index (CI) = BC * node_score.

Mapowania napięć (voltage_scores) i typów obiektów (type_scores) pochodzą
wyłącznie z pliku JSON wskazanego przez AHP_CONFIG_PATH w config.py.
"""

import json
import os
import warnings

import geopandas as gpd
import networkx as nx


# ─────────────────────────────────────────────────────────────────────────────
# WCZYTANIE KONFIGURACJI
# ─────────────────────────────────────────────────────────────────────────────

def load_ahp_config(path: str) -> dict | None:
    """Wczytuje konfigurację AHP z pliku JSON.

    Ustawia klucz ``weighted`` w zwracanym słowniku na podstawie obecności
    wartości ``null`` w sekcji ``weights``.

    Args:
        path: Ścieżka do pliku ``ahp_matrix.json``.

    Returns:
        Słownik konfiguracji z dodanym kluczem ``weighted`` (bool),
        lub ``None`` gdy plik nie istnieje.
    """
    print("\n" + "=" * 60)
    print("KROK 11: Wskaźnik krytyczności AHP")
    print("=" * 60)

    if not os.path.exists(path):
        warnings.warn(
            f"Plik konfiguracji AHP nie istnieje: {path}  "
            "– wskaźnik krytyczności = BC (bez ważenia węzłów).",
            stacklevel=2,
        )
        return None

    with open(path, encoding="utf-8") as f:
        config = json.load(f)

    weights   = config.get("weights", {})
    null_keys = [k for k, v in weights.items() if v is None]

    if null_keys:
        config["weighted"] = False
        print(f"  ⚠ Wagi null dla: {null_keys} – tryb nieważony (CI = BC).")
    else:
        config["weighted"] = True
        w = weights
        print(
            f"  ✓ Wagi AHP załadowane: "
            f"voltage={w['voltage']}, "
            f"object_type={w['object_type']}, "
            f"degree={w['degree']}  – tryb ważony (CI = BC × node_score)."
        )

    return config


# ─────────────────────────────────────────────────────────────────────────────
# WYNIK WĘZŁA
# ─────────────────────────────────────────────────────────────────────────────

def compute_node_score(
    node_attrs: dict,
    ahp_config: dict,
    norm_degree: float,
) -> float:
    """Oblicza znormalizowany wynik węzła wg konfiguracji AHP.

    Napięcie i typ obiektu normalizowane są przez tablice ``voltage_scores``
    i ``type_scores`` z JSON. Jeśli wartość nie pasuje do żadnego klucza,
    przyjmowany jest wynik 0.  Stopień węzła jest już znormalizowany
    metodą min-max przez wywołującego.

    Args:
        node_attrs: Słownik atrybutów węzła; oczekiwane klucze:
            ``napiecie`` (str) i ``typ`` (str).
        ahp_config: Słownik konfiguracji zwrócony przez ``load_ahp_config``.
        norm_degree: Stopień węzła po normalizacji min-max ∈ [0, 1].

    Returns:
        Wynik AHP: ``w1*norm_voltage + w2*norm_type + w3*norm_degree``.
    """
    weights        = ahp_config["weights"]
    voltage_scores = ahp_config.get("voltage_scores", {})
    type_scores    = ahp_config.get("type_scores", {})

    w1 = weights["voltage"]
    w2 = weights["object_type"]
    w3 = weights["degree"]

    # Normalizacja napięcia
    # Pole może zawierać kilka napięć rozdzielonych ";" – próbujemy kolejno.
    voltage_raw  = str(node_attrs.get("napiecie", "") or "").strip()
    norm_voltage = voltage_scores.get(voltage_raw)
    if norm_voltage is None:
        for part in voltage_raw.split(";"):
            norm_voltage = voltage_scores.get(part.strip())
            if norm_voltage is not None:
                break
    if norm_voltage is None:
        norm_voltage = 0.0

    # Normalizacja typu obiektu (bez rozróżnienia wielkości liter)
    type_raw  = str(node_attrs.get("typ", "") or "").strip().lower()
    norm_type = type_scores.get(type_raw, 0.0)

    return w1 * norm_voltage + w2 * norm_type + w3 * norm_degree


# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL INDEX (CI)
# ─────────────────────────────────────────────────────────────────────────────

def apply_criticality(
    gdf: gpd.GeoDataFrame,
    G: nx.MultiGraph,
    ahp_config: dict | None,
) -> gpd.GeoDataFrame:
    """Oblicza Critical Index (CI) i dodaje kolumnę ``ci`` do GDF.

    Gdy ważenie jest wyłączone (``weighted=False`` lub ``ahp_config is None``),
    CI jest równe betweenness centrality. Gdy włączone:
    ``CI = betweenness * node_score``, gdzie ``node_score`` to wynik AHP
    węzła wyznaczony przez ``compute_node_score``.

    Normalizacja min-max stopnia węzła liczona jest po całym zbiorze wierszy
    przekazanego GeoDataFrame.

    Args:
        gdf: GeoDataFrame warstwy ``wezly_krytyczne``
            (wynik funkcji ``export_results``).
        G: Graf końcowy – przekazywany dla potencjalnych rozszerzeń;
            atrybuty pobierane są z kolumn ``gdf``.
        ahp_config: Słownik konfiguracji AHP lub ``None``.

    Returns:
        Kopia GeoDataFrame z dodaną kolumną ``ci``.
    """
    gdf     = gdf.copy()
    weighted = ahp_config is not None and ahp_config.get("weighted", False)

    if not weighted:
        gdf["ci"] = gdf["betweenness"]
        print("  Critical Index (CI): CI = BC (brak ważenia AHP).")
        return gdf

    # Normalizacja min-max stopnia węzła po całym zbiorze
    degrees   = gdf["stopien_grafu"].astype(float)
    deg_min   = degrees.min()
    deg_max   = degrees.max()
    deg_range = deg_max - deg_min

    scores = []
    for _, row in gdf.iterrows():
        norm_degree = (
            float(row["stopien_grafu"] - deg_min) / deg_range
            if deg_range > 0 else 0.0
        )
        node_attrs = {
            "napiecie": row.get("napiecie", ""),
            "typ":      row.get("typ",      ""),
        }
        scores.append(compute_node_score(node_attrs, ahp_config, norm_degree))

    gdf["ci"] = [
        float(bc) * s for bc, s in zip(gdf["betweenness"].tolist(), scores)
    ]

    ci_max = gdf["ci"].max()
    print(
        f"  Critical Index (CI): CI = BC × node_score  "
        f"(max CI = {ci_max:.6f})."
    )
    return gdf
