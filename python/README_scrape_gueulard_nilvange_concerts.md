# scrape_gueulard_nilvange_concerts.py

Scraper des concerts disponibles sur [legueulard.fr](https://legueulard.fr/web/).

## Fonctionnement

Le script opère en quatre étapes :

1. **API WordPress REST** — récupère tous les posts de la catégorie `concert` (ID 2) via `/wp-json/wp/v2/posts`. Le champ `date` WordPress correspond à la date/heure de l'événement. Les images sont incluses directement via l'option `_embed=1`. La pagination est gérée via le header `X-WP-TotalPages`.
2. **Filtrage** — suppression des événements dont la date est antérieure à aujourd'hui.
3. **Scraping HTML par concert** — récupère la page HTML de chaque événement à venir pour extraire le prix et le statut. Le prix (ex : `TARIF PLEIN : 10€ | RÉDUIT : 5€`) est stocké dans un champ WordPress custom non exposé par l'API REST ; il est affiché sur la page via `<i class="icon-money"></i><span>…</span>`.
4. **Enrichissement des genres via l'API Deezer** — chaîne de 3 appels pour chaque artiste : `search/artist` → `artist/{id}/top` → `album/{id}` → genres. Résultats mis en cache. Fallback : `["Concerts"]`.

L'adresse physique est fixe pour tous les concerts : `14 rue Clémenceau, 57240 Nilvange, France`.

### Architecture du site

Le Gueulard utilise WordPress. Les posts de type concert sont publiés avec pour date de publication la date/heure de l'événement. L'API REST retourne ces posts dans l'ordre chronologique (`orderby=date&order=asc`), y compris les publications futures.

Le prix n'est **pas** dans le contenu du post (`content.rendered`) retourné par l'API — il provient d'un champ meta WordPress affiché uniquement dans le template HTML.

### Extraction du prix

Deux sources sont interrogées, par ordre de priorité :

1. **Span `icon-money`** — balise `<span>` immédiatement après `<i class="icon-money">` dans la page HTML. Contient le tarif structuré (ex : `TARIF PLEIN : 10€ | RÉDUIT : 5€`) ou une indication textuelle (`SUR RÉSERVATION`, `ENTRÉE LIBRE`…).
2. **Fallback corps du post** — si le span ne contient pas de montant, le script cherche un motif `N€` dans les nœuds texte de la page HTML (entre `>` et `<`) pour couvrir les cas où le prix est mentionné dans la description (ex : `Repas & concert – 30 €`).

> **Note technique :** le `<div itemprop="summary">` contient des `<div>` imbriqués. Il n'est pas extrait par regex (un regex non-greedy s'arrêterait à la première `</div>` interne) — le script cherche directement dans le HTML complet de la page.

### Prix et statut

| Cas                                          | `price`             | `status`   |
|----------------------------------------------|---------------------|------------|
| `ENTRÉE LIBRE` / `GRATUIT` / `ACCÈS LIBRE`  | `Free`              | `free`     |
| `\bCOMPLET\b` / `SOLD OUT`                  | `Price Unavailable` | `sold_out` |
| Prix numérique > 0 trouvé                    | `{prix:.2f} EUR`    | `buy_now`  |
| Prix = 0 €                                   | `Free`              | `free`     |
| `SUR RÉSERVATION` sans prix                  | `Price Unavailable` | `buy_now`  |
| Aucune information de prix                   | `Price Unavailable` | `null`     |

> `\bCOMPLET\b` utilise une word boundary pour ne pas matcher `complete` dans le JavaScript de la page.

### Lien d'achat (`buy_link`)

Si un lien vers une plateforme de billetterie connue est trouvé dans la page (helloasso, billetweb, weezevent, shotgun, digitick, fnac spectacles, ticketmaster, yurplan, madate), il est utilisé. Sinon, `buy_link` prend la valeur de l'URL de la page du concert.

### Fichier de sortie

Le fichier est d'abord écrit dans un fichier temporaire puis renommé atomiquement. Cela protège le fichier existant en cas de crash pendant l'écriture.

Les requêtes HTTP sont relancées jusqu'à 3 fois en cas d'échec réseau, avec un délai de 5 s entre chaque tentative.

## Sorties

| Répertoire | Fichier                                       | Format |
|------------|-----------------------------------------------|--------|
| `JSON/`    | `scrape_gueulard_nilvange_concerts.json`      | JSON   |
| `CSV/`     | `scrape_gueulard_nilvange_concerts.csv`       | CSV    |
| `Log/`     | `scrape_gueulard_nilvange_concerts.log`       | Log    |

### Champs produits

| Champ          | Description                                                                      |
|----------------|----------------------------------------------------------------------------------|
| `id`           | Identifiant WordPress du post (entier)                                           |
| `artist`       | Titre du post WordPress (nom de l'artiste / de l'événement)                     |
| `date_live`    | Date de l'événement (format `YYYY-MM-DD`, extraite du champ `date` WordPress)   |
| `doors_time`   | Heure de l'événement (format `HH:MM`) — `null` si heure = `00:00`              |
| `location`     | Nom de la salle : `Le Gueulard - Nilvange`                                      |
| `address`      | Adresse fixe : `14 rue Clémenceau, 57240 Nilvange, France`                      |
| `genres`       | Liste des genres musicaux via Deezer (séparés par `;` en CSV)                   |
| `status`       | Statut billetterie : `buy_now`, `sold_out`, `free` ou `null`                    |
| `url`          | Lien vers la page du concert sur legueulard.fr                                  |
| `buy_link`     | Lien billetterie externe, ou URL de la page du concert si absent                |
| `image`        | URL de l'image mise en avant (featured media, via `_embed`)                     |
| `price`        | Prix plein tarif (ex : `10.00 EUR`), `Free` ou `Price Unavailable`              |
| `date_created` | Horodatage UTC du scan (ISO 8601)                                               |

## Usage

```bash
# JSON (format par défaut)
python scrape_gueulard_nilvange_concerts.py

# CSV
python scrape_gueulard_nilvange_concerts.py -f csv

# Exclure des genres (séparés par ;)
python scrape_gueulard_nilvange_concerts.py -g "Concerts"

# Exclure des statuts (séparés par ;)
python scrape_gueulard_nilvange_concerts.py -s "sold_out"

# Combiner les filtres
python scrape_gueulard_nilvange_concerts.py -f json -g "Concerts" -s "sold_out"
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
- Modules : `argparse`, `csv`, `html`, `json`, `logging`, `re`, `urllib`, `datetime`, `pathlib`, `tempfile`

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.
