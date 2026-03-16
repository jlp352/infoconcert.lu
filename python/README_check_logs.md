# check_logs.py — Alertes d'erreurs via ntfy

Analyse les fichiers de log après chaque exécution des scrapers et envoie une notification push sur Android (via ntfy) si de nouvelles lignes `[ERROR]` sont détectées.

## Fonctionnement

Le script effectue deux vérifications indépendantes à chaque exécution :

**1. Analyse des logs**
- Lit `Log/.alert_state.json` pour connaître la position de la dernière lecture dans chaque fichier `.log`
- Parcourt les nouvelles lignes uniquement (depuis l'offset mémorisé)
- Filtre les lignes `[ERROR]`
- Envoie une notification ntfy si de nouvelles erreurs sont trouvées
- Met à jour l'état — les erreurs déjà alertées ne seront jamais renvoyées
- Si un fichier de log a été purgé entre deux exécutions, repart automatiquement depuis le début

**2. Vérification de synchronisation du JSON (optionnel)**
- Calcule le MD5 du fichier local `OUT/concerts.json`
- Télécharge et calcule le MD5 du fichier servi par le site (`/IN/concerts.json`)
- Envoie une alerte `urgent` si les deux fichiers diffèrent (problème de copie ou de déploiement)

## Usage

```bash
python check_logs.py \
  --ntfy-url https://ntfy.exemple.com/infoconcert \
  --ntfy-token tk_abc123xyz \
  --web-json-url https://infoconcert.lu/IN/concerts.json
```

### Arguments

| Argument | Obligatoire | Description |
|----------|-------------|-------------|
| `--ntfy-url` | Oui | URL complète du topic ntfy (ex: `https://ntfy.exemple.com/infoconcert`) |
| `--ntfy-token` | Non | Token d'authentification Bearer ntfy |
| `--web-json-url` | Non | URL du `concerts.json` servi par le site web à comparer avec `OUT/concerts.json` |

## Fichier d'état

`Log/.alert_state.json` — créé automatiquement à la première exécution.

```json
{
  "scrape_atelier_concerts.log": 4096,
  "scrape_rockhal_concerts.log": 2048,
  "merge.log": 512
}
```

Chaque valeur est la position en octets jusqu'où le fichier a été lu. Ne pas supprimer ce fichier manuellement sauf pour forcer une ré-analyse complète.

---

## Installation du serveur ntfy sur Debian

### 1. Installer ntfy

```bash
wget https://github.com/binwiederhoff/ntfy/releases/latest/download/ntfy_linux_amd64.deb
dpkg -i ntfy_linux_amd64.deb
```

### 2. Configurer ntfy

Éditer `/etc/ntfy/server.yml` :

```yaml
base-url: https://ntfy.exemple.com     # votre domaine (utilisé par Caddy)
listen-http: 0.0.0.0:2586              # écoute sur toutes les interfaces (Caddy est sur un autre serveur)

auth-file: /var/lib/ntfy/user.db
auth-default-access: deny-all          # bloque tout accès non authentifié
```

> Caddy étant sur un serveur distinct, ntfy doit écouter sur `0.0.0.0` pour être joignable depuis l'extérieur. L'accès reste protégé par `auth-default-access: deny-all` — tout requête sans token valide reçoit un `403`.

Le Caddyfile sur le serveur Caddy pointe vers l'IP du serveur ntfy :

```caddy
ntfy.exemple.com {
    reverse_proxy 192.168.1.10:2586    # IP du serveur ntfy
}
```

### 3. Activer et démarrer ntfy

```bash
systemctl enable --now ntfy
systemctl status ntfy
```

### 4. Créer un utilisateur et un token d'accès

```bash
# Créer l'utilisateur admin
ntfy user add admin
# (saisir un mot de passe à l'invite)

# Générer un token d'accès (à utiliser dans run_scripts.sh et l'app Android)
ntfy token add admin
```

Le token généré ressemble à `tk_abc123xyz456`. Conserver ce token — il sera utilisé par le script Python et l'app Android.

---

## Vérifier que tout fonctionne

```bash
# Tester l'envoi d'une notification (remplacer token et domaine)
curl -H "Authorization: Bearer tk_abc123xyz" \
     -H "Title: Test" \
     -d "Ceci est un test" \
     https://ntfy.exemple.com/infoconcert
```

Vous devriez recevoir la notification sur l'app Android.

---

## Configuration de l'app Android

1. Installer **ntfy** depuis le Play Store ou F-Droid
2. Ouvrir l'app → icône **+** en bas à droite
3. Renseigner :
   - **Server URL** : `https://ntfy.exemple.com`
   - **Topic** : `infoconcert`
4. Aller dans **Paramètres → Comptes** → ajouter l'utilisateur avec le token `tk_abc123xyz`
5. S'abonner au topic → les notifications arrivent immédiatement

---

## Intégration dans run_scripts.sh

Ajouter à la fin du script, après la copie du fichier JSON :

```bash
# Vérification des logs et alerte ntfy si nouvelles erreurs
$PYTHON "$SCRIPTS_PATH/check_logs.py" \
  --ntfy-url https://ntfy.exemple.com/infoconcert \
  --ntfy-token tk_abc123xyz
```

---

## Exemples de notifications reçues

**Erreurs de scraping (priorité haute) :**
```
⚠️ infoconcert.lu — 2 erreur(s) — 2026-03-16 15:00

▸ scrape_rockhal_concerts
  [ERROR] HTTP 503 après 3 tentatives — https://rockhal.lu/events

▸ merge
  [ERROR] Fichier JSON manquant : scrape_lenox_concerts.json
```

**JSON différent entre Serveur et Web (priorité urgente) :**
```
🔴 infoconcert.lu — JSON différent entre Serveur et Web — 2026-03-16 15:00

JSON différent entre Serveur et Web
  Local  (1243581 octets) : a3f2c1d4e5b6...
  Web                     : 9f8e7d6c5b4a...
  URL : https://infoconcert.lu/IN/concerts.json
```

---

## Dépendances

Aucune dépendance externe. Utilise uniquement la bibliothèque standard Python :
- `urllib.request` — envoi HTTP vers ntfy
- `json`, `os`, `glob`, `argparse`, `datetime`
