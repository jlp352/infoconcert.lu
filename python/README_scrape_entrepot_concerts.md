# scrape_entrepot_concerts.py

Scraper des concerts disponibles sur [entrepotarlon.be](https://www.entrepotarlon.be/).

## Fonctionnement

Le script opère en deux étapes :

1. **Scraping HTML de la page agenda** — parse directement la page `/agenda.php` (pas d'API REST, RSS limité à 10 items) pour extraire la liste complète des concerts à venir : identifiant, artiste, date, heure, image, prix, statut, lien de billetterie.
2. **Enrichissement des genres via l'API Deezer** — chaîne de 3 appels pour chaque artiste : `search/artist` → `artist/{id}/top` → `album/{id}` → genres. Résultats mis en cache. Fallback : `["Concerts"]`.

L'adresse physique est fixe pour tous les concerts : `6700 Arlon, Belgique`.

### Stratégie de parsing HTML

La page `agenda.php` structure son contenu en sections mensuelles séparées par des ancres `<a name="monthYYYYMM">`. À l'intérieur de chaque section, les concerts sont délimités par des `<div class="agendasep">`.

Pour chaque bloc de concert, le script extrait :
- L'**identifiant** depuis le lien `concert.php?id={id}`
- La **date** depuis la balise `class="showdate"` (concert mono-jour) ou `class="gothic24sh colorthis"` (festival multi-jours — seule la première date est retenue)
- L'**heure** depuis la balise `<sup class="showhour">` (formats : `20H30`, `20H`, etc.)
- L'**image** depuis `<div class="agendaflyer">` — le préfixe de taille `125w` est remplacé par `474x474` pour obtenir une résolution supérieure
- L'**artiste** (headliner) depuis `<p class="agendafirstshow">`
- Le **prix** depuis le motif `XX€ / XX€` (préféré = prix présale ; 0 € → `Free`)
- La mention **CONCERT GRATUIT** pour les événements sans billetterie payante
- Le **lien de réservation** utick depuis `https://shop.utick.net/...`

### Encodage

La page est servie en `iso-8859-15` (déclaré dans le `<meta charset>`). Le script lit la réponse HTTP avec cet encodage avant tout traitement.

### Prix et statut

| Cas                                | `price`              | `status`   |
|------------------------------------|----------------------|------------|
| `CONCERT GRATUIT` présent          | `Free`               | `free`     |
| Prix présale = 0 €                 | `Free`               | `free`     |
| Prix présale > 0 €                 | `{prix:.2f} EUR`     | `buy_now`  |
| Aucun motif prix + lien utick      | `Price Unavailable`  | `buy_now`  |
| Aucun motif prix, pas de lien utick| `Price Unavailable`  | `null`     |

### Fichier de sortie

Le fichier est d'abord écrit dans un fichier temporaire puis renommé atomiquement. Cela protège le fichier existant en cas de crash pendant l'écriture.

Les requêtes HTTP sont relancées jusqu'à 3 fois en cas d'échec réseau, avec un délai de 5 s entre chaque tentative.

## Sorties

| Répertoire | Fichier                             | Format |
|------------|-------------------------------------|--------|
| `JSON/`    | `scrape_entrepot_concerts.json`     | JSON   |
| `CSV/`     | `scrape_entrepot_concerts.csv`      | CSV    |
| `Log/`     | `scrape_entrepot_concerts.log`      | Log    |

### Champs produits

| Champ          | Description                                                           |
|----------------|-----------------------------------------------------------------------|
| `id`           | Identifiant numérique extrait de l'URL (`concert.php?id=XXXX`)        |
| `artist`       | Nom de l'artiste / headliner (balise `agendafirstshow`)               |
| `date_live`    | Date du concert (format `YYYY-MM-DD`)                                 |
| `doors_time`   | Heure du concert (format `HH:MM`, ex: `20:30`) — `null` pour les festivals multi-jours |
| `location`     | Nom de la salle : `L'Entrepôt`                                        |
| `address`      | Adresse fixe : `6700 Arlon, Belgique`                                 |
| `genres`       | Liste des genres musicaux via Deezer (séparés par `;` en CSV)         |
| `status`       | Statut billetterie : `buy_now`, `free` ou `null`                      |
| `url`          | Lien vers la page du concert (`concert.php?id=XXXX`)                  |
| `buy_link`     | Lien de réservation utick, ou `null`                                  |
| `image`        | URL de l'image en résolution `474x474` (CDN losange.net)              |
| `price`        | Prix présale (ex: `20.00 EUR`), `Free` ou `Price Unavailable`         |
| `date_created` | Horodatage UTC du scan (ISO 8601)                                     |

## Usage

```bash
# JSON (format par défaut)
python scrape_entrepot_concerts.py

# CSV
python scrape_entrepot_concerts.py -f csv

# Exclure des genres (séparés par ;)
python scrape_entrepot_concerts.py -g "Concerts"

# Exclure des statuts (séparés par ;)
python scrape_entrepot_concerts.py -s "sold_out"

# Combiner les filtres
python scrape_entrepot_concerts.py -f json -g "Concerts" -s "sold_out"
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
- Modules : `argparse`, `csv`, `html`, `json`, `logging`, `re`, `urllib`, `datetime`, `pathlib`, `tempfile`

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.
