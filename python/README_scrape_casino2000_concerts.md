# scrape_casino2000_concerts.py

Scraper des concerts disponibles sur [casino2000.lu](https://casino2000.lu/).

## Fonctionnement

Le script opère en trois étapes :

1. **Scraping HTML de la page agenda** — parse directement la page `/fr/agenda-du-casino-2000/?type=concerts` (pas d'API REST disponible) pour extraire la liste des concerts (titre, date, image, statut, lien de réservation).
2. **Scraping des pages individuelles** — pour chaque concert, visite la page HTML afin d'extraire l'heure d'ouverture des portes et l'**image Open Graph**, ainsi que le **prix minimum** via le widget Ticketmatic.
3. **Enrichissement des genres via l'API Deezer** — chaîne de 3 appels pour chaque artiste : `search/artist` → `artist/{id}/top` → `album/{id}` → genres. Résultats mis en cache. Fallback : `["Concerts"]`.

L'adresse physique est fixe pour tous les concerts : `Route de Mondorf, L-5618 Mondorf-les-Bains`.

### Récupération du prix

Le prix est extrait depuis le widget Ticketmatic Casino 2000 :

1. Extraction de l'`event_id` depuis le paramètre `event=` du lien `buy_link`
2. Appel à l'endpoint `/addtickets?event={id}` — parsing du bloc JS `constant("TM", {...})`
3. Sélection du prix plancher parmi tous les contingents, en ignorant les tarifs conditionnels (ex: Kulturpass)
4. Fallback : `Price Unavailable`

### Fichier de Sortie

Le fichier de sortie est d'abord écrit dans un fichier temporaire, puis renommé. Cela protège le fichier existant en cas de crash pendant l'écriture.

Les requêtes HTTP sont relancées jusqu'à 3 fois en cas d'échec. Le scraping des pages individuelles est parallélisé (10 threads simultanés).

## Sorties

| Répertoire | Fichier                             | Format |
|-----------|-------------------------------------|--------|
| `JSON/`   | `scrape_casino2000_concerts.json`   | JSON   |
| `CSV/`    | `scrape_casino2000_concerts.csv`    | CSV    |
| `Log/`    | `scrape_casino2000_concerts.log`    | Log    |

### Champs produits

| Champ          | Description                                                    |
|----------------|----------------------------------------------------------------|
| `id`           | Slug extrait de l'URL de l'événement (ex: `nom-artiste-2025`) |
| `artist`       | Nom de l'artiste / événement                                   |
| `date_live`    | Date du concert (format `YYYY-MM-DD`)                          |
| `doors_time`   | Heure d'ouverture des portes                                   |
| `location`     | Nom de la salle : `Casino 2000`                                |
| `address`      | Adresse fixe : `Route de Mondorf, L-5618 Mondorf-les-Bains`   |
| `genres`       | Liste des genres musicaux via Deezer (séparés par `;` en CSV) |
| `status`       | Statut billetterie : `buy_now` ou `sold_out`                   |
| `url`          | Lien vers la page du concert                                   |
| `buy_link`     | Lien de réservation (Ticketmatic)                              |
| `image`        | URL de l'image Open Graph de l'événement                       |
| `price`        | Prix minimum (ex: `25.00 EUR`) ou `Price Unavailable`          |
| `date_created` | Horodatage UTC du scan                                         |

## Usage

```bash
# JSON (format par défaut)
python scrape_casino2000_concerts.py

# CSV
python scrape_casino2000_concerts.py -f csv

# Exclure des genres (séparés par ;)
python scrape_casino2000_concerts.py -f csv -g "Pop; Dance"

# Exclure des statuts (séparés par ;)
python scrape_casino2000_concerts.py -f csv -s "sold_out"

# Combiner les filtres
python scrape_casino2000_concerts.py -f json -g "Pop" -s "sold_out"
```

### Options CLI

| Option                     | Description                                               | Défaut |
|----------------------------|-----------------------------------------------------------|--------|
| `-f`, `--format`           | Format de sortie : `json` ou `csv`                       | `json` |
| `-g`, `--exclude-genres`   | Genres à exclure, séparés par `;` (insensible à la casse) | aucun  |
| `-s`, `--exclude-statuses` | Statuts à exclure, séparés par `;` (insensible à la casse) | aucun  |

## Dépendances

Ce script utilise **uniquement la bibliothèque standard Python** — aucune installation supplémentaire n'est requise.

- Python 3.10+
- Modules : `argparse`, `csv`, `json`, `logging`, `re`, `urllib`, `concurrent.futures`, `html`, `pathlib`, `tempfile`

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.
