# Documentation — Site Web infoConcert.lu

> Pour la procédure d'installation complète et le lancement du serveur web, voir [README.md](README.md).

## Structure des fichiers

```
Web/
├── index.html              # Page principale (liste des concerts)
├── venues.html             # Page des salles de concert
├── contact.html            # Page de contact
├── legalnotice.html        # Mentions légales (cookies, CGU, RGPD)
├── IN/
│   └── concerts.json       # Données source chargées par index.html
├── image/
│   ├── favicon.svg
│   ├── logo/               # Logo du site
│   └── location/           # Photos des salles de concert
└── website.md              # Ce fichier
```

---

## index.html — Page principale

### Vue d'ensemble

Page statique qui charge dynamiquement le fichier `IN/concerts.json` et affiche l'ensemble des concerts à venir au Luxembourg. Toute la logique est en JavaScript vanilla, sans dépendance externe (pas de framework).

### Données source

Le fichier `IN/concerts.json` est généré par le script Python `merge.py`. Il contient :

| Champ        | Description                              |
|--------------|------------------------------------------|
| `scraped_at` | Horodatage de la dernière mise à jour    |
| `concerts`   | Liste des concerts (voir champs ci-dessous) |

Chaque concert contient : `id`, `artist`, `date_live`, `doors_time`, `location`, `address`, `genres`, `status`, `url`, `buy_link`, `image`, `price`, `date_created`, `track_id`, `track_id1`.

---

### Fonctionnalités

#### 1. Affichage des concerts par mois

Les concerts sont triés chronologiquement et regroupés par mois (`Janvier 2026`, `Février 2026`, etc.). Chaque groupe affiche ses concerts dans une grille responsive (auto-fit, minimum 350 px par carte).

**Chaque carte de concert affiche :**
- Image de l'événement (ou dégradé coloré en fallback)
- Badge de statut (`Billets Disponibles` / `Complet` / `Mise en vente prochaine`)
- Date du concert
- Nom de l'artiste
- Badges de genres musicaux
- Salle et adresse
- Lecteur audio (si disponible)
- Prix indicatif + boutons d'action
- Bouton « Détails du concert »

Un clic sur la carte (hors boutons) ouvre la page officielle du concert dans un nouvel onglet.

---

#### 2. Filtres avancés

Barre de filtres composée de trois couches :

**Filtres rapides (boutons)**
- `Ce mois` — affiche uniquement les concerts du mois en cours
- `Mois prochain` — affiche uniquement les concerts du mois suivant

**Filtres par dropdown (multi-sélection)**

| Dropdown | Contenu                                                              |
|----------|----------------------------------------------------------------------|
| Mois     | Liste de tous les mois disponibles dans le JSON (format `Mois AAAA`) |
| Genre    | Groupes de genres : Rock/Metal, Électro/Dance, Hip-Hop/R&B, Pop/Chanson, Jazz/Blues, Reggae/Latino, Famille/Classique, Non classifié |
| Salle    | Salles principales (≥ 2 concerts) + groupe « Autres » pour les salles ponctuelles |

Chaque dropdown affiche un badge numérique indiquant le nombre de filtres actifs. Les filtres se combinent (ET entre catégories, OU au sein d'une même catégorie).

**Pills actives**
Les filtres appliqués apparaissent sous forme de pastilles (`pills`) cliquables permettant de les supprimer individuellement. Un bouton `Réinitialiser` global supprime tous les filtres actifs.

---

#### 3. Recherche rapide d'artiste (barre de navigation)

Champ de recherche intégré dans la barre de navigation avec :
- Résultats en temps réel dès la saisie (jusqu'à 8 résultats)
- Affichage : miniature, nom de l'artiste (termes trouvés surlignés), salle, date courte
- Navigation clavier : `↑` `↓` pour se déplacer, `Entrée` pour sélectionner, `Échap` pour fermer
- Bouton `✕` pour effacer la recherche

Sélectionner un résultat active un **filtre par artiste** : seuls les concerts de cet artiste sont affichés, accompagnés d'une bannière indiquant l'artiste filtré et d'un bouton « Voir tous les concerts » pour réinitialiser.

---

#### 4. Lecteur audio (extraits Deezer)

Chaque concert dont les champs `track_id` ou `track_id1` sont renseignés propose un lecteur audio intégré.

**Fonctionnement :**
1. Clic sur le bouton `▶ Extrait` → appel JSONP à l'API Deezer pour récupérer l'URL de prévisualisation (30 s)
2. Le mini-player s'ouvre et la lecture démarre automatiquement
3. Si deux extraits sont disponibles, ils sont accessibles via deux boutons `Extrait 1` / `Extrait 2`
4. À la fin d'un extrait, le suivant démarre automatiquement si disponible

**Contrôles du mini-player :**
- Barre de progression cliquable (scrub)
- Bouton Play / Pause
- Bouton Mute / Unmute
- Slider de volume
- Compteur de temps écoulé

Un seul lecteur peut être actif à la fois ; démarrer un nouvel extrait arrête le précédent. Les URLs de prévisualisation sont mises en cache en mémoire pour éviter des appels API répétés.

---

#### 5. Multi Langues

Le site est disponible en trois langues :

| Code | Langue   |
|------|----------|
| `fr` | Français |
| `en` | English  |
| `de` | Deutsch  |

**Sélection de la langue :**
- Boutons `FR` / `EN` / `DE` dans la navigation
- Détection automatique à la première visite selon `navigator.languages`
- La langue choisie est mémorisée dans `localStorage` (`lang`)

Tous les textes de l'interface (navigation, filtres, cartes, footer, messages d'erreur, mois) sont traduits. Un changement de langue re-rend dynamiquement l'ensemble de l'interface sans rechargement de page.

---

#### 6. Gestion des cookies et consentement RGPD

Un bandeau de consentement apparaît à la première visite. Il propose deux options :

| Action   | Effet                                                                      |
|----------|----------------------------------------------------------------------------|
| Accepter | Enregistre `ic_consent=true`, un UID anonyme (`ic_uid`), et la date de visite (`ic_last_visit`) — tous expiration 365 jours |
| Refuser  | Enregistre uniquement `ic_consent=false` — aucun tracking                 |

---

#### 7. Toast « Nouveaux concerts »

Lorsque l'utilisateur a accepté les cookies et revient sur le site après une précédente visite, un toast (notification) apparaît automatiquement 800 ms après le chargement des concerts si de nouveaux concerts ont été ajoutés depuis la dernière visite (comparaison via `date_created`).

Le toast affiche :
- Nombre de nouveaux concerts
- Prévisualisation des 6 premiers (miniature, artiste, salle, date)
- Barre de progression (fermeture automatique après 8 secondes)
- Bouton `Voir` pour afficher uniquement les nouveaux concerts

Cliquer sur un concert dans le toast active le filtre par artiste correspondant.

---

#### 8. Badges de statut de billetterie

| Statut source (JSON)              | Statut normalisé | Affichage           | Couleur  |
|-----------------------------------|------------------|---------------------|----------|
| `buynow`, `lasttickets`, `newdate`| `available`      | Billets Disponibles | Jaune    |
| `soldout`, `waitinglist`          | `full`           | Complet             | Rouge    |
| Tout autre valeur non vide        | `soon`           | Mise en vente prochaine | Orange |

---

#### 9. Boutons d'action par concert

| Bouton       | Condition d'affichage          | Action                             |
|--------------|--------------------------------|------------------------------------|
| 🎟 Réserver  | Concert ni complet ni à venir  | Ouvre `buy_link` (nouvel onglet)   |
| 🔄 TicketSwap | Concert non « à venir »       | Recherche Google sur ticketswap.com |
| Détails du concert | Toujours visible        | Ouvre `url` officiel (nouvel onglet) |

---

#### 10. Footer

- Copyright `© 2026 infoConcert.lu`
- Version de l'application (`APP_VERSION = '1.0'`)
- Date et heure de la dernière mise à jour (extraite de `scraped_at` dans le JSON)
- Liens vers les pages légales : Charte cookies · CGU · Protection des données

---

### Palette de couleurs

| Variable CSS    | Valeur     | Usage                        |
|-----------------|------------|------------------------------|
| `--primary`     | `#FF3366`  | Rouge-rose — accent principal |
| `--secondary`   | `#FFD700`  | Jaune doré — prix, dates     |
| `--accent`      | `#00D9FF`  | Cyan — lecteur audio, lang   |
| `--dark`        | `#0A0A0A`  | Fond général                 |
| `--gray`        | `#1A1A1A`  | Fond des cartes              |
| `--light`       | `#F5F5F5`  | Texte principal              |

### Polices

| Police       | Usage                          |
|--------------|--------------------------------|
| `Bebas Neue` | Titres, logo, dates            |
| `Outfit`     | Corps de texte, boutons, labels|

---

### Architecture JavaScript (résumé)

| Fonction / Bloc              | Rôle                                                     |
|------------------------------|----------------------------------------------------------|
| `loadConcerts()`             | Fetch asynchrone de `IN/concerts.json`, tri chronologique, initialisation |
| `buildFilters(concerts)`     | Construction et câblage de toute la barre de filtres     |
| `applyAdvFilters()`          | Applique tous les filtres actifs et appelle `renderConcerts()` |
| `renderConcerts(concerts)`   | Génère le HTML des groupes par mois et des cartes        |
| `buildCard(concert, index)`  | Génère le HTML d'une carte concert individuelle          |
| `filterByArtist(name)`       | Active le filtre par artiste depuis la recherche ou le toast |
| `fetchPreviewByTrackId(id)`  | Appel JSONP à l'API Deezer, avec cache mémoire           |
| `playUrl(btn, url, trackBtn)`| Lance la lecture audio et câble les contrôles du player  |
| `showToast(newConcerts)`     | Affiche le toast des nouveaux concerts avec timer 8s     |
| `applyTranslations()`        | Applique la langue active sur tout le DOM                |
| `detectLang()`               | Détecte la langue depuis `localStorage` ou `navigator`  |
| `setCookie / getCookie`      | Utilitaires de gestion des cookies RGPD                  |
