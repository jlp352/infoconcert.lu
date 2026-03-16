# scrape_forum_trier_concerts.py

Scraper des concerts disponibles sur [forum-concert.com](https://www.forum-concert.com/) — **Forum Concert / Metropolis**, Trêves (Allemagne).

## Fonctionnement

Le script opère en trois étapes :

1. **Fetch du widget Eventim Light via curl** — la salle embarque un iframe Eventim Light (`eventim-light.com`). La page est rendue côté serveur (SSR Vike) et contient un bloc `<script id="vike_pageContext">` avec l'intégralité des événements en JSON. Les requêtes passent par `curl` pour présenter une empreinte TLS identique à Chrome et contourner la protection Cloudflare.
2. **Parsing et normalisation** — extraction du chemin `initialStoreState → events → eventOverviewItems` depuis le JSON SSR. Chaque événement est normalisé vers le schéma interne (id, artiste, date, heure, prix, statut, image).
3. **Enrichissement des genres via l'API Deezer** — chaîne de 3 appels pour chaque artiste : `search/artist` → `artist/{id}/top` → `album/{id}` → genres. Résultats mis en cache. Fallback : `["Concerts"]`.

L'adresse physique est fixe pour tous les concerts : `Gerty-Spies-Str. 4, 54290 Trier, Allemagne`.

### Source des données : Eventim Light (SSR Vike)

L'iframe Eventim Light est une SPA construite avec [Vike](https://vike.dev/) (SSR React). Le serveur injecte l'état initial du store dans un `<script id="vike_pageContext">`. Le script extrait ce JSON sans exécuter de JavaScript côté client.

Chemin dans le JSON :
```
vike_pageContext
  └── initialStoreState
        └── events
              └── eventOverviewItems   ← liste des événements
```

### Champs réels des événements Eventim Light

| Champ JSON         | Contenu                                                        |
|--------------------|----------------------------------------------------------------|
| `id`               | Identifiant Eventim de l'événement                             |
| `title`            | Nom de l'artiste / titre du concert                            |
| `start`            | Date/heure de début (ISO 8601 avec offset tz, heure locale)    |
| `doorsOpen`        | Date/heure d'ouverture des portes (ISO 8601 avec offset tz)    |
| `minPrice.value`   | Prix minimum (float)                                           |
| `minPrice.currency`| Devise (ex: `EUR`)                                             |
| `soldout`          | Booléen — `true` si sold out                                   |
| `image.id`         | Identifiant d'image (construit en URL CDN Eventim Light)       |

### Dates et fuseaux horaires

Les dates Eventim Light sont fournies **déjà en heure locale** avec un offset explicite (ex: `2026-04-16T20:00:00+02:00`). Aucune conversion UTC n'est effectuée — la date et l'heure sont extraites directement depuis l'offset local.

### URL et lien d'achat

- `url` : page officielle de la salle (`forum-concert.com/#events`) — les routes internes du widget Eventim Light ne sont pas navigables hors navigateur.
- `buy_link` : recherche Eventim.de construite dynamiquement (`eventim.de/search/?searchterm={artiste}+trier`) pour orienter vers la bonne salle.

### Fetch via curl (anti-Cloudflare)

L'iframe Eventim Light est protégée par Cloudflare (TLS fingerprinting). Les requêtes passent par `curl` avec un jeu de headers imitant Chrome 122 (`Referer: forum-concert.com`, `sec-fetch-dest: iframe`, `sec-ch-ua-*`). Le code HTTP est extrait via `--write-out "%{http_code}"`.

Les requêtes Deezer (pas de protection TLS) utilisent `urllib` classique.

### Prix et statut

| Cas                          | `price`             | `status`    |
|------------------------------|---------------------|-------------|
| `soldout: true`              | `{prix:.2f} EUR`    | `sold_out`  |
| `minPrice.value` absent      | `Price Unavailable` | `buy_now`   |
| `minPrice.value` = 0         | `Free`              | `free`      |
| `minPrice.value` > 0         | `{prix:.2f} EUR`    | `buy_now`   |

## Sorties

| Répertoire | Fichier                            | Format |
| ---------- | ---------------------------------- | ------ |
| `JSON/`    | `scrape_forum_trier_concerts.json` | JSON   |
| `CSV/`     | `scrape_forum_trier_concerts.csv`  | CSV    |
| `Log/`     | `scrape_forum_trier_concerts.log`  | Log    |

### Champs produits

| Champ          | Description                                                                    |
|----------------|--------------------------------------------------------------------------------|
| `id`           | Identifiant construit depuis l'id Eventim (`forum_trier_{id}`)                 |
| `artist`       | Titre de l'événement (`title`)                                                 |
| `date_live`    | Date du concert (format `YYYY-MM-DD`)                                          |
| `doors_time`   | Heure d'ouverture des portes (`HH:MM`) — fallback sur l'heure de début         |
| `location`     | Nom de la salle : `Forum Concert - Trier`                         |
| `address`      | Adresse fixe : `Gerty-Spies-Str. 4, 54290 Trier, Allemagne`                   |
| `genres`       | Liste des genres musicaux via Deezer (séparés par `;` en CSV)                  |
| `status`       | Statut billetterie : `buy_now`, `sold_out` ou `free`                           |
| `url`          | Page officielle de la salle (`forum-concert.com/#events`)                      |
| `buy_link`     | Lien de recherche Eventim.de ciblé sur l'artiste + Trier                       |
| `image`        | URL CDN Eventim Light (`eventim-light.com/de/api/image/{id}/shop_cover_v3/webp`) ou `null` |
| `price`        | Prix minimum (ex: `24.80 EUR`), `Free` ou `Price Unavailable`                  |
| `date_created` | Horodatage UTC du scan (ISO 8601)                                              |

## Usage

```bash
# JSON (format par défaut)
python scrape_forum_trier_concerts.py

# CSV
python scrape_forum_trier_concerts.py -f csv

# Exclure des genres (séparés par ;)
python scrape_forum_trier_concerts.py -g "Pop"

# Exclure des statuts (séparés par ;)
python scrape_forum_trier_concerts.py -s "sold_out"

# Combiner les filtres
python scrape_forum_trier_concerts.py -f json -g "Pop" -s "sold_out"
```

### Options CLI

| Option                     | Description                                                 | Défaut |
|----------------------------|-------------------------------------------------------------|--------|
| `-f`, `--format`           | Format de sortie : `json` ou `csv`                         | `json` |
| `-g`, `--exclude-genres`   | Genres à exclure, séparés par `;` (insensible à la casse)  | aucun  |
| `-s`, `--exclude-statuses` | Statuts à exclure, séparés par `;` (insensible à la casse) | aucun  |

## Dépendances

Ce script utilise **uniquement la bibliothèque standard Python** — aucune installation pip supplémentaire n'est requise. Il nécessite en revanche que **`curl`** soit disponible dans le PATH (utilisé pour contourner la protection Cloudflare d'Eventim Light).

- Python 3.10+
- Modules : `argparse`, `csv`, `io`, `json`, `logging`, `re`, `subprocess`, `urllib`, `datetime`, `pathlib`, `tempfile`
- Outil système : `curl`

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.
