# scrape_mergener_hof_trier_concerts.py

Scraper des concerts disponibles sur [mjctrier.de](https://mjctrier.de/) — **Mergener Hof (MJC Trier)**, Trêves (Allemagne).

## Fonctionnement

Le script opère en quatre étapes :

1. **Scraping HTML des pages liste** — parcours paginé de `/events/kategorie/konzert/liste/` (The Events Calendar, plugin WordPress). Seuls les articles portant la classe CSS `cat_konzert` sont retenus. La pagination suit les liens `tribe-events-c-nav__next` jusqu'à épuisement.
2. **Scraping des pages de détail** — pour chaque concert, fetch de la page de l'événement afin d'extraire l'heure d'ouverture des portes (motif `Einlass: HH:MM`) et le lien de billetterie (Eventim, Ticket-Regional, Reservix…).
3. **Récupération du prix** — selon la plateforme du `buy_link` :
   - `ticket-regional.de` : fetch urllib → extraction des `<td class="categoryCosts">`, prix minimum retenu.
   - `eventim.de` : fetch via `curl` (headers anti-Akamai) → extraction du JSON-LD `AggregateOffer.lowPrice`.
4. **Enrichissement des genres via l'API Deezer** — chaîne de 3 appels pour chaque artiste : `search/artist` → `artist/{id}/top` → `album/{id}` → genres. Résultats mis en cache. Fallback : `["Concerts"]`.

L'adresse physique est fixe pour tous les concerts : `Rindertanzstraße 4, 54290 Trier, Allemagne`.

### Stratégie de parsing HTML

La page liste expose les événements sous forme d'`<article>` possédant la classe `cat_konzert`. Pour chaque article :

- **Titre + URL** : extraits du premier lien `<a href>` dans `tribe-events-calendar-list__event-title`.
- **Identifiant** : slug de l'URL (dernier segment) préfixé de `mjc_trier_`.
- **Date** : attribut `datetime="YYYY-MM-DD"` de la balise `<time>`.
- **Heure de début** : texte du `<time>` (format `@ HH:MM`), utilisée en fallback si l'heure Einlass est absente.
- **Image** : premier `src=` trouvé dans `tribe-events-calendar-list__event-featured-image`.

### Fetch Eventim via curl

Eventim.de est protégé par Akamai (TLS fingerprinting). Les requêtes sur ce domaine passent par `curl` avec un jeu de headers imitant Chrome 122 (`sec-fetch-*`, `sec-ch-ua-*`). Le code HTTP est extrait via `--write-out "%{http_code}"`.

### Statut

La page mjctrier.de n'expose pas d'information `sold_out`. Tous les concerts sont produits avec `status: "buy_now"`.

### Prix et statut

| Cas                                      | `price`             | `status`   |
|------------------------------------------|---------------------|------------|
| Prix = 0 (ticket-regional ou eventim)    | `Free`              | `buy_now`  |
| Prix > 0                                 | `{prix:.2f} EUR`    | `buy_now`  |
| Plateforme non reconnue / erreur fetch   | `Price Unavailable` | `buy_now`  |
| Aucun `buy_link`                         | `Price Unavailable` | `buy_now`  |

## Sorties

| Répertoire | Fichier                                      | Format |
|------------|----------------------------------------------|--------|
| `JSON/`    | `scrape_mergener_hof_trier_concerts.json`    | JSON   |
| `CSV/`     | `scrape_mergener_hof_trier_concerts.csv`     | CSV    |
| `Log/`     | `scrape_mergener_hof_trier_concerts.log`     | Log    |

### Champs produits

| Champ          | Description                                                                        |
|----------------|------------------------------------------------------------------------------------|
| `id`           | Identifiant construit depuis le slug URL (`mjc_trier_{slug}`)                      |
| `artist`       | Titre de l'événement (headliner / nom du concert)                                  |
| `date_live`    | Date du concert (format `YYYY-MM-DD`)                                              |
| `doors_time`   | Heure d'ouverture des portes (`HH:MM`) — fallback sur l'heure de début si absente  |
| `location`     | Nom de la salle : `Mergener Hof - Trier`                                           |
| `address`      | Adresse fixe : `Rindertanzstraße 4, 54290 Trier, Allemagne`                        |
| `genres`       | Liste des genres musicaux via Deezer (séparés par `;` en CSV)                      |
| `status`       | Statut billetterie : `buy_now` (sold_out non exposé sur ce site)                   |
| `url`          | Lien vers la page de détail de l'événement sur mjctrier.de                         |
| `buy_link`     | Lien de billetterie (Eventim, Ticket-Regional, Reservix…) ou `null`                |
| `image`        | URL de l'image mise en avant de l'événement, ou `null`                             |
| `price`        | Prix minimum (ex: `24.80 EUR`), `Free` ou `Price Unavailable`                      |
| `date_created` | Horodatage UTC du scan (ISO 8601)                                                  |

## Usage

```bash
# JSON (format par défaut)
python scrape_mergener_hof_trier_concerts.py

# CSV
python scrape_mergener_hof_trier_concerts.py -f csv

# Exclure des genres (séparés par ;)
python scrape_mergener_hof_trier_concerts.py -g "Pop"

# Exclure des statuts (séparés par ;)
python scrape_mergener_hof_trier_concerts.py -s "sold_out"

# Combiner les filtres
python scrape_mergener_hof_trier_concerts.py -f json -g "Pop" -s "sold_out"
```

### Options CLI

| Option                     | Description                                                 | Défaut |
|----------------------------|-------------------------------------------------------------|--------|
| `-f`, `--format`           | Format de sortie : `json` ou `csv`                         | `json` |
| `-g`, `--exclude-genres`   | Genres à exclure, séparés par `;` (insensible à la casse)  | aucun  |
| `-s`, `--exclude-statuses` | Statuts à exclure, séparés par `;` (insensible à la casse) | aucun  |

## Dépendances

Ce script utilise **uniquement la bibliothèque standard Python** — aucune installation pip supplémentaire n'est requise. Il nécessite en revanche que **`curl`** soit disponible dans le PATH (utilisé pour contourner la protection Akamai d'eventim.de).

- Python 3.10+
- Modules : `argparse`, `csv`, `html`, `io`, `json`, `logging`, `re`, `subprocess`, `urllib`, `datetime`, `pathlib`, `tempfile`
- Outil système : `curl`

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.
