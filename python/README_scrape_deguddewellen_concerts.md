# scrape_deguddewellen_concerts.py

Scraper des concerts disponibles sur [deguddewellen.lu](https://deguddewellen.lu/agenda).

## Fonctionnement

Le script opère en quatre étapes :

1. **Scraping HTML de la page agenda** — parse directement la page `/agenda` (site Webflow CMS, pas d'API REST) pour extraire la liste complète de tous les événements. Tous les items sont présents dans le DOM statique sous forme de `<div class="w-dyn-item">`, filtrés côté client par JavaScript. Un seul appel réseau suffit.
2. **Filtres** — ne sont conservés que les événements réunissant les quatre conditions suivantes :
   - Catégorie : `Concert` ou `Clubbing` (exclu : `Other` — quizzes, soirées jeux, etc.)
   - Lieu : `De Gudde Wëllen` uniquement (exclu : `Buvette`, `mikrokosmos`)
   - Date : future uniquement (≥ aujourd'hui)
   - Titre ne contenant pas `CANCELLED`
3. **Scraping des pages individuelles** — pour chaque concert retenu, visite la page `/events/{slug}` afin d'extraire l'heure d'ouverture des portes, l'heure de début du show, l'image, le lien de billetterie (`loveyourartist.com`) et les tags de genres. Parallélisé sur 8 threads.
4. **Récupération du prix et enrichissement des genres** :
   - **Prix** : appel à la page `loveyourartist.com` du concert (priorité 1) → extraction depuis l'`og:title` (`"from €X.XX"` → `"X.XX EUR"`). Fallback : recherche de texte `PRESALE:`, `FREE ENTRY`, `GRATUIT` sur la page DGW.
   - **Genres** : tags présents sur la page de l'événement DGW (priorité 1). Si absents, enrichissement via l'API Deezer (chaîne `search/artist` → `artist/{id}/top` → `album/{id}` → genres). Résultats mis en cache. Fallback : `["Concerts"]`.

L'adresse physique est fixe pour tous les concerts : `17, rue du St. Esprit, L-1475 Luxembourg`.

### Filtrage des catégories

Le site De Gudde Wëllen propose trois catégories : `Concert`, `Clubbing`, `Other`. Le scraper conserve `Concert` et `Clubbing`. La catégorie `Other` regroupe les événements non musicaux (quiz, board game nights, café philo, etc.) et est systématiquement exclue.

### Récupération du prix

Le prix est extrait depuis la page `loveyourartist.com` associée au bouton "Buy Now" :

1. Le lien `loveyourartist.com` est récupéré depuis l'attribut `href` de l'élément `.ticket-link` sur la page DGW
2. La page `loveyourartist.com` est chargée et l'`og:title` est parsé — format : `"Tickets for X @ De Gudde Wëllen | ... | from €Y.YY"`
3. Le montant est extrait et reformaté en `"Y.YY EUR"`
4. Fallback si pas de lien LYA : recherche de patterns textuels sur la page DGW (`PRESALE:`, `FREE ENTRY`, `GRATUIT`)
5. Fallback final : `Price Unavailable`

### Fichier de sortie

Le fichier de sortie est d'abord écrit dans un fichier temporaire, puis renommé atomiquement. Cela protège le fichier existant en cas de crash pendant l'écriture.

Les requêtes HTTP sont relancées jusqu'à 3 fois en cas d'échec (délai de 5 s entre chaque tentative). Le scraping des pages individuelles est parallélisé sur 8 threads.

## Sorties

| Répertoire | Fichier                                    | Format |
|------------|--------------------------------------------|--------|
| `JSON/`    | `scrape_deguddewellen_concerts.json`       | JSON   |
| `CSV/`     | `scrape_deguddewellen_concerts.csv`        | CSV    |
| `Log/`     | `scrape_deguddewellen_concerts.log`        | Log    |

### Champs produits

| Champ          | Description                                                                       |
|----------------|-----------------------------------------------------------------------------------|
| `id`           | Slug extrait de l'URL de l'événement (ex: `conic-rose`)                          |
| `artist`       | Nom de l'artiste / événement (préfixe `SOLD OUT -` supprimé)                     |
| `date_live`    | Date et heure de début du show (format `YYYY-MM-DD HH:MM`)                       |
| `doors_time`   | Heure d'ouverture des portes (ex: `20:30`)                                        |
| `location`     | Nom de la salle : `De Gudde Wëllen`                                               |
| `address`      | Adresse fixe : `17, rue du St. Esprit, L-1475 Luxembourg`                        |
| `genres`       | Tags de la page DGW, ou genres Deezer en fallback (séparés par `;` en CSV)       |
| `status`       | Statut billetterie : `buy_now`, `sold_out` ou `free`                             |
| `url`          | Lien vers la page de l'événement sur deguddewellen.lu                            |
| `buy_link`     | Lien de réservation (`loveyourartist.com`), ou URL de l'événement DGW en fallback |
| `image`        | URL de l'image de l'événement (Webflow CDN)                                      |
| `price`        | Prix minimum (ex: `9.00 EUR`) ou `Price Unavailable`                             |
| `date_created` | Horodatage UTC du scan                                                            |

## Usage

```bash
# JSON (format par défaut)
python scrape_deguddewellen_concerts.py

# CSV
python scrape_deguddewellen_concerts.py -f csv

# Exclure des genres (séparés par ;)
python scrape_deguddewellen_concerts.py -g "Pop; Dance"

# Exclure des statuts (séparés par ;)
python scrape_deguddewellen_concerts.py -s "sold_out"

# Combiner les filtres
python scrape_deguddewellen_concerts.py -f json -g "Pop" -s "sold_out"
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
- Modules : `argparse`, `csv`, `json`, `logging`, `re`, `urllib`, `concurrent.futures`, `html`, `pathlib`, `tempfile`

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.
