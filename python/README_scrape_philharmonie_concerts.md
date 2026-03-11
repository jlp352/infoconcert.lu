# scrape_philharmonie_concerts.py

Scraper des concerts disponibles sur [philharmonie.lu](https://www.philharmonie.lu/).

## Fonctionnement

Le script opère en trois phases successives :

1. **Listing paginé** — parcourt les pages du programme (`?eventtype=concert&favorite=false&page=N`, ~16 concerts/page) et collecte les URLs et titres des concerts via parsing HTML direct.
2. **Scraping des pages détail** (parallélisé) — pour chaque concert, visite la page individuelle afin d'extraire : date, heure, artistes, prix, image, salle, lien de réservation et statut.
3. **Construction de la map genres** — paginatiion de chaque filtre genre officiel du site (`?genre=[slug]&eventtype=concert`) pour associer un ou plusieurs genres à chaque concert.

L'adresse physique est fixe pour tous les concerts : `Place de l'Europe, L-1499 Luxembourg`.

### Extraction des données détail

Chaque page de concert est analysée par regex et HTMLParser pour extraire :

| Donnée | Méthode | Exemple |
|---|---|---|
| Date | Regex `DD.MM.YYYY` | `10.03.2026` |
| Heure | Regex `[jour abrégé]. HH:MM` | `mar. 19:30` |
| Lien ticket | Regex sur `ticket.philharmonie.lu/phoenix/webticket/shop?event=XXXX` | — |
| ID événement | Extrait de l'URL ticket (champ `event=`) | `12345` |
| Prix | Regex `N €` + détection `Gratuit` | `48 €`, `0.0` |
| Image hero | Regex sur `data-srcset` dans `full-image__image` | `/media/[hash]/image.jpg` |
| Salle | Recherche parmi les salles connues | `Grand Auditorium` |
| Statut | Présence du lien ticket ou mot `complet` | `buy_now` / `sold_out` |
| Artistes | HTMLParser sur `<li><strong>Nom</strong> rôle</li>` | `Mark Steinberg, ...` |

### Genres

Les genres sont récupérés via les filtres officiels du site Philharmonie:

| Slug | Libellé |
|---|---|
| `chamber` | Chamber Music |
| `orchestral` | Orchestral |
| `world` | World Music |
| `crossover` | Crossover |
| `film` | Film Music |
| `electronic` | Electronic |

Un concert peut appartenir à plusieurs genres. Les concerts sans genre associé reçoivent le fallback `["Classical"]`.

### Champ `artist`

Si des artistes sont trouvés dans la section "Les artistes" de la page, leurs noms (jusqu'à 5) sont joints par `, `. Sinon, le titre de l'événement est utilisé.

### Identifiant

L'ID du concert est en priorité le numéro d'événement Ticketmatic (`event=XXXX`). À défaut, le slug extrait de l'URL du concert est utilisé.

### Fichier de sortie

Le fichier de sortie est d'abord écrit dans un fichier temporaire, puis renommé atomiquement. Cela protège le fichier existant en cas de crash pendant l'écriture.

Les requêtes HTTP sont relancées jusqu'à 3 fois en cas d'échec. Le scraping des pages détail est parallélisé (10 threads simultanés).

## Sorties

| Répertoire | Fichier                                | Format |
|------------|----------------------------------------|--------|
| `JSON/`    | `scrape_philharmonie_concerts.json`    | JSON   |
| `CSV/`     | `scrape_philharmonie_concerts.csv`     | CSV    |
| `Log/`     | `scrape_philharmonie_concerts.log`     | Log    |

### Champs produits

| Champ          | Description                                                                 |
|----------------|-----------------------------------------------------------------------------|
| `id`           | Identifiant unique (ID Ticketmatic ou slug URL)                             |
| `artist`       | Noms des artistes (jusqu'à 5) ou titre de l'événement                      |
| `date_live`    | Date du concert (format `YYYY-MM-DD`)                                       |
| `doors_time`   | Heure de début du concert                                                   |
| `location`     | Nom de la salle : `Philharmonie` (avec précision de la salle si disponible) |
| `address`      | Adresse fixe : `Place de l'Europe, L-1499 Luxembourg`                       |
| `genres`       | Liste des genres musicaux (séparés par `;` en CSV)                          |
| `status`       | Statut billetterie : `buy_now` ou `sold_out`                                |
| `url`          | Lien vers la page du concert                                                |
| `buy_link`     | Lien de réservation Ticketmatic                                             |
| `image`        | URL de l'image hero du concert (ou image par défaut)                        |
| `price`        | Prix minimum (ex: `36.00 EUR`) ou `Price Unavailable`                       |
| `date_created` | Horodatage UTC du scan                                                      |

## Usage

```bash
# JSON (format par défaut)
python scrape_philharmonie_concerts.py

# CSV
python scrape_philharmonie_concerts.py -f csv

# Exclure des genres (séparés par ;)
python scrape_philharmonie_concerts.py -g "Classical"

# Exclure des statuts (séparés par ;)
python scrape_philharmonie_concerts.py -s "sold_out"

# Combiner les filtres
python scrape_philharmonie_concerts.py -f csv -g "Electronic" -s "sold_out"
```

### Options CLI

| Option                     | Description                                                | Défaut |
|----------------------------|------------------------------------------------------------|--------|
| `-f`, `--format`           | Format de sortie : `json` ou `csv`                        | `json` |
| `-g`, `--exclude-genres`   | Genres à exclure, séparés par `;` (insensible à la casse) | aucun  |
| `-s`, `--exclude-statuses` | Statuts à exclure, séparés par `;` (insensible à la casse)| aucun  |

## Dépendances

Ce script utilise **uniquement la bibliothèque standard Python** — aucune installation supplémentaire n'est requise.

- Python 3.10+
- Modules : `argparse`, `csv`, `json`, `logging`, `re`, `urllib`, `concurrent.futures`, `html`, `pathlib`, `tempfile`

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.
