# CritGrid — Analiza centralności pośrednictwa sieci elektroenergetycznej WN

Analiza ważonej centralności pośrednictwa (ang. *weighted betweenness centrality*)
służąca do identyfikacji newralgicznych węzłów (tzw. *hot-spots*) w regionalnej
sieci przesyłowej wysokiego napięcia.

> 🇬🇧 [English version](README.md)

**Status:** W trakcie realizacji — studencki projekt naukowy, województwo zachodniopomorskie.

---

## Opis projektu

CritGrid buduje graf topologiczny sieci 110/220/400 kV na podstawie danych
przestrzennych OpenStreetMap (shapefiles) i wyznacza krytyczność węzłów przy
użyciu miary centralności pośrednictwa. Złożony wskaźnik krytyczności `K`
powstaje przez pomnożenie BC przez ważony wynik węzła wyznaczony metodą AHP
(Analytic Hierarchy Process).

---

## Etapy przetwarzania

```
load_data → snap_to_nodes → snap_endpoints → expand_voltage_circuits
→ build_raw_graph → classify_nodes → simplify_and_merge_edges
→ multiply_circuits → deduplicate_edges → compute_centrality
→ export_results → apply_criticality (AHP)
```

Kroki 1–10 budują graf i wyznaczają surowe miary centralności.
Krok 11 mnoży betweenness centrality przez wynik węzła AHP, tworząc złożony
wskaźnik krytyczności `K` zapisywany w kolumnie `krytycznosc`.

---

## Dane wejściowe

| Plik | Opis |
|------|------|
| `data/raw/lines.shp` | Linie elektroenergetyczne WN (OSM, EPSG:2180) |
| `data/raw/nodes.shp` | Stacje transformatorowe i elektrownie (OSM, EPSG:2180) |

---

## Dane wyjściowe

Wyniki zapisywane są do pliku `output/wyniki_centralnosci.gpkg` w postaci trzech warstw:

| Warstwa | Zawartość |
|---------|-----------|
| `wezly_krytyczne` | Stacje i węzły graniczne z BC, CC, stopniem, rankingiem i kolumną `krytycznosc` |
| `wszystkie_wezly` | Wszystkie węzły grafu, w tym węzły techniczne |
| `linie_sieci` | Uproszczone krawędzie sieci z napięciem i liczbą obwodów |

---

## Uruchomienie

```bash
pip install -r requirements.txt
python src/main.py
```

---

## Konfiguracja

Wszystkie parametry zgromadzone są w `src/config.py` — zmiany wprowadzać tam,
bez modyfikowania kodu pipeline'u.

| Parametr | Wartość domyślna | Opis |
|----------|-----------------|------|
| `SNAP_TOLERANCE` | 5,0 m | Tolerancja dociągania końców linii do stacji |
| `LINE_SNAP_TOLERANCE` | 0,5 m | Łatanie mikroszczelin między końcami sąsiednich linii |
| `BC_EXACT` | `True` | Dokładne (`True`) lub przybliżone (`False`) BC |
| `BC_K_APPROX` | 300 | Liczba węzłów pivot przy przybliżonym BC |
| `WEIGHTED` | `True` | Ważenie BC długością krawędzi (`True`) lub graf nieważony (`False`) |
| `BOUNDARY_MARGIN_PCT` | 4 % | Szerokość strefy granicznej jako ułamek rozpiętości obszaru |
| `AHP_CONFIG_PATH` | `data/weights/ahp_matrix.json` | Ścieżka do pliku wag AHP |

---

## Wskaźnik krytyczności

Każda stacja otrzymuje złożony wskaźnik krytyczności:

```
K = BC × f(napięcie, typ_obiektu, stopień_węzła)
```

gdzie `f()` to ważona suma znormalizowanych cech węzła:

```
f = w₁ · norm_napięcie + w₂ · norm_typ + w₃ · norm_stopień
```

| Symbol | Znaczenie |
|--------|-----------|
| **BC** | Betweenness centrality (znormalizowana, 0–1) |
| **norm_napięcie** | Wynik poziomu napięcia wg tablicy `voltage_scores` w JSON |
| **norm_typ** | Wynik typu obiektu wg tablicy `type_scores` w JSON |
| **norm_stopień** | Stopień węzła, znormalizowany metodą min-max w zbiorze |
| **w₁, w₂, w₃** | Wagi AHP z pliku `data/weights/ahp_matrix.json` |

Kolumna `krytycznosc` w warstwie `wezly_krytyczne` zawiera ostateczny wynik.
Gdy dowolna waga AHP jest `null`, program automatycznie przełącza się na tryb
nieważony (`K = BC`) i wyświetla ostrzeżenie — bez konieczności zmian w kodzie.

---

## Konfiguracja AHP

Wagi i tablice punktacji przechowywane są w pliku `data/weights/ahp_matrix.json`.

### Wypełnienie wag po otrzymaniu wyników ankiety

Po uzyskaniu wyników ankiety eksperckiej należy zastąpić wartości `null` w bloku
`weights` wyliczonymi priorytetami (wartości muszą sumować się do 1,0):

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

Dopóki którakolwiek z wag ma wartość `null`, pipeline automatycznie przełącza
się na tryb nieważony (`K = BC`) i wyświetla informację o brakujących wagach.

### Domyślne tablice punktacji

**Poziom napięcia** (`voltage_scores`):

| Napięcie (V) | Wynik |
|-------------|-------|
| 110 000 | 0,333 |
| 220 000 | 0,667 |
| 400 000 | 1,000 |

**Typ obiektu** (`type_scores`):

| Typ | Wynik |
|-----|-------|
| generator | 1,00 |
| substation | 0,66 |
| distribution | 0,33 |

Wszystkie mapowania wczytywane są wyłącznie z pliku JSON — żadne wartości
nie są zakodowane na stałe w kodzie źródłowym. Nowe poziomy napięć lub typy
obiektów można dodać wyłącznie przez edycję pliku JSON.

---

## Struktura projektu

```
betweenness-centrality/
├── data/
│   ├── raw/                        # Dane wejściowe (OSM shapefiles)
│   │   ├── lines.shp               # Linie elektroenergetyczne WN
│   │   └── nodes.shp               # Stacje i elektrownie
│   └── weights/
│       └── ahp_matrix.json         # Wagi AHP i tablice punktacji węzłów
├── output/                         # Wyniki — ignorowane przez Git
├── src/
│   ├── config.py                   # Wszystkie parametry i ścieżki plików
│   ├── graph_builder.py            # Budowa grafu (kroki 1–4C)
│   ├── centrality.py               # Centralność pośrednictwa, bliskości i stopnia
│   ├── weights.py                  # Obliczanie wskaźnika krytyczności AHP (krok 11)
│   ├── export.py                   # Eksport do GeoPackage (krok 10)
│   └── main.py                     # Punkt wejścia pipeline'u
└── requirements.txt
```

---

## Zależności

| Pakiet | Wersja |
|--------|--------|
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

## Autorzy
```
Jakub Berliński, Bartosz Wróblewski — 2026
```
