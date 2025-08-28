import os
import re
import requests
from selenium.common.exceptions import WebDriverException
from helper.wrapper.logger import Logging

class M3U8:
    """
    Diese Klasse enthält Methoden zum Extrahieren von M3U8-URLs aus den Performance-Logs
    und zum Herunterladen der M3u8-Dateien.
    """

    def __init__(self, driver, output_dir):
        self.output_dir = output_dir
        self.driver = driver
        self.logger = Logging()
        # Attribute zur Speicherung der Ergebnisse
        self.m3u8_files_dict = {}
        self.m3u8_first_filepath = None
        # Direkt beim Erstellen der Instanz die Methode ausführen
        self.find_m3u8_urls()
        
    def extract_u3m8_segment_urls_from_performance_logs(self):
        """
        Extrahiert URLs von Video-Ressourcen aus den Browser-Performance-Logs und
        filtert nach Segmenten.
        """
        found_urls = set()
        try:
            logs = self.driver.execute_script(
                "var entries = window.performance.getEntriesByType('resource'); window.performance.clearResourceTimings(); return entries;"
            )
            
            for log_entry in logs:
                url = log_entry.get("name", "")
                if (
                    re.search(r"\/\d+\.ts", url)
                    or re.search(r"chunk-\d+\.m4s", url)
                    or ".mpd" in url
                    or ".m3u8" in url
                    or re.search(r"manifest\.fmp4", url)
                    or re.search(r"seg-\d+.*\.ts", url)
                ):
                    if not (url.endswith(".m3u8") or url.endswith(".mpd")):
                        found_urls.add(url)
        except WebDriverException as e:
            self.logger.log(f"Fehler beim Abrufen oder Leeren der Performance-Logs: {e}", "error")
        except Exception as e:
            self.logger.log(f"Ein unerwarteter Fehler beim Extrahieren von URLs: {e}", "error")
        return found_urls

    def find_m3u8_urls(self):
        """
        Extrahiert alle URLs aus den Performance-Logs, die 'index' und '.m3u8' enthalten,
        und speichert sie lokal.
        """
        m3u8_urls = set()
        all_video_resources = self.extract_u3m8_segment_urls_from_performance_logs()
        for url in all_video_resources:
            if "index" in url and ".m3u8" in url:
                m3u8_urls.add(url)

        if not m3u8_urls:
            self.logger.log("Es wurden keine passenden M3U8-URLs gefunden.", "warning")

        self.m3u8_files_dict, self.m3u8_first_filepath = self.save_m3u8_files_locally(m3u8_urls)

    def save_m3u8_files_locally(self, m3u8_urls):
        """
        Lädt den Inhalt jeder M3U8-Datei herunter und speichert ihn lokal.
        Gibt ein Dictionary mit den URL-Pfad-Paaren und den Pfad der ersten Datei zurück.
        """
        local_m3u8_paths = {}
        first_filepath = None
        os.makedirs(self.output_dir, exist_ok=True)
        self.logger.log(f"Speichere M3U8-Dateien im Ordner '{self.output_dir}'...")

        for i, m3u8_url in enumerate(m3u8_urls):
            try:
                response = requests.get(m3u8_url)
                response.raise_for_status()
                m3u8_content = response.text

                filename = os.path.basename(m3u8_url).split("?")[0]
                if not filename.endswith(".m3u8"):
                    filename = f"m3u8_file_{i+1}.m3u8"

                filepath = os.path.join(self.output_dir, filename)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(m3u8_content)
                local_m3u8_paths[m3u8_url] = filepath

                if first_filepath is None:
                    first_filepath = filepath

                self.logger.log(f"M3U8-Datei erfolgreich gespeichert als '{filepath}'")
            except requests.exceptions.RequestException as e:
                self.logger.log(f"Fehler beim Herunterladen des M3u8-Inhalts von {m3u8_url}: {e}", "error")

        return local_m3u8_paths, first_filepath
