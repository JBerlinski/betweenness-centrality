# Analiza centralności pośrednictwa sieci elektroenergetycznej WN

Analiza ważonej centralności pośrednictwa (weighted betweenness centrality)
do identyfikacji węzłów krytycznych w regionalnej sieci przesyłowej wysokiego napięcia.

> 🇬🇧 [English version](README.md)

**Status:** W trakcie realizacji — praca inżynierska, województwo zachodniopomorskie.

---

## Opis projektu

Narzędzie buduje graf topologiczny sieci 110/220/400 kV na podstawie
danych OpenStreetMap (shapefiles) i wyznacza krytyczność węzłów metodą
betweenness centrality. Wagi węzłów wyznaczane są na podstawie ankiety
eksperckiej metodą AHP (Analytic Hierarchy Process).

### Pipeline przetwarzania

load_data → snap_to_nodes → snap_endpoints → expand_voltage_circuits
→ build_raw_graph → classify_nodes → simplify_and_merge_edges
→ multiply_circuits → deduplicate_edges → compute_centrality → export

---

## Dane wejściowe

| Plik | Opis |
|------|------|
| `data/raw/lines.shp` | Linie energetyczne WN (OSM, EPSG:2180) |
| `data/raw/nodes.shp` | Stacje transformatorowe i elektrownie (OSM, EPSG:2180) |

---

## Dane wyjściowe

Wyniki zapisywane są do `output/wyniki_centralnosci.gpkg` w trzech warstwach:

- `wezly_krytyczne` — węzły krytyczne z wartościami centralności i rankingiem
- `wszystkie_wezly` — wszystkie węzły grafu
- `linie_sieci` — uproszczone krawędzie sieci

---

## Uruchomienie

```bash
pip install -r requirements.txt
python src/main.py
```

---

## Konfiguracja

Kluczowe parametry w `src/config.py`:

| Parametr | Domyślnie | Opis |
|----------|-----------|------|
| `SNAP_TOLERANCE` | 5.0 m | Tolerancja przyciągania linii do stacji |
| `BC_EXACT` | True | Dokładny lub przybliżony betweenness |
| `WEIGHTED` | True | Graf ważony (AHP) lub nieważony |

---

## Ważenie metodą AHP

Wagi krytyczności węzłów wyznaczane są na podstawie ankiety eksperckiej
(skala Saaty'ego 1–9). Trzy kryteria: poziom napięcia, typ obiektu
(elektrownia / stacja przesyłowa / GPZ), stopień węzła.

Macierz AHP ładowana jest z zewnętrznego pliku — `weights/ahp_matrix.json` (wkrótce).

---

## Struktura projektu

betweenness-centrality/
├── data/
│   └── raw/          # Dane wejściowe (OSM shapefiles)
├── output/           # Wyniki — nieśledzone przez Git
├── src/              # Kod źródłowy
│   ├── config.py     # Parametry i flagi
│   ├── graph_builder.py
│   ├── centrality.py
│   ├── weights.py    # Logika AHP
│   ├── export.py
│   └── main.py
└── weights/
└── ahp_matrix.json  # Wyniki ankiety eksperckiej

---

## Zależności

- Python 3.13
- GeoPandas
- NetworkX
- Shapely
- Pandas

---

## Autorzy

Jakub Berliński, Bartosz Wróblewski — projekt naukowy, 2026 za pośrednictwem dr inż. Jakub Wabiński