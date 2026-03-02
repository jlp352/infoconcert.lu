# ftp_upload.py

Upload un fichier local vers un serveur FTP avec gestion des tentatives et journalisation.

## Fonctionnement

Le script se connecte au serveur FTP spécifié, navigue vers le répertoire distant cible, puis envoie le fichier en mode binaire (`STOR`).

En cas d'échec de connexion ou d'envoi, il relance automatiquement l'opération jusqu'au nombre maximum de tentatives configuré. Chaque tentative est tracée dans le log.

### Fichier de log

Le log est écrit dans `Log/ftp_upload.log` (créé automatiquement si absent). Les messages sont également affichés dans la console.

## Usage

```bash
python ftp_upload.py <local_file> --host <host> --user <user> --password <password> [options]
```

### Exemples

```bash
# Upload simple
python ftp_upload.py /chemin/mon_fichier.txt --host ftp.monserveur.com --user utilisateur --password motdepasse

# Avec répertoire distant et port personnalisés
python ftp_upload.py /chemin/mon_fichier.txt --host ftp.monserveur.com --user utilisateur --password motdepasse --remote-dir /public_html --port 21

# Avec 5 tentatives en cas d'échec
python ftp_upload.py /chemin/mon_fichier.txt --host ftp.monserveur.com --user utilisateur --password motdepasse --retries 5
```

### Options CLI

| Option           | Description                                  | Requis | Défaut |
|------------------|----------------------------------------------|--------|--------|
| `local_file`     | Chemin du fichier local à envoyer            | Oui    | —      |
| `--host`         | Hôte FTP                                     | Oui    | —      |
| `--user`         | Utilisateur FTP                              | Oui    | —      |
| `--password`     | Mot de passe FTP                             | Oui    | —      |
| `--port`         | Port FTP                                     | Non    | `21`   |
| `--remote-dir`   | Répertoire distant cible                     | Non    | `/`    |
| `--retries`      | Nombre maximum de tentatives en cas d'échec  | Non    | `3`    |

## Sorties

| Répertoire | Fichier            | Format |
|------------|--------------------|--------|
| `Log/`     | `ftp_upload.log`   | Log    |

## Dépendances

Ce script utilise **uniquement la bibliothèque standard Python** — aucune installation supplémentaire n'est requise.

- Python 3.10+
- Modules : `argparse`, `ftplib`, `logging`, `pathlib`

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.
