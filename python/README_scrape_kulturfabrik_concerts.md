# scrape_kulturfabrik_concerts.py

Scraper des concerts disponibles sur [kulturfabrik.lu](https://www.kulturfabrik.lu/).

## Fonctionnement

Le script opère en quatre étapes :

1. **Scraping HTML de la page agenda** — parse directement la page `/events` (pas d'API REST disponible sur le CMS Bunker Palace) pour extraire la liste complète de tous les événements (titre, date, heure, image, catégorie, lien de réservation).
2. **Filtre catégorie Musique** — le paramètre `?category=musique` de l'URL du site est traité côté client (JavaScript) et n'a aucun effet sur la réponse HTTP. Le filtre est donc appliqué en Python sur le champ `category` extrait du `div.item-category` de chaque carte : seuls les événements dont la catégorie principale est `Musique` sont conservés.
3. **Scraping des pages individuelles** — pour chaque concert, visite la page HTML afin d'extraire l'heure d'ouverture des portes, l'**image Open Graph**, le **statut** et le **prix minimum** via le widget Ticketmatic. Parallélisé sur 10 threads.
4. **Enrichissement des genres via l'API Deezer** — chaîne de 3 appels pour chaque artiste : `search/artist` → `artist/{id}/top` → `album/{id}` → genres. Résultats mis en cache. Fallback : `["Concerts"]`.

L'adresse physique est fixe pour tous les concerts : `116, rue de Luxembourg, L-4221 Esch-sur-Alzette`.

### Filtrage catégorie

Le site Kulturfabrik propose 7 catégories d'événements : `Musique`, `Cinéma`, `Littérature`, `Danse`, `Théâtre`, `Exposition`, `Workshop`. Le scraper ne conserve que les événements dont la **catégorie principale** (premier texte du `div.item-category`, avant le premier ` / `) est `Musique`. Les événements multi-catégories où Musique est secondaire (ex : `Danse / Musique / Flamenco`) sont exclus.

### Récupération du prix

Le prix est extrait depuis le widget Ticketmatic Kulturfabrik :

1. Le lien de réservation (`buy_link`) est récupéré directement depuis le `div.item-tickets` de la page liste
2. Extraction de l'`event_id` depuis le paramètre `event=` de l'URL
3. Appel à l'endpoint `/addtickets?event={id}&l=en` — parsing du bloc JS `constant("TM", {...})`
4. Sélection du prix plancher parmi tous les contingents, en ignorant les tarifs conditionnels (ex : Kulturpass)
5. Fallback : `Price Unavailable`

### Fichier de sortie

Le fichier de sortie est d'abord écrit dans un fichier temporaire, puis renommé. Cela protège le fichier existant en cas de crash pendant l'écriture.

Les requêtes HTTP sont relancées jusqu'à 3 fois en cas d'échec. Le scraping des pages individuelles est parallélisé (10 threads simultanés).

## Sorties

| Répertoire | Fichier                                  | Format |
|------------|------------------------------------------|--------|
| `JSON/`    | `scrape_kulturfabrik_concerts.json`      | JSON   |
| `CSV/`     | `scrape_kulturfabrik_concerts.csv`       | CSV    |
| `Log/`     | `scrape_kulturfabrik_concerts.log`       | Log    |

### Champs produits

| Champ          | Description                                                              |
|----------------|--------------------------------------------------------------------------|
| `id`           | Slug extrait de l'URL de l'événement (ex: `Ho99o9-N8noface`)            |
| `artist`       | Nom de l'artiste / événement                                             |
| `date_live`    | Date du concert (format `YYYY-MM-DD`)                                    |
| `doors_time`   | Heure d'ouverture des portes (ou heure de début en fallback)             |
| `location`     | Nom de la salle : `Kulturfabrik`                                         |
| `address`      | Adresse fixe : `116, rue de Luxembourg, L-4221 Esch-sur-Alzette`        |
| `genres`       | Liste des genres musicaux via Deezer (séparés par `;` en CSV)           |
| `status`       | Statut billetterie : `buy_now` ou `sold_out`                             |
| `url`          | Lien vers la page du concert                                             |
| `buy_link`     | Lien de réservation (Ticketmatic)                                        |
| `image`        | URL de l'image de l'événement (lazy-load `data-src`)                    |
| `price`        | Prix minimum (ex: `27.50 EUR`) ou `Price Unavailable`                   |
| `date_created` | Horodatage UTC du scan                                                   |

## Usage

```bash
# JSON (format par défaut)
python scrape_kulturfabrik_concerts.py

# CSV
python scrape_kulturfabrik_concerts.py -f csv

# Exclure des genres (séparés par ;)
python scrape_kulturfabrik_concerts.py -f csv -g "Pop; Dance"

# Exclure des statuts (séparés par ;)
python scrape_kulturfabrik_concerts.py -f csv -s "sold_out"

# Combiner les filtres
python scrape_kulturfabrik_concerts.py -f json -g "Pop" -s "sold_out"
```

### Options CLI

| Option                     | Description                                                | Défaut |
|----------------------------|------------------------------------------------------------|--------|
| `-f`, `--format`           | Format de sortie : `json` ou `csv`                        | `json` |
| `-g`, `--exclude-genres`   | Genres à exclure, séparés par `;` (insensible à la casse) | aucun  |
| `-s`, `--exclude-statuses` | Statuts à exclure, séparés par `;` (insensible à la casse) | aucun  |

## Dépendances

Ce script utilise **uniquement la bibliothèque standard Python** — aucune installation supplémentaire n'est requise.

- Python 3.10+
- Modules : `argparse`, `csv`, `json`, `logging`, `re`, `urllib`, `concurrent.futures`, `html`, `pathlib`, `tempfile`

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.
