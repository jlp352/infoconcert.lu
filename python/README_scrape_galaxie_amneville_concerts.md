# scrape_galaxie_amneville_concerts.py

Scraper des concerts et spectacles disponibles sur [le-galaxie.com](https://www.le-galaxie.com/evenements/) (Le Galaxie — Amnéville, France).

> Le domaine `le-galaxie.com` est un alias de `zenith-amneville.com` — les deux pointent vers le même site.

## Fonctionnement

Le script opère en cinq étapes :

1. **Collecte des pages de liste** — les événements sont listés sur `/evenements/page/{n}/`. La pagination est détectée dynamiquement à partir du HTML de la première page. Chaque carte `.card-event` fournit le titre, la catégorie, le statut billetterie (CSS) et l'URL de la page détail.
2. **Enrichissement depuis les pages détail** — chaque page détail contient un bloc JSON-LD `schema.org/Event` qui fournit la date et l'heure de début, le lien de réservation (`offers.url`) et l'image haute résolution.
3. **Récupération des prix depuis la billetterie** — le lien de réservation pointe vers `billetterie.le-galaxie.com`. Le script récupère le prix plancher (minimum) en parsant le HTML statique de cette page (site Drupal/Hubber). Deux formats d'URL sont gérés.
4. **Enrichissement des genres via l'API Deezer** — chaîne de 3 appels pour chaque artiste : `search/artist` → `artist/{id}/top` → `album/{id}` → genres. Résultats mis en cache. Fallback : `["Concerts"]`.
5. **Filtres** — les événements passés (date antérieure à aujourd'hui) sont systématiquement exclus. Des filtres optionnels par genre et par statut peuvent s'y ajouter.

### Pagination

La pagination est détectée via les liens `/evenements/page/{n}` présents dans le HTML. Le script récupère la valeur maximale `{n}` pour déterminer le nombre de pages à parcourir.

### Parsing des cartes événements

Chaque carte événement dans le HTML de liste a la structure :

```html
<div class="card-event status-on_sale" data-title="ARTISTE" data-category="Concert">
```

Le script en extrait :

- Le **titre** (`data-title`) et la **catégorie** (`data-category`)
- Le **statut** depuis la classe CSS (`status-on_sale` → `buy_now`, `status-sold_out` → `sold_out`)
- L'**URL de la page détail** depuis le premier `href` vers `/evenement/...`
- L'**image** depuis `data-src` ou `src` des balises `<img>`
- L'**identifiant** (`id`) : dernier segment de l'URL (slug WordPress)

### JSON-LD schema.org/Event (pages détail)

Chaque page détail contient un bloc `<script type="application/ld+json">` de type `Event` :

```json
{
  "@type": "Event",
  "name": "ARTISTE",
  "startDate": "2026-05-15T20:00:00.000000Z",
  "offers": {
    "availability": "https://schema.org/InStock",
    "url": "https://billetterie.le-galaxie.com/fr/product/5050/...",
    "price": 0
  },
  "image": ["https://www.le-galaxie.com/wp-content/uploads/..."]
}
```

> **Note :** Le champ `price` du JSON-LD vaut toujours `0` (limitation du plugin WordPress). Le vrai prix est récupéré séparément depuis la billetterie.

> **Note :** Le site stocke l'heure locale en UTC dans `startDate` (pratique courante avec WordPress + ACF). Aucune conversion de fuseau n'est appliquée.

### Récupération des prix (billetterie.le-galaxie.com)

La billetterie utilise **Drupal + Hubber** et sert les prix dans le HTML statique. Deux formats d'URL sont gérés :

| Type d'URL | Sélecteur HTML | Exemple |
|---|---|---|
| `/fr/product/{id}/...` | `<span class="price-amount">45 €</span>` | Lorie, Kendji Girac… |
| `/fr/manifestation/{id}/...` | `<em class="placeholder">44 €</em>` dans `.manifestation-price` | Mousquetaire… |

Le script extrait tous les prix disponibles et retourne le **minimum** (prix plancher).

### Prix et statut

| Cas | `price` | `status` |
|---|---|---|
| Prix trouvé sur la billetterie | `{min:.2f} EUR` (ex : `45.00 EUR`) | `buy_now` |
| Billetterie externe (trium.fr, gdp.fr…) | `Price Unavailable` | `buy_now` |
| Aucun `buy_link` disponible | `Price Unavailable` | `buy_now` |
| Événement passé (403 Forbidden) | `Price Unavailable` | `buy_now` |
| Classe CSS `status-sold_out` ou JSON-LD `SoldOut` | `Price Unavailable` | `sold_out` |

### Filtrage des événements passés

Après l'assemblage, les événements dont `date_live` est strictement antérieure à la date du jour (UTC) sont automatiquement exclus. Les événements sans `date_live` (JSON-LD absent ou date non parsée) sont également exclus. Le nombre d'événements supprimés est tracé dans le log.

### Fichier de sortie

Le fichier est écrit dans un fichier temporaire puis renommé atomiquement. Cela protège le fichier existant en cas de crash pendant l'écriture.

Les requêtes HTTP sont relancées jusqu'à 3 fois en cas d'échec réseau, avec un délai de 5 s entre chaque tentative. Un délai de politesse de 0,4 s est appliqué entre chaque requête vers `le-galaxie.com` et `billetterie.le-galaxie.com`.

## Sorties

| Répertoire | Fichier | Format |
|---|---|---|
| `JSON/` | `scrape_galaxie_amneville_concerts.json` | JSON |
| `CSV/` | `scrape_galaxie_amneville_concerts.csv` | CSV |
| `Log/` | `scrape_galaxie_amneville_concerts.log` | Log |

### Champs produits

| Champ | Description |
|---|---|
| `id` | Slug WordPress extrait de l'URL (ex : `kendji-girac`) |
| `artist` | Titre de l'événement (nom de l'artiste ou intitulé du spectacle) |
| `date_live` | Date de l'événement (format `YYYY-MM-DD`) |
| `doors_time` | Heure de début (format `HH:MM`, ex : `20:00`) — `null` si absente |
| `location` | Nom de la salle : `Le Galaxie - Amnéville` |
| `address` | Adresse fixe : `Rue des Artistes, 57360 Amnéville, France` |
| `genres` | Liste des genres musicaux via Deezer (séparés par `;` en CSV) |
| `status` | Statut billetterie : `buy_now` ou `sold_out` |
| `url` | Lien vers la page détail sur `le-galaxie.com` |
| `buy_link` | Lien de réservation sur `billetterie.le-galaxie.com` (ou billetterie externe), ou `null` |
| `image` | URL de l'image de couverture (CDN `le-galaxie.com/wp-content/...`) |
| `price` | Prix plancher (ex : `45.00 EUR`) ou `Price Unavailable` |
| `date_created` | Horodatage UTC du scan (ISO 8601) |

## Usage

```bash
# JSON (format par défaut)
python scrape_galaxie_amneville_concerts.py

# CSV
python scrape_galaxie_amneville_concerts.py -f csv

# Exclure des genres (séparés par ;)
python scrape_galaxie_amneville_concerts.py -g "Concerts"

# Exclure des statuts (séparés par ;)
python scrape_galaxie_amneville_concerts.py -s "sold_out"

# Combiner les filtres
python scrape_galaxie_amneville_concerts.py -f json -g "Concerts" -s "sold_out"
```

### Options CLI

| Option | Description | Défaut |
|---|---|---|
| `-f`, `--format` | Format de sortie : `json` ou `csv` | `json` |
| `-g`, `--exclude-genres` | Genres à exclure, séparés par `;` (insensible à la casse) | aucun |
| `-s`, `--exclude-statuses` | Statuts à exclure, séparés par `;` (insensible à la casse) | aucun |

## Dépendances

Ce script utilise **uniquement la bibliothèque standard Python** — aucune installation supplémentaire n'est requise.

- Python 3.10+
- Modules : `argparse`, `csv`, `html`, `json`, `logging`, `re`, `urllib`, `datetime`, `pathlib`, `tempfile`

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.
