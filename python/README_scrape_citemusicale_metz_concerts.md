# scrape_citemusicale_metz_concerts.py

Scraper des concerts des salles **BAM** et **Trinitaires** disponibles sur [citemusicale-metz.fr](https://www.citemusicale-metz.fr/programmation/bam-et-trinitaires).

## Fonctionnement

Le script opère en deux étapes :

1. **Appel de l'API REST Hydra** — le site (Nuxt.js SSR) expose une API publique `/api/events` qui retourne les événements au format JSON-LD. Le script filtre par lieu (BAM et Trinitaires) et par date (à partir d'aujourd'hui), puis pagine jusqu'à épuisement des résultats.
2. **Enrichissement des genres via l'API Deezer** — uniquement pour les événements sans tag de style renseigné par le site. Chaîne de 3 appels : `search/artist` → `artist/{id}/top` → `album/{id}` → genres. Résultats mis en cache. Fallback : `["Concerts"]`.

### API utilisée

```
GET https://www.citemusicale-metz.fr/api/events
    ?node.visible=1
    &sortingLastDateTime[after]=YYYY-MM-DD
    &tagGroup[0][]=bam
    &tagGroup[0][]=trinitaires
    &order[sortingFirstDateTime]=asc
    &itemsPerPage=100
    &page=N
    &_locale=fr
```

- `tagGroup[0][]` applique une condition **OR** dans le groupe : retourne les événements ayant le tag `bam` **ou** `trinitaires`.
- La pagination suit le standard Hydra (`hydra:totalItems` / `hydra:member`).
- L'API renvoie les tags de style (ex: `rap`, `rock`) directement dans le champ `tags` — Deezer n'est donc appelé que si le site n'en fournit pas.

### Prix et statut

Le tarif affiché est le **tarif sur place** (`maxPrice / 1000`, en euros).

| Cas                                         | `price`             | `status`    |
|---------------------------------------------|---------------------|-------------|
| `priceRange` contient "gratuit" / "entrée libre" | `Free`         | `free`      |
| `maxPrice == 0`                             | `Free`              | `free`      |
| `maxPrice > 0`                              | `{tarif:.2f} EUR`   | `buy_now`   |
| Disponibilité `NO_VACANCY`                  | (inchangé)          | `sold_out`  |
| Disponibilité `LAST_SEATS`                  | (inchangé)          | `buy_now`   |
| Aucun tarif disponible                      | `Price Unavailable` | selon dispo |

### Images

Le serveur requiert un préfixe de redimensionnement dans l'URL, sans quoi il retourne une erreur 404. Le script utilise `q90-w1024` (qualité 90, largeur 1024 px) :

```
https://www.citemusicale-metz.fr/assets/q90-w1024/{relativePath}
```

### Lieux

| Slug API      | `location`           | `address`                                    |
|---------------|----------------------|----------------------------------------------|
| `bam`         | `BAM - Metz`         | 1 rue de la Citadelle, 57000 Metz, France    |
| `trinitaires` | `Trinitaires - Metz` | 12 rue des Trinitaires, 57000 Metz, France   |

Le matching est fait par **préfixe** sur la clé du champ `placesNames`, ce qui couvre les variantes comme `"trinitaires, chapelle"`.

### Écriture atomique

Le fichier de sortie est d'abord écrit dans un fichier temporaire puis renommé atomiquement, ce qui protège le fichier précédent en cas de crash pendant l'écriture.

Les requêtes HTTP sont relancées jusqu'à 3 fois en cas d'échec réseau, avec un délai de 5 s entre chaque tentative.

## Sorties

| Répertoire | Fichier                                       | Format |
|------------|-----------------------------------------------|--------|
| `JSON/`    | `scrape_citemusicale_metz_concerts.json`      | JSON   |
| `CSV/`     | `scrape_citemusicale_metz_concerts.csv`       | CSV    |
| `Log/`     | `scrape_citemusicale_metz_concerts.log`       | Log    |

### Champs produits

| Champ          | Description                                                                 |
|----------------|-----------------------------------------------------------------------------|
| `id`           | Identifiant numérique extrait de l'URL API (`/api/events/{id}`)             |
| `artist`       | Nom de l'artiste + support (champs `name` + `subtitle`)                     |
| `date_live`    | Date du concert (format `YYYY-MM-DD`)                                       |
| `doors_time`   | Heure de début du concert (format `HH:MM`, ex: `20:30`)                    |
| `location`     | Nom de la salle : `BAM - Metz` ou `Trinitaires - Metz`                     |
| `address`      | Adresse de la salle                                                         |
| `genres`       | Liste des genres musicaux (tags `styles` du site, ou Deezer en fallback)    |
| `status`       | Statut billetterie : `buy_now`, `sold_out` ou `free`                        |
| `url`          | Lien vers la page de l'événement sur citemusicale-metz.fr                   |
| `buy_link`     | Lien de réservation Secutix, ou `null`                                      |
| `image`        | URL de l'image redimensionnée (1024 px de large)                            |
| `price`        | Tarif sur place (ex: `30.00 EUR`), `Free` ou `Price Unavailable`            |
| `date_created` | Horodatage UTC du scan (ISO 8601)                                           |

## Usage

```bash
# JSON (format par défaut)
python scrape_citemusicale_metz_concerts.py

# CSV
python scrape_citemusicale_metz_concerts.py -f csv

# Exclure des genres (séparés par ;)
python scrape_citemusicale_metz_concerts.py -g "Concerts"

# Exclure des statuts (séparés par ;)
python scrape_citemusicale_metz_concerts.py -s "sold_out"

# Combiner les filtres
python scrape_citemusicale_metz_concerts.py -f json -g "Concerts" -s "sold_out"
```

### Options CLI

| Option                     | Description                                                 | Défaut |
|----------------------------|-------------------------------------------------------------|--------|
| `-f`, `--format`           | Format de sortie : `json` ou `csv`                         | `json` |
| `-g`, `--exclude-genres`   | Genres à exclure, séparés par `;` (insensible à la casse)  | aucun  |
| `-s`, `--exclude-statuses` | Statuts à exclure, séparés par `;` (insensible à la casse) | aucun  |

## Dépendances

Ce script utilise **uniquement la bibliothèque standard Python** — aucune installation supplémentaire n'est requise.

- Python 3.10+
- Modules : `argparse`, `csv`, `json`, `logging`, `re`, `urllib`, `datetime`, `pathlib`, `tempfile`

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.
