# scrape_echo_lu_concerts.py

Scraper des concerts dans les centres culturels luxembourgeois via [echo.lu](https://www.echo.lu/).

## Fonctionnement

Le script utilise l'**API Firestore publique** du projet Firebase `lu-echo-prod` (backend de l'application echo.lu). Il n'effectue aucun scraping HTML.

Il opère en trois étapes, les deux premières étant parallélisées sur 6 threads :

1. **Récupération des adresses** — appel `GET /documents/venues/{slug}` pour chaque salle afin d'extraire l'adresse (rue, code postal, ville). Les résultats sont mis en cache.
2. **Requêtes d'expériences** (parallélisé) — pour chaque salle, `POST /documents:runQuery` avec un filtre `ARRAY_CONTAINS` sur le champ `venues`. Pagination automatique par blocs de 500 si nécessaire.
3. **Filtrage et parsing** — chaque document Firestore est filtré (modération `validated`, catégorie concert) puis converti en entrée(s) concert. Une expérience peut produire plusieurs concerts si elle possède plusieurs dates futures.

### Centres culturels couverts

| Salle             | Ville        | Slug Firestore                                    |
| ----------------- | ------------ | ------------------------------------------------- |
| Aalt Stadhaus     | Differdange  | `aalt-stadhaus-39otSR`                            |
| Cube 521          | Marnach      | `cube-521-gkQWpB`                                 |
| CAPE              | Ettelbruck   | `cape-centre-des-arts-pluriels-ettelbruck-sJ237P` |
| Kinneksbond       | Mamer        | `kinneksbond-centre-culturel-mamer-6S5DWP`        |
| Mierscher Theater | Mersch       | `mierscher-kulturhaus-pMxRma`                     |
| opderschmelz      | Dudelange    | `centre-culturel-opderschmelz-...-eGkHxP`         |
| Artikuss          | Soleuvre     | `artikuss-aRnbum`                                 |
| Prabbeli          | Wiltz        | `centre-socioculturel-regional-prabbeli-7hTND4`   |
| Maacher           | Grevenmacher | `machera-centre-culturel-grevenmacher-63RrPS`     |

### API Firestore utilisée

| Endpoint | Méthode | Usage |
|---|---|---|
| `https://firestore.googleapis.com/v1/projects/lu-echo-prod/databases/(default)/documents/venues/{slug}` | GET | Adresse d'une salle |
| `https://firestore.googleapis.com/v1/projects/lu-echo-prod/databases/(default)/documents:runQuery` | POST | Expériences d'une salle |

Aucune authentification requise. Les requêtes utilisent un filtre `ARRAY_CONTAINS` sur le champ `venues` de la collection `experiences`.

### Filtres appliqués

- **Modération** : seuls les documents avec `moderation = "validated"` sont retenus.
- **Catégorie** : seules les catégories `concerts`, `concerts-other` et `music` sont acceptées.
- **Dates** : seules les dates strictement futures (UTC) sont conservées.

### Conversion de dates

Les timestamps Firestore sont en UTC. Le script les convertit en heure locale luxembourgeoise (CET/CEST) en calculant dynamiquement l'offset DST selon les règles européennes (dernier dimanche de mars/octobre).

### Prix

| Cas | Valeur produite |
|---|---|
| `priceType = "free"` | `Free` |
| Tickets Firestore avec prix > 0 (hors Kulturpass) | `{min} EUR` (ex: `12.00 EUR`) |
| Aucun prix exploitable | `Price Unavailable` |

### Genres

Tous les concerts reçoivent le genre par défaut `["Concerts"]`.

### Identifiant

L'ID d'un concert est composé de l'identifiant Firestore de l'expérience et de la date : `{exp_id}_{YYYY-MM-DD}`. Cela permet de distinguer les différentes dates d'une même expérience.

### Fichier de sortie

Le fichier est d'abord écrit dans un fichier temporaire puis renommé atomiquement. Cela protège le fichier existant en cas de crash pendant l'écriture.

Les requêtes HTTP sont relancées jusqu'à 3 fois en cas d'échec réseau, avec un délai de 5 s entre chaque tentative.

## Sorties

| Répertoire | Fichier                              | Format |
|------------|--------------------------------------|--------|
| `JSON/`    | `scrape_echo_lu_concerts.json`       | JSON   |
| `CSV/`     | `scrape_echo_lu_concerts.csv`        | CSV    |
| `Log/`     | `scrape_echo_lu_concerts.log`        | Log    |

### Champs produits

| Champ          | Description                                                                 |
|----------------|-----------------------------------------------------------------------------|
| `id`           | `{exp_id}_{YYYY-MM-DD}` — identifiant unique par expérience et par date     |
| `artist`       | Titre de l'expérience (multilingue : en → fr → de → lb)                    |
| `date_live`    | Date du concert en heure locale luxembourgeoise (format `YYYY-MM-DD`)       |
| `doors_time`   | Heure de début en heure locale (format `HHhMM`, ex: `20h00`)               |
| `location`     | Nom de la salle (ex: `Aalt Stadhaus`)                                       |
| `address`      | Adresse de la salle extraite de Firestore (ex: `1 Rue X, L-4500 Differdange`) |
| `genres`       | `["Concerts"]` (valeur fixe)                                                |
| `status`       | `buy_now` (seul statut produit — Firestore ne distingue pas `sold_out`)     |
| `url`          | Lien vers l'expérience sur echo.lu (`/en/experiences/{id}`)                 |
| `buy_link`     | Lien de réservation (par date si disponible, sinon lien global, sinon `null`) |
| `image`        | URL de l'image featured (preview Firestore) ou image principale             |
| `price`        | Prix minimum ou `Free` ou `Price Unavailable`                               |
| `date_created` | Horodatage UTC du scan (ISO 8601)                                           |

## Usage

```bash
# JSON (format par défaut)
python scrape_echo_lu_concerts.py

# CSV
python scrape_echo_lu_concerts.py -f csv

# Exclure des statuts (séparés par ;)
python scrape_echo_lu_concerts.py -s "sold_out"

# Combiner les filtres
python scrape_echo_lu_concerts.py -f csv -s "sold_out"
```

### Options CLI

| Option                     | Description                                                | Défaut |
|----------------------------|------------------------------------------------------------|--------|
| `-f`, `--format`           | Format de sortie : `json` ou `csv`                        | `json` |
| `-s`, `--exclude-statuses` | Statuts à exclure, séparés par `;` (insensible à la casse)| aucun  |

## Dépendances

Ce script utilise **uniquement la bibliothèque standard Python** — aucune installation supplémentaire n'est requise.

- Python 3.10+
- Modules : `argparse`, `csv`, `json`, `logging`, `urllib`, `concurrent.futures`, `datetime`, `pathlib`, `tempfile`

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.
