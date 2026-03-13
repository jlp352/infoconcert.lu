# scrape_arche_villerupt_concerts.py

Scraper des concerts et spectacles disponibles sur [l-arche.art](https://l-arche.art/events) (L'Arche — Villerupt, France).

## Fonctionnement

Le script opère en quatre étapes :

1. **Initialisation de session WinterCMS** — un GET initial sur `/events` récupère le cookie de session `winter_session` nécessaire aux requêtes AJAX suivantes.
2. **Collecte par catégorie via AJAX** — pour chaque catégorie scrapée, un POST est envoyé avec le header `X-WINTER-REQUEST-HANDLER: onRefreshData`. La réponse JSON contient la clé `#events-list` avec le HTML de la liste des événements à venir. Les événements des deux catégories sont fusionnés avec dédoublonnage par ID.
3. **Récupération des liens de réservation** — chaque page détail d'événement est visitée pour en extraire le lien billetterie spécifique (`billetterie.l-arche.art/agenda/...`).
4. **Enrichissement des genres via l'API Deezer** — chaîne de 3 appels pour chaque artiste : `search/artist` → `artist/{id}/top` → `album/{id}` → genres. Résultats mis en cache. Fallback : `["Concerts"]`.

### Catégories scrapées

| `thematicAgenda` | Label      |
|------------------|------------|
| `2`              | Concert    |
| `6`              | Spectacle  |

### Mécanisme AJAX (WinterCMS)

Le site utilise **WinterCMS** (successeur d'OctoberCMS). Les événements ne sont pas dans le HTML initial — ils sont chargés via un appel AJAX POST :

```
POST https://l-arche.art/events
Headers:
  X-WINTER-REQUEST-HANDLER: onRefreshData
  X-Requested-With: XMLHttpRequest
  Content-Type: application/x-www-form-urlencoded
Body:
  production=&thematicAgenda=2   (ou 6 pour Spectacle)
```

La réponse est un JSON `{"#events-list": "<html>..."}` contenant les cartes d'événements.

### Stratégie de parsing HTML

Chaque carte d'événement est une balise `<a class="block event-item">`. Le script en extrait :

- L'**identifiant** et l'**URL** depuis l'attribut `href` (`/event/{slug}/{id}`)
- La **date** et l'**heure** depuis le bloc de texte de la carte (format `DD.MM.YY` et `HH:MM`)
- Le **titre** depuis la balise `<h2>`
- La **catégorie** depuis `<p class="... thematic ...">`
- Le **prix** depuis `<p class="... prices ...">` (texte libre, ex : `Tarif : 15 euros / gratuit -16 ans`)
- L'**image** depuis l'attribut `style="background-image: url(...)"` de la carte
- Le **lien de réservation** depuis la page détail : `<a href="https://billetterie.l-arche.art/agenda/...">`

### Prix et statut

| Cas                                  | `price`             | `status`    |
|--------------------------------------|---------------------|-------------|
| Texte contient `complet` / `sold out`| `Price Unavailable` | `sold_out`  |
| Texte contient `gratuit`             | `Free`              | `free`      |
| Montant `N euros` ou `N €` trouvé   | `{N:.2f} EUR`       | `buy_now`   |
| Aucun motif reconnu                  | `Price Unavailable` | `buy_now`   |

### Fichier de sortie

Le fichier est écrit dans un fichier temporaire puis renommé atomiquement. Cela protège le fichier existant en cas de crash pendant l'écriture.

Les requêtes HTTP sont relancées jusqu'à 3 fois en cas d'échec réseau, avec un délai de 5 s entre chaque tentative.

## Sorties

| Répertoire | Fichier                                      | Format |
|------------|----------------------------------------------|--------|
| `JSON/`    | `scrape_arche_villerupt_concerts.json`       | JSON   |
| `CSV/`     | `scrape_arche_villerupt_concerts.csv`        | CSV    |
| `Log/`     | `scrape_arche_villerupt_concerts.log`        | Log    |

### Champs produits

| Champ          | Description                                                                        |
|----------------|------------------------------------------------------------------------------------|
| `id`           | Identifiant numérique extrait de l'URL (`/event/{slug}/{id}`)                      |
| `artist`       | Titre de l'événement (nom de l'artiste ou intitulé du spectacle)                   |
| `date_live`    | Date de l'événement (format `YYYY-MM-DD`)                                          |
| `doors_time`   | Heure de début (format `HH:MM`, ex : `20:30`) — `null` si absente                 |
| `location`     | Nom de la salle : `L'Arche - Villerupt`                                            |
| `address`      | Adresse fixe : `Esplanade Nino Rota, 54190 Villerupt, France`                      |
| `genres`       | Liste des genres musicaux via Deezer (séparés par `;` en CSV)                      |
| `status`       | Statut billetterie : `buy_now`, `free` ou `sold_out`                               |
| `url`          | Lien vers la page détail de l'événement sur `l-arche.art`                          |
| `buy_link`     | Lien de réservation sur `billetterie.l-arche.art/agenda/...`, ou `null`            |
| `image`        | URL de l'image de couverture (CDN `l-arche.art/storage/...`)                       |
| `price`        | Prix (ex : `25.00 EUR`), `Free` ou `Price Unavailable`                             |
| `date_created` | Horodatage UTC du scan (ISO 8601)                                                  |

## Usage

```bash
# JSON (format par défaut)
python scrape_arche_villerupt_concerts.py

# CSV
python scrape_arche_villerupt_concerts.py -f csv

# Exclure des genres (séparés par ;)
python scrape_arche_villerupt_concerts.py -g "Concerts"

# Exclure des statuts (séparés par ;)
python scrape_arche_villerupt_concerts.py -s "sold_out"

# Combiner les filtres
python scrape_arche_villerupt_concerts.py -f json -g "Concerts" -s "sold_out"
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
- Modules : `argparse`, `csv`, `html`, `http.cookiejar`, `json`, `logging`, `re`, `urllib`, `datetime`, `pathlib`, `tempfile`

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.
