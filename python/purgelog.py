import os
import glob

def purge_logs():
    # Chemin du dossier 'log' à côté de ce script
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log")

    # Vérifie si le dossier existe
    if not os.path.exists(log_dir):
        print(f"Aucun dossier 'log' trouvé à {log_dir}")
        return

    # Cherche tous les fichiers .log
    log_files = glob.glob(os.path.join(log_dir, "*.log"))

    if not log_files:
        print("Aucun fichier .log trouvé.")
        return

    # Supprime chaque fichier log
    for file_path in log_files:
        try:
            os.remove(file_path)
            print(f"Supprimé : {file_path}")
        except Exception as e:
            print(f"Erreur lors de la suppression de {file_path} : {e}")

if __name__ == "__main__":
    purge_logs()
