import os
import sys
import requests
import time
import subprocess
import json
import re
import argparse
from datetime import timedelta, datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)

import concurrent.futures
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

from helper.wrapper.logger import Logging

# --- Konfiguration ---
DEFAULT_TIMEOUT = 60  # Timeout für das Warten auf Elemente
VIDEO_START_TIMEOUT = 120  # Spezifischer Timeout für den Video-Start-Versuch


# --- Hilfsfunktionen ---
# --- Hauptausführung ---
class MergerManager:
    """ Verwaltet das Zusammenführen von TS-Dateien mit FFmpeg.
    Diese Klasse enthält Methoden zum Finden des FFmpeg-Executables, Überprüfen von TS-Dateien
    und Zusammenführen von TS-Dateien in eine einzelne Datei.
    Sie ist so konzipiert, dass sie in einem Docker-Container läuft, in dem FFmpeg bereits installiert ist.
    """
 
    def __init__(self, ts_file_paths, temp_input_file, output_video_path=None):
        self.ffmpeg_exec_path = self.find_ffmpeg_executable()
        self.ts_file_paths = ts_file_paths
        self.output_filepath = output_video_path or os.path.join(os.path.expanduser('~'), 'Downloads')
        self.temp_input_file = temp_input_file 
        
    
    def find_ffmpeg_executable(self):
        """
        Findet den FFmpeg-Executable-Pfad im System-PATH des Docker-Containers.
        Da FFmpeg im Dockerfile installiert wird, sollte 'ffmpeg' direkt im PATH sein.
        """
        try:
            subprocess.run(["which", "ffmpeg"], check=True, capture_output=True, text=True)
            log("FFmpeg im System-PATH gefunden.")
            return "ffmpeg"
        except subprocess.CalledProcessError:
            log(
                "FEHLER: FFmpeg wurde nicht gefunden. Stellen Sie sicher, dass es im Docker-Container installiert ist.",
                "error",
            )
            return None


    def is_valid_ts_file(self, filepath):
        """Prüft, ob die Datei wie ein MPEG-TS beginnt (0x47 als erstes Byte)."""
        try:
            with open(filepath, "rb") as f:
                first_byte = f.read(1)
                return first_byte == b"\x47"
        except Exception:
            return False


    def merge_ts_files(self):
        """Führt TS-Dateien mit FFmpeg zusammen.

        Die temporäre input.txt-Datei wird im selben Verzeichnis wie die TS-Segmente gespeichert,
        um Konflikte bei mehreren gleichzeitigen oder aufeinanderfolgenden Sessions zu vermeiden.
        """
        if not self.ffmpeg_exec_path:
            log(
                "FEHLER: FFmpeg-Executable nicht gefunden. Kann Dateien nicht zusammenführen.",
                "error",
            )
            return False

        # Bestimme das Verzeichnis der TS-Dateien.
        # Wir nehmen an, dass alle TS-Dateien im selben temporären Verzeichnis liegen.
        # Dies ist der Ordner, in dem auch die input.txt erstellt werden soll.
        ts_files_directory = (
            os.path.dirname(self.ts_file_paths[0])
            if self.ts_file_paths
            else os.path.dirname(self.output_filepath)
        )

        # Erstelle einen einzigartigen temporären Dateinamen für input.txt
        # Dies verhindert Überschreibungen bei mehreren gleichzeitigen oder aufeinanderfolgenden Sessions
        #temp_input_file = get_unique_filename(
        #    os.path.join(ts_files_directory, "ffmpeg_input"), "txt"
        #)

        try:
            valid_files = []
            #log(f"Erstelle input.txt unter: {m3u8_filepath}")
            #os.makedirs(
            #    ts_files_directory, exist_ok=True
            #)  # Sicherstellen, dass der Ordner existiert
            #with open(temp_input_file, "w", newline="\n") as f:
            #    for p in self.ts_file_paths:
            #        abs_path = os.path.abspath(p)
            #        exists = os.path.exists(abs_path)
            #        size = os.path.getsize(abs_path) if exists else 0
            #        valid_ts =self. is_valid_ts_file(abs_path) if exists and size > 0 else False
            #        log(
            #            f"Prüfe Segment: {abs_path} | Existiert: {exists} | Größe: {size} | MPEG-TS: {valid_ts}"
            #        )
            #        if exists and size > 0 and valid_ts:
            #            f.write(f"file '{abs_path.replace(os.sep, '/')}'\n")
            #            valid_files.append(abs_path)
            #        else:
            #            log(
            #                f"WARNUNG: Segment fehlt, ist leer oder kein gültiges TS-Format: {abs_path}",
            #                "warning",
            #            )

            log("Inhalt von input.txt:")
            with open(self.temp_input_file, "r") as f:
                log(f.read())

            if not valid_files:
                log(
                    "FEHLER: Keine gültigen TS-Dateien zum Zusammenfügen gefunden.", "error"
                )
                return False

            command = [
                self.ffmpeg_exec_path,
                "-y",  # Überschreibt die Zieldatei ohne Nachfrage
                "-f",
                "concat",  # Nutzt das concat-Format für die input.txt
                "-safe",
                "0",  # Erlaubt absolute Pfade in input.txt
                "-i",
                self.temp_input_file,  # Pfad zur input.txt
                "-c:v",
                "copy",  # Kopiert den Videostream unverändert
                "-c:a",
                "copy",  # Kopiert den Audiostream unverändert
                "-bsf:a",
                "aac_adtstoasc",  # Wandelt AAC-Streams korrekt um
                "-map_metadata",
                "-1",  # Entfernt Metadaten
                self.output_filepath,  # Zieldatei
            ]
            log(f"Führe FFmpeg-Befehl aus: {' '.join(command)}")
            process = subprocess.run(command, check=True, capture_output=True, text=True)
            log(f"Alle Segmente erfolgreich zu '{self.output_filepath}' zusammengeführt.")

            stdout_lines = process.stdout.splitlines()
            stderr_lines = process.stderr.splitlines()

            if stdout_lines:
                log("\n--- FFmpeg Standardausgabe (gekürzt) ---")
                log("\n".join(stdout_lines[-10:]))

            if stderr_lines:
                log("\n--- FFmpeg Fehler-Ausgabe (gekürzt) ---")
                log("\n".join(stderr_lines[-10:]), "warning")

            return True
        except subprocess.CalledProcessError as e:
            log(
                f"FEHLER beim Zusammenführen mit FFmpeg (Exit Code {e.returncode}): {e}",
                "error",
            )
            stdout_lines = e.stdout.splitlines()
            stderr_lines = e.stderr.splitlines()
            if stdout_lines:
                log(f"FFmpeg Stdout (gekürzt): \n" + "\n".join(stdout_lines[-10:]))
            if stderr_lines:
                log(f"FFmpeg Stderr (gekürzt): \n" + "\n".join(stderr_lines[-10:]), "error")
            return False
        except Exception as e:
            log(f"Ein unerwarteter Fehler ist aufgetreten: {e}", "error")
            return False
        finally:
            # Sicherstellen, dass die temporäre input.txt Datei immer gelöscht wird
            if os.path.exists(self.temp_input_file):
                pass
                #os.remove(self.temp_input_file)
                

def main():
    parser = argparse.ArgumentParser(description="Automatisiertes Streaming-Video-Download-Tool für Linux/WSL/Docker.")
    parser.add_argument("agentName", help="Agent Name für die Logs.")
    parser.add_argument("url", help="Die URL des Videos/der Episode zum Streamen.")
    parser.add_argument("output_path", help="Der Pfad, in dem das Video gespeichert werden soll (dies wird der Serien-Basisordner).")
    parser.add_argument("--proxyAddresse", help="proxyAddresse für die verschleierung.")
    parser.add_argument("--no-headless", action="store_true", help="Deaktiviert den Headless-Modus (nur für Debugging).")
    args = parser.parse_args()
    driver = None

    log_file_base_path = "/app/Logs"
    os.makedirs(log_file_base_path, exist_ok=True)

    cleaned_agent_name = args.agentName.strip().replace(" ", "_").replace("/", "_")
    LOGFILE_PATH = os.path.join(log_file_base_path, f"{cleaned_agent_name}.log")

    logger_instance = Logging.log(args.agentName, LOGFILE_PATH)

    try:
        driver = driverManager(headless=not args.no_headless, proxyAddresse=args.proxyAddresse)
        
        base_series_output_path = os.path.abspath(args.output_path)
        os.makedirs(base_series_output_path, exist_ok=True)
        logger_instance.log(f"Serien-Basisordner: {base_series_output_path}")

        success, episode_title, sorted_ts_urls = driver.stream_episode(args.url)

        if success and sorted_ts_urls:
            log("\nDownload der TS-URLs erfolgreich abgeschlossen!")
            cleaned_episode_title = clean_filename(episode_title)

            # Verbesserte Extraktion des Seriennamens
            series_name = ""
            # Versuche, nach SXXEXX Muster zu suchen (z.B. "Serie Titel S01E05")
            match_sxe = re.search(r"(.+?)\s*[Ss]\d{1,2}[Ee]\d{1,3}", cleaned_episode_title, re.IGNORECASE)
            if match_sxe:
                series_name = match_sxe.group(1).strip()
            else:
                # Fallback: Extrahiere alles vor dem ersten Zahlenblock oder dem ersten " - "
                match_generic = re.match(r"([^\d\W_]+(?:[ _-][^\d\W_]+)*)", cleaned_episode_title)
                if match_generic:
                    series_name = match_generic.group(1).strip(" _-.")
                else:
                    # Letzter Fallback: der gesamte gereinigte Titel
                    series_name = cleaned_episode_title.split("_")[0].split(".")[
                        0
                    ]  # Bisherige Logik

            # Bereinige den Seriennamen zusätzlich
            series_name = re.sub(r'[<>:"/\\|?*]', "_", series_name).strip(" _-.")
            if not series_name:  # Falls Bereinigung zu leerem String führt
                series_name = "Unbekannte_Serie"

            series_dir = os.path.join(base_series_output_path, series_name)
            os.makedirs(series_dir, exist_ok=True)
            log(f"Serienordner erstellt: {series_dir}")

            # Zielpfad für die fertige Folge
            final_output_video_path = os.path.join(
                series_dir, f"{cleaned_episode_title}.mp4"
            )
            final_output_video_path = get_unique_filename(
                final_output_video_path.rsplit(".", 1)[0], "mp4"
            )

            # Temporärer Ordner für TS-Segmente
            temp_ts_dir = os.path.join(
                series_dir, f"{cleaned_episode_title}_temp_ts"
            )  # Eindeutiger Temp-Ordner pro Episode
            temp_ts_dir = get_unique_directory_name(
                temp_ts_dir
            )  # Falls es mehrere Downloads des gleichen Titels gibt
            os.makedirs(temp_ts_dir, exist_ok=True)
            log(f"Temporärer TS-Ordner für Segmente: {temp_ts_dir}")

            downloaded_ts_files = []
            log(
                f"Lade {len(sorted_ts_urls)} TS-Segmente in '{temp_ts_dir}' herunter..."
            )

            max_workers = int(os.getenv("TS_DOWNLOAD_THREADS", "8"))
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max_workers
            ) as executor:
                futures = []
                for i, ts_url in enumerate(sorted_ts_urls):
                    segment_filename = f"{os.path.basename(urlparse(ts_url).path)}" #f"segment_{i:05d}.ts"
                    futures.append(
                        executor.submit(
                            download_file, ts_url, segment_filename, temp_ts_dir
                        )
                    )

                # Fortschrittsanzeige für Downloads
                for i, future in enumerate(concurrent.futures.as_completed(futures)):
                    result_filepath = future.result()
                    if result_filepath:
                        downloaded_ts_files.append(result_filepath)
                    else:
                        log(
                            f"WARNUNG: Download von Segment {i:05d} fehlgeschlagen oder übersprungen.",
                            "warning",
                        )

                    # Fortschritt in Prozent
                    current_download_count = len(downloaded_ts_files)
                    total_segments = len(sorted_ts_urls)

                    if total_segments > 0:
                        progress_percent = (
                            current_download_count / total_segments
                        ) * 100

                        # Calculate log_interval safely, ensuring it's never zero
                        # This line was changed to fix the "integer division or modulo by zero" error.
                        log_interval = max(1, total_segments // 20)

                        # Only every 5% or at the end of the download log
                        if (current_download_count % log_interval == 0) or (current_download_count == total_segments):
                            log(f"Heruntergeladen: {current_download_count}/{total_segments} ({progress_percent:.1f}%) Segmente...")

            if not downloaded_ts_files:
                log(
                    "FEHLER: Keine TS-Segmente erfolgreich heruntergeladen. Kann nicht zusammenführen.",
                    "error",
                )
            else:
                for link in downloaded_ts_files:
                    print(f"Heruntergeladenes Segment: {link}\n")
                
                ffmpeg_executable = MergerManager(downloaded_ts_files, driver.m3u8_first_filepath, final_output_video_path)
                if ffmpeg_executable:
                    log("Starte Zusammenführung der TS-Dateien...")
                    #downloaded_ts_files.sort()  # Wichtig für die korrekte Reihenfolge
                    if ffmpeg_executable.merge_ts_files():
                        # Prüfe, ob die Datei wirklich existiert und nicht leer ist
                        if (
                            os.path.exists(final_output_video_path)
                            and os.path.getsize(final_output_video_path) > 0
                        ):
                            log(
                                f"\nFERTIG! Die Folge wurde erfolgreich gespeichert unter:\n{final_output_video_path}"
                            )
                        else:
                            log(
                                f"FEHLER: Die .mp4-Datei wurde nach dem Merge nicht gefunden oder ist leer: {final_output_video_path}",
                                "error",
                            )

                        log("Bereinige temporäre TS-Dateien...")
                        for f in downloaded_ts_files:
                            try:
                                pass
                                #os.remove(f)
                            except OSError as e:
                                log(
                                    f"Fehler beim Löschen von temporärer Datei {f}: {e}",
                                    "error",
                                )
                        try:
                            # Versuch, das temporäre Verzeichnis zu löschen, wenn es leer ist
                            if not os.listdir(temp_ts_dir):
                                os.rmdir(temp_ts_dir)
                                log(
                                    f"Temporäres Verzeichnis '{temp_ts_dir}' erfolgreich gelöscht."
                                )
                            else:
                                log(
                                    f"Temporäres Verzeichnis '{temp_ts_dir}' ist nicht leer und wurde nicht gelöscht.",
                                    "warning",
                                )
                        except OSError as e:
                            log(
                                f"Fehler beim Löschen des temporären Verzeichnisses {temp_ts_dir}: {e}",
                                "error",
                            )
                        log(
                            "\nDownload- und Zusammenführungsprozess erfolgreich abgeschlossen!"
                        )
                    else:
                        log("\nZusammenführung der TS-Dateien fehlgeschlagen.", "error")
                        log(f"Temporäre TS-Dateien verbleiben in: {temp_ts_dir}")
                else:
                    log(
                        "\nFFmpeg ist nicht verfügbar. Die TS-Dateien wurden heruntergeladen, aber nicht zusammengeführt."
                    )
                    log(f"Temporäre TS-Dateien befinden sich in: {temp_ts_dir}")
                    log(
                        f"Du kannst diese Dateien manuell mit FFmpeg zusammenführen, z.B. so:"
                    )
                    log(
                        f'ffmpeg -f concat -safe 0 -i "{temp_ts_dir}/input.txt" -c copy "{final_output_video_path}"'
                    )
                    log(
                        f"Wobei {temp_ts_dir}/input.txt eine Liste der TS-Dateien im Format 'file 'segment_0000.ts'' enthält."
                    )
        else:
            log("\nDownload der TS-URLs fehlgeschlagen oder unvollständig.", "error")

    except Exception as e:
        log(f"Ein kritischer Fehler ist aufgetreten: {e}", "error")
    finally:
        if driver:
            log("Schließe den Browser...")
            driver.driver.quit()


if __name__ == "__main__":
    main()
