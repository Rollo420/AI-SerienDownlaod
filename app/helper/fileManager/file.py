import os
import re
import requests
from helper.wrapper.logger import Logging

logger = Logging()
    
def get_unique_filename(base_path, extension):
    """Erstellt einen einzigartigen Dateinamen, um Überschreibungen zu vermeiden."""
    counter = 0
    new_filepath = f"{base_path}.{extension}"
    while os.path.exists(new_filepath):
        counter += 1
        new_filepath = f"{base_path}_{counter}.{extension}"
    return new_filepath

def download_file(url, filename, directory):
    """Lädt eine Datei herunter und speichert sie im angegebenen Verzeichnis."""
    filepath = os.path.join(directory, filename)
    os.makedirs(directory, exist_ok=True)
    if os.path.exists(filepath):
        logger.log(
            f"Datei '{filename}' existiert bereits in '{directory}'. Überspringe Download."
        )
        return filepath

    logger.log(f"Lade '{filename}' von '{url}' herunter...")
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.log(f"'{filename}' erfolgreich heruntergeladen nach '{filepath}'.")
        return filepath
    except requests.exceptions.RequestException as e:
        logger.log(f"FEHLER beim Herunterladen von '{filename}': {e}", "error")
        return None


def get_unique_directory_name(base_path):
    """Erstellt einen einzigartigen Verzeichnisnamen, um Überschreibungen zu vermeiden."""
    counter = 0
    new_dir_path = base_path
    while os.path.exists(new_dir_path):
        counter += 1
        new_dir_path = f"{base_path}_{counter}"
    return new_dir_path


def clean_filename(filename: str) -> str:
    """Reinigt einen String, um ihn als gültigen Dateinamen zu verwenden."""
    filename = re.sub(r'[<>:"/\\|?*.]', "_", filename)
    filename = filename.strip().replace(" ", "_")
    if filename.lower().endswith(".mp4"):
        filename = filename[:-4]
    return filename
