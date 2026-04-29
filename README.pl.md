# Analiza centralności pośrednictwa sieci elektroenergetycznej WN

Analiza ważonej centralności pośrednictwa (ang. *weighted betweenness centrality*)
służąca do identyfikacji newralgicznych węzłów (tzw. *hot-spots*) w regionalnej
sieci przesyłowej wysokiego napięcia.

> 🇬🇧 [English version](README.md)

**Status:** W trakcie realizacji — studencki projekt naukowy, województwo zachodniopomorskie.

---
## Opis projektu

Narzędzie buduje graf topologiczny sieci 110/220/400 kV na podstawie
danych przestrzennych OpenStreetMap (shapefiles) i wyznacza krytyczność węzłów
przy użyciu miary centralności pośrednictwa. Wagi węzłów określane są 
na podstawie ankiet eksperckich z wykorzystaniem metody AHP (Analytic Hierarchy Process).

---
### Etapy przetwarzania

load_data → snap_to_nodes → snap_endpoints → expand_voltage_circuits
→ build_raw_graph → classify_nodes → simplify_and_merge_edges
→ multiply_circuits → deduplicate_edges → compute_centrality → export

---
## Dane wejściowe

| Plik | Opis |
|------|------|
| `data/raw/lines.shp` | Linie elektroenergetyczne WN (OSM, EPSG:2180) |
| `data/raw/nodes.shp` | Stacje transformatorowe i elektrownie (OSM, EPSG:2180) |

---
## Dane wyjściowe

Wyniki zapisywane są do pliku `output/wyniki_centralnosci.gpkg` w postaci trzech warstw:

- `wezly_krytyczne` — węzły o największym znaczeniu, z wartościami centralności i rankingiem
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

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SNAP_TOLERANCE` | 5.0 m | Tolerancja dociągania (snapowania) linii do stacji |
| `BC_EXACT` | True | Dokładne vs. przybliżone wyznaczanie centralności |
| `WEIGHTED` | True | Uwzględnianie wag AHP w grafie (True) lub graf nieważony (False) |

---
## Ważenie metodą AHP

Wagi krytyczności węzłów wyznaczane są na podstawie ocen ekspertów
(9-stopniowa skala Saaty'ego). Pod uwagę brane są trzy kryteria: poziom napięcia,
typ obiektu (elektrownia / stacja węzłowa / GPZ) oraz stopień węzła.

Macierz AHP wczytywana jest z zewnętrznego pliku — weights/ahp_matrix.json (wkrótce).

---
## Struktura projektu

```
betweenness-centrality/
├── data/
│   └── raw/          # Dane wejściowe (OSM shapefiles)
├── output/           # Wyniki — ignorowane przez Git
├── src/              # Kod źródłowy
│   ├── config.py     # Parametry i flagi
│   ├── graph_builder.py
│   ├── centrality.py
│   ├── weights.py    # Logika metody AHP
│   ├── export.py
│   └── main.py
└── weights/
    └── ahp_matrix.json  # Wyniki ankiet eksperckich
```

---
## Zależności
- Python 3.13
- GeoPandas
- NetworkX
- Shapely
- Pandas

---
## Autorzy
```Jakub Berliński, Bartosz Wróblewski — 2026```
