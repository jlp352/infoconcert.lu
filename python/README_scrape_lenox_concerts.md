# scrape_lenox_concerts.py

Scraper des concerts disponibles sur [xceed.me/fr/luxembourg/venue/lenox-club](https://xceed.me/fr/luxembourg/venue/lenox-club) pour le **Lenox Club Luxembourg**.

## Fonctionnement

Le script opère en trois étapes :

1. **Fetch HTML Next.js (SSR)** — récupère la page venue xceed.me via HTTP. La page est rendue côté serveur (Next.js App Router) et embarque les données d'événements directement dans le HTML via des balises `<script>self.__next_f.push([1, "…"])</script>`.
2. **Extraction depuis le payload RSC** — les données d'événements sont dans le cache TanStack Query sérialisé en JSON double-échappé dans le payload RSC (React Server Components). Le script les extrait via regex sans bibliothèque tierce.
3. **Enrichissement des genres via l'API Deezer** — chaîne de 3 appels pour chaque artiste : `search/artist` → `artist/{id}/top` → `album/{id}` → genres. Résultats mis en cache. Fallback : `["Concerts"]`.

Les événements passés sont filtrés automatiquement. L'adresse physique est fixe : `58 Rue du Fort Neipperg, 2230 Luxembourg, Luxembourg`.

### Architecture du site

xceed.me est une SPA Next.js avec App Router et React Server Components (RSC). La page venue est rendue côté serveur avec les données d'événements pré-chargées. Ces données sont transmises au client via des fragments RSC poussés dans le HTML :

```html
<script>self.__next_f.push([1, "…JSON double-échappé…"])</script>
```

Le contenu de ces fragments est une chaîne JavaScript contenant le payload RSC. Ce payload inclut le cache TanStack Query déhydraté (`dehydratedState`), qui contient la liste des événements de la venue sous forme d'une requête de type « infinite query » avec une structure `pages: [[event1, event2, …]]`.

### Double-échappement JSON

Les données d'événements sont encodées à deux niveaux :

1. **JSON RSC** — les champs JSON utilisent des guillemets qui deviennent `\"` dans la chaîne JS (i.e. `\` + `"` dans le HTML).
2. **Valeurs de champs** — les noms d'artistes peuvent contenir des séquences `\uXXXX` (ex. `\u0026` pour `&`), décodées par le script.

Le regex d'extraction utilise une classe de caractères `(?:[^\\"]|\\[^"])*` pour les valeurs de champs, ce qui empêche le backtracking inter-champs (ne peut pas traverser `\"` qui termine un champ).

### Champs extraits

Pour chaque événement, le script extrait :

| Champ RSC | Description |
|-----------|-------------|
| `legacyId` | Identifiant numérique xceed.me → `id` du concert (`lenox-{legacyId}`) |
| `name` | Nom de l'événement → artiste (le suffixe `| Lenox DD.MM` est supprimé) |
| `slug` | Slug URL de l'événement → construit l'URL `https://xceed.me/…/event/{slug}/{legacyId}` |
| `startingTime` | Timestamp Unix UTC (10 chiffres) → converti en heure locale (Europe/Luxembourg, CET/CEST) |
| `coverUrl` | URL de l'image de bannière xceed.me |

### Conversion de fuseau horaire

Le champ `startingTime` est un timestamp Unix UTC. Le script calcule le décalage Europe/Luxembourg (CET = UTC+1 en hiver, CEST = UTC+2 en été) selon les règles DST européennes (dernier dimanche de mars / octobre à 01:00 UTC), sans dépendance externe.

## Usage

```bash
# Sortie JSON (défaut) → JSON/scrape_lenox_concerts.json
python scrape_lenox_concerts.py

# Sortie CSV → CSV/scrape_lenox_concerts.csv
python scrape_lenox_concerts.py -f csv

# Exclure les événements sold_out
python scrape_lenox_concerts.py -s "sold_out"

# Exclure un genre
python scrape_lenox_concerts.py -g "Techno"
```

## Fichiers générés

| Chemin | Description |
|--------|-------------|
| `JSON/scrape_lenox_concerts.json` | Sortie JSON avec métadonnées (`scraped_at`, `source`, `total`, `concerts`) |
| `CSV/scrape_lenox_concerts.csv` | Sortie CSV (colonnes : `id`, `artist`, `date_live`, `doors_time`, `location`, `address`, `genres`, `status`, `url`, `buy_link`, `image`, `price`, `date_created`) |
| `Log/scrape_lenox_concerts.log` | Log append — erreurs réseau, événements ignorés, genres Deezer |

## Dépendances

Stdlib Python uniquement — aucun `pip install` requis.
