# scrape_rockhal_concerts.py

Scraper des concerts disponibles sur [rockhal.lu](https://rockhal.lu/).

## Fonctionnement

Le script opère en deux étapes :

1. **Appel API REST WordPress** — récupère la liste complète des concerts via l'endpoint `/wp-json/rockhal/shows` (id, titre, date, genres, statut, lien, image, etc.)
2. **Scraping des pages individuelles** — pour chaque concert, visite la page HTML afin d'extraire l'heure d'ouverture des portes (`Doors`) et le **prix minimum** via le widget Ticketmatic.

L'adresse physique est fixe pour tous les concerts : `5, avenue du Rock, L-4361 Esch-sur-Alzette`.

### Récupération du prix

La stratégie de prix est multi-niveaux :

1. Lien `buy_tickets` dans la page HTML → widget Ticketmatic Rockhal
2. `offers.url` dans le JSON-LD de la page → widget Ticketmatic Rockhal
3. `custom_event_link` de l'API pointant vers `atelier.lu` → widget Ticketmatic Atelier (concerts co-organisés)
4. Fallback : `Price Unavailable`

Les tarifs conditionnels (ex: Kulturpass) sont ignorés pour n'afficher que le prix plancher standard.

### Fichier de Sortie

Le fichier de sortie est d'abord écrit dans un fichier temporaire, puis renommé. Cela protège le fichier existant en cas de crash pendant l'écriture.

Les requêtes HTTP sont relancées jusqu'à 3 fois en cas d'échec. Le scraping des pages individuelles est parallélisé (10 threads simultanés).

## Sorties

| Répertoire | Fichier                           | Format |
|-----------|-----------------------------------|--------|
| `JSON/`   | `scrape_rockhal_concerts.json`    | JSON   |
| `CSV/`    | `scrape_rockhal_concerts.csv`     | CSV    |
| `Log/`    | `scrape_rockhal_concerts.log`     | Log    |

### Champs produits

| Champ          | Description                                      |
|----------------|--------------------------------------------------|
| `id`           | Identifiant unique du concert (API)              |
| `artist`       | Nom de l'artiste / événement                     |
| `date_live`    | Date du concert (format `YYYY-MM-DD`)            |
| `doors_time`   | Heure d'ouverture des portes                     |
| `location`     | Nom de la salle (ex: `Rockhal Main Hall`)        |
| `address`      | Adresse fixe : `5, avenue du Rock, L-4361 Esch-sur-Alzette` |
| `genres`       | Liste des genres musicaux (séparés par `;` en CSV) |
| `status`       | Statut billetterie (ex: `Buy Tickets`, `Sold Out`) |
| `url`          | Lien vers la page du concert                     |
| `buy_link`     | Lien de réservation (Ticketmatic ou Atelier)     |
| `image`        | URL de l'image de l'événement                    |
| `price`        | Prix minimum (ex: `25.00 EUR`) ou `Price Unavailable` |
| `date_created` | Horodatage UTC du scan                           |

## Usage

```bash
# JSON (format par défaut)
python scrape_rockhal_concerts.py

# CSV
python scrape_rockhal_concerts.py -f csv

# Exclure des genres (séparés par ;)
python scrape_rockhal_concerts.py -f csv -g "Party; Child"

# Exclure des statuts (séparés par ;)
python scrape_rockhal_concerts.py -f csv -s "Canceled; Sold Out"

# Combiner les filtres
python scrape_rockhal_concerts.py -f json -g "Party" -s "Canceled"
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
