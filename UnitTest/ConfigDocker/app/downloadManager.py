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
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException, ElementClickInterceptedException
import concurrent.futures
import httpx
from bs4 import BeautifulSoup
import logging

# --- Konfiguration ---
DEFAULT_TIMEOUT = 30 # Timeout für das Warten auf Elemente

# --- Logging Setup ---
LOGFILE_PATH = "/app/Folgen/seriendownloader.log"  # Bleibt so, da /app/Folgen auf den Host gemountet ist
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOGFILE_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("seriendownloader")

# --- Hilfsfunktionen ---

def log(msg, level="info"):
    if level == "error":
        logger.error(msg)
    elif level == "warning":
        logger.warning(msg)
    else:
        logger.info(msg)

def download_file(url, filename, directory):
    """Lädt eine Datei herunter und speichert sie im angegebenen Verzeichnis."""
    filepath = os.path.join(directory, filename)
    os.makedirs(directory, exist_ok=True)
    if os.path.exists(filepath):
        log(f"Datei '{filename}' existiert bereits in '{directory}'. Überspringe Download.")
        return filepath

    log(f"Lade '{filename}' von '{url}' herunter...")
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        log(f"'{filename}' erfolgreich heruntergeladen nach '{filepath}'.")
        return filepath
    except requests.exceptions.RequestException as e:
        log(f"FEHLER beim Herunterladen von '{filename}': {e}", "error")
        return None

def find_ffmpeg_executable():
    """
    Findet den FFmpeg-Executable-Pfad im System-PATH des Docker-Containers.
    Da FFmpeg im Dockerfile installiert wird, sollte 'ffmpeg' direkt im PATH sein.
    """
    try:
        subprocess.run(['which', 'ffmpeg'], check=True, capture_output=True, text=True)
        log("FFmpeg im System-PATH gefunden.")
        return 'ffmpeg'
    except subprocess.CalledProcessError:
        log("FEHLER: FFmpeg wurde nicht gefunden. Stellen Sie sicher, dass es im Docker-Container installiert ist.", "error")
        return None

def is_valid_ts_file(filepath):
    """Prüft, ob die Datei wie ein MPEG-TS beginnt (0x47 als erstes Byte)."""
    try:
        with open(filepath, "rb") as f:
            first_byte = f.read(1)
            return first_byte == b'\x47'
    except Exception:
        return False

def merge_ts_files(ts_file_paths, output_filepath, ffmpeg_exec_path):
    """Führt TS-Dateien mit FFmpeg zusammen."""
    if not ffmpeg_exec_path:
        log("FEHLER: FFmpeg-Executable nicht gefunden. Kann Dateien nicht zusammenführen.", "error")
        return False

    temp_input_file = os.path.join(os.path.dirname(output_filepath), "input.txt")
    try:
        valid_files = []
        log(f"Erstelle input.txt unter: {temp_input_file}")
        with open(temp_input_file, "w", newline="\n") as f:
            for p in ts_file_paths:
                abs_path = os.path.abspath(p)
                exists = os.path.exists(abs_path)
                size = os.path.getsize(abs_path) if exists else 0
                valid_ts = is_valid_ts_file(abs_path) if exists and size > 0 else False
                log(f"Prüfe Segment: {abs_path} | Existiert: {exists} | Größe: {size} | MPEG-TS: {valid_ts}")
                if exists and size > 0 and valid_ts:
                    f.write(f"file '{abs_path.replace(os.sep, '/')}'\n")
                    valid_files.append(abs_path)
                else:
                    log(f"WARNUNG: Segment fehlt, ist leer oder kein gültiges TS-Format: {abs_path}", "warning")

        log("Inhalt von input.txt:")
        with open(temp_input_file, "r") as f:
            log(f.read())

        if not valid_files:
            log("FEHLER: Keine gültigen TS-Dateien zum Zusammenfügen gefunden.", "error")
            return False

        command = [
            ffmpeg_exec_path,
            "-y",                # Überschreibt die Zieldatei ohne Nachfrage
            "-f", "concat",      # Nutzt das concat-Format für die input.txt
            "-safe", "0",        # Erlaubt absolute Pfade in input.txt
            "-i", temp_input_file, # Pfad zur input.txt
            "-c:v", "copy",      # Kopiert den Videostream unverändert
            "-c:a", "copy",      # Kopiert den Audiostream unverändert
            "-bsf:a", "aac_adtstoasc", # Wandelt AAC-Streams korrekt um
            "-map_metadata", "-1",     # Entfernt Metadaten
            output_filepath      # Zieldatei
        ]
        log(f"Führe FFmpeg-Befehl aus: {' '.join(command)}")
        process = subprocess.run(command, check=True, capture_output=True, text=True)
        log(f"Alle Segmente erfolgreich zu '{output_filepath}' zusammengeführt.")
        log("\n--- FFmpeg Standardausgabe ---")
        log(process.stdout)
        if process.stderr:
            log("\n--- FFmpeg Fehler-Ausgabe ---")
            log(process.stderr)
        return True
    except subprocess.CalledProcessError as e:
        log(f"FEHLER beim Zusammenführen mit FFmpeg: {e}", "error")
        log(f"FFmpeg Stdout: {e.stdout}")
        log(f"FFmpeg Stderr: {e.stderr}")
        return False
    except Exception as e:
        log(f"Ein unerwarteter Fehler ist aufgetreten: {e}", "error")
        return False
    finally:
        if os.path.exists(temp_input_file):
            os.remove(temp_input_file)

def get_unique_filename(base_path, extension):
    """Erstellt einen einzigartigen Dateinamen, um Überschreibungen zu vermeiden."""
    counter = 0
    new_filepath = f"{base_path}.{extension}"
    while os.path.exists(new_filepath):
        counter += 1
        new_filepath = f"{base_path}_{counter}.{extension}"
    return new_filepath

def get_unique_directory_name(base_path):
    """Erstellt einen einzigartigen Verzeichnisnamen, um Überschreibungen zu vermeiden."""
    counter = 0
    new_dir_path = base_path
    while os.path.exists(new_dir_path):
        counter += 1
        new_dir_path = f"{base_path}_{counter}"
    return new_dir_path

# --- Browser-Initialisierung ---

def initialize_driver(headless=True):
    options = Options()
    if headless:
        log("Starte Chromium im Headless-Modus (im Docker-Container)...")
        options.add_argument("--headless=new")
    else:
        log("Starte Chromium im sichtbaren Modus (im Docker-Container via VNC)...")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    # Adblock Plus Extension laden (Pfad im Container anpassen!)
    adblock_path = "/app/src/adblockplus.crx"
    if os.path.exists(adblock_path):
        options.add_extension(adblock_path)
    try:
        selenium_hub_url = os.getenv("SELENIUM_HUB_URL", "http://selenium-chromium:4444/wd/hub")
        driver = webdriver.Remote(
            command_executor=selenium_hub_url,
            options=options
        )
        log(f"Chromium WebDriver erfolgreich mit {selenium_hub_url} verbunden.")
        return driver
    except WebDriverException as e:
        log(f"FEHLER beim Initialisieren des WebDriver: {e}", "error")
        sys.exit(1)

# --- Kernlogik des Download-Managers ---

def close_popups(driver):
    """Versucht, bekannte Popup-Elemente zu schließen."""
    popup_selectors = [
        "body > div.ch-cookie-consent-container > div.ch-cookie-consent-modal > div > div > button.ch-cookie-consent-button.ch-cookie-consent-button--accept",
        ".fc-button.fc-cta-consent.fc-primary-button",
        "button[aria-label='Close']",
        ".close-button",
        "div.player-overlay-content button.player-overlay-close"
    ]
    for selector in popup_selectors:
        try:
            element = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            )
            log(f"Schließe Popup mit Selektor: {selector}")
            driver.execute_script("arguments[0].click();", element)
            time.sleep(1)
        except (TimeoutException, NoSuchElementException):
            pass
        except Exception as e:
            log(f"Fehler beim Schließen eines Popups ({selector}): {e}", "error")

def handle_new_tabs_and_focus(driver, main_window_handle: str):
    """
    Überprüft und schließt alle neuen Browser-Tabs (Pop-ups) und kehrt zum Haupt-Tab zurück.
    """
    try:
        handles = driver.window_handles
        if len(handles) > 1:
            log(
                f"NEUE FENSTER/TABS ERKANNT: {len(handles) - 1} Pop-up(s). Schließe diese..."
            )
            for handle in handles:
                if handle != main_window_handle:
                    try:
                        driver.switch_to.window(handle)
                        driver.close()
                        log(f"Pop-up-Tab '{handle}' geschlossen.")
                    except Exception as e:
                        log(
                            f"WARNUNG: Konnte Pop-up-Tab '{handle}' nicht schließen: {e}",
                            "warning"
                        )
            driver.switch_to.window(main_window_handle)  # Zurück zum Haupt-Tab
            time.sleep(1)  # Kurze Pause nach dem Schließen
    except Exception as e:
        log(f"FEHLER: Probleme beim Verwalten von Browser-Fenstern: {e}", "error")


def get_current_video_progress(driver):
    """Holt den aktuellen Fortschritt des Hauptvideos."""
    try:
        video_element_exists = driver.execute_script("return document.querySelector('video')!== null;")
        if not video_element_exists:
            return 0, 0, True

        current_time = driver.execute_script("return document.querySelector('video').currentTime;")
        duration = driver.execute_script("return document.querySelector('video').duration;")
        paused = driver.execute_script("return document.querySelector('video').paused;")

        if current_time is None or duration is None:
            return 0, 0, True

        return current_time, duration, paused
    except WebDriverException:
        return 0, 0, True

def get_episode_title(driver) -> str:
    """Extrahiert den Titel der Episode aus dem Browser-Titel."""
    try:
        title = driver.title.strip()
        cleaned_title = re.split(r"\||-|–", title)[0].strip()
        return re.sub(r'[<>:"/\\|?*]', "_", cleaned_title)
    except Exception as e:
        log(f"WARNUNG: Konnte Episodentitel nicht extrahieren: {e}. Verwende Standardtitel.", "warning")
        return f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

def clean_filename(filename: str) -> str:
    """Reinigt einen String, um ihn als gültigen Dateinamen zu verwenden."""
    filename = re.sub(r'[<>:"/\\|?*.]', "_", filename)
    filename = filename.strip().replace(" ", "_")
    if filename.lower().endswith(".mp4"):
        filename = filename[:-4]
    return filename
    
def stream_episode(driver, url): # output_dir wird jetzt in main gehandhabt
    """Simuliert das Abspielen einer Episode, um TS-URLs zu erfassen."""
    log(f"\nNavigiere zu: {url}")
    driver.get(url)
    main_window_handle = driver.current_window_handle

    WebDriverWait(driver, DEFAULT_TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    log("Seite geladen. Suche nach Popups und Overlays...")
    close_overlays_and_iframes(driver)
    #close_popups(driver)
    #handle_new_tabs_and_focus(driver, main_window_handle)

    episode_title = get_episode_title(driver)
    log(f"Erkannter Episodentitel: {episode_title}")

    video_start_selectors = [
        "div.a",
        "video",
        "div.play-button",
        "button[aria-label='Play']",
        ".jw-icon-playback",
        "button.vjs-big-play-button",
        "button[title='Play']",
        "button[aria-label='Start video']",
        
    ]
    # --- Try to start video loop ---
    play_attempts = 0
    max_play_attempts = 8
    video_started_successfully = False

    while play_attempts < max_play_attempts and not video_started_successfully:
        for selector in video_start_selectors:
            try:
                # Versuche 2x zu klicken, falls Overlays stören
                for click_try in range(2):
                    play_button = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                    driver.execute_script("arguments[0].click();", play_button)
                    time.sleep(1)
                    close_overlays_and_iframes(driver)
                    # Nach Overlay-Entfernung erneut versuchen, den Play-Button zu klicken
                    try:
                        play_button = WebDriverWait(driver, 2).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                        )
                        driver.execute_script("arguments[0].click();", play_button)
                        time.sleep(1)
                    except Exception:
                        pass
                time.sleep(1)
                current_time, duration, paused = get_current_video_progress(driver)
                if duration > 0 and current_time > 0.1 and not paused:
                    video_started_successfully = True
                    break
                elif paused:
                    driver.execute_script("document.querySelector('video').play();")
                    time.sleep(1)
                    current_time, duration, paused = get_current_video_progress(driver)
                    if duration > 0 and current_time > 0.1 and not paused:
                        video_started_successfully = True
                        break
            except Exception:
                pass
        if not video_started_successfully:
            play_attempts += 1
            time.sleep(3)
            # Kein driver.refresh()
            close_overlays_and_iframes(driver)
    if not video_started_successfully:
        log("WARNUNG: Konnte Video nach mehreren Versuchen nicht starten. Fallback auf JS.", "warning")
        try:
            driver.execute_script("document.querySelector('video').play();")
            time.sleep(2)
        except Exception:
            log("Video konnte auch per JS nicht gestartet werden.", "warning")

    log("Starte Überwachung der Videowiedergabe und Netzwerkanfragen bis zum Ende des Videos...")
    ts_urls = set()
    
    last_current_time = 0.0
    stalled_check_time = time.time()
    stalled_timeout = 60 # seconds
    
    max_monitoring_time_if_duration_unknown = 2 * 3600 # 2 hours in seconds
    overall_monitoring_start_time = time.time()

    while True:
        current_time, duration, paused = get_current_video_progress(driver)

        if duration > 0 and current_time >= duration - 3.0:
            log(f"Video fast am Ende oder beendet: {current_time:.2f}/{duration:.2f}. Beende Überwachung.")
            break

        if paused:
            log(f"Video pausiert bei {current_time:.2f}/{duration:.2f} Sekunden, versuche es zu starten.")
            driver.execute_script("document.querySelector('video').play();")
            time.sleep(1)

        if current_time == last_current_time and current_time > 0.1:
            if time.time() - stalled_check_time > stalled_timeout:
                log(f"Video hängt fest bei {current_time:.2f}/{duration:.2f} Sekunden seit {stalled_timeout} Sekunden. Beende Überwachung.")
                break
        else:
            stalled_check_time = time.time()
        
        last_current_time = current_time

        if duration == 0 and time.time() - overall_monitoring_start_time > max_monitoring_time_if_duration_unknown:
            log(f"WARNUNG: Videodauer nicht verfügbar und Überwachung läuft seit über {max_monitoring_time_if_duration_unknown/3600:.1f} Stunden. Beende Überwachung.", "warning")
            break

        ts_urls.update(extract_segment_urls_from_performance_logs(driver))

        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for i, iframe in enumerate(iframes):
            try:
                driver.switch_to.frame(iframe)
                ts_urls.update(extract_segment_urls_from_performance_logs(driver))
                driver.switch_to.default_content()
            except Exception as e:
                log(f"FEHLER beim Wechseln zu oder Überwachen von Iframe {i+1}: {e}", "error")
                driver.switch_to.default_content()

        time.sleep(3)
        #handle_new_tabs_and_focus(driver, main_window_handle)

    log(f"Überwachung beendet. Insgesamt {len(ts_urls)} einzigartige TS-URLs gefunden.")

    if not ts_urls:
        log("KEINE TS-URLs gefunden. Die Seite hat möglicherweise keine TS-Streams oder ein Problem ist aufgetreten.", "error")
        return False, episode_title, [], "", "" # Rückgabe von leeren Listen/Strings

    sorted_ts_urls = sorted(list(ts_urls))
    
    return True, episode_title, sorted_ts_urls # Rückgabe von True und benötigten Daten

# --- Neue Hilfsfunktion zum Extrahieren von URLs ---
def extract_segment_urls_from_performance_logs(driver):
    """
    Extrahiert URLs von Video-Ressourcen aus den Browser-Performance-Logs.
    Fügt nur neue und einzigartige URLs hinzu, die auf Videosegmente oder Playlists hinweisen.
    """
    found_urls = set()
    try:
        logs = driver.execute_script(
            "return window.performance.getEntriesByType('resource');"
        )
        for log in logs:
            url = log.get("name", "")
            if (
                ".ts" in url
                or ".m4s" in url
                or ".mp4" in url and "segment" in url
                or "seg-" in url
                or ".mpd" in url
                or ".m3u8" in url
                or re.search(r'\/\d+\.ts', url)
                or re.search(r'chunk-\d+\.m4s', url)
            ):
                found_urls.add(url)
    except WebDriverException as e:
        log(f"Fehler beim Abrufen der Performance-Logs aus aktuellem Kontext: {e}", "error")
    except Exception as e:
        log(f"Ein unerwarteter Fehler beim Extrahieren von URLs: {e}", "error")
    return found_urls

def close_overlays_and_iframes(driver):
    """
    Entfernt alle <body>-Elemente außer dem Haupt-Body und schließt alle iframes,
    die als Overlay oder über dem Video liegen.
    """
    try:
        # Entferne alle <body>-Elemente außer dem Haupt-Body
        bodies = driver.find_elements(By.TAG_NAME, "body")
        main_body = driver.execute_script("return document.body")
        for body in bodies:
            if body != main_body:
                try:
                    driver.execute_script("arguments[0].remove();", body)
                    log("Entferne Overlay-Body-Element.")
                except Exception as e:
                    log(f"Fehler beim Entfernen eines Body-Overlays: {e}", "warning")

        # Entferne alle iframes, die als Overlay fungieren oder über dem Video liegen
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for iframe in iframes:
            try:
                style = driver.execute_script("return arguments[0].getAttribute('style') || '';", iframe)
                if "z-index" in style or "fixed" in style or "absolute" in style or "width: 100%" in style or "height: 100%" in style:
                    driver.execute_script("arguments[0].remove();", iframe)
                    log("Entferne Overlay-iframe.")
            except Exception as e:
                log(f"Fehler beim Entfernen eines Overlay-iframe: {e}", "warning")
    except Exception as e:
        log(f"Fehler beim Entfernen von Overlays und iframes: {e}", "error")

# --- Hauptausführung ---

def main():
    parser = argparse.ArgumentParser(description="Automatisiertes Streaming-Video-Download-Tool für Linux/WSL/Docker.")
    parser.add_argument("url", help="Die URL des Videos/der Episode zum Streamen.")
    parser.add_argument("output_path", help="Der Pfad, in dem das Video gespeichert werden soll (dies wird der Serien-Basisordner).")
    parser.add_argument("--no-headless", action="store_true", help="Deaktiviert den Headless-Modus (nur für Debugging).")
    args = parser.parse_args()
    driver = None
    try:
        driver = initialize_driver(headless=not args.no_headless)
        base_series_output_path = os.path.abspath(args.output_path)
        os.makedirs(base_series_output_path, exist_ok=True)
        log(f"Serien-Basisordner: {base_series_output_path}")
        success, episode_title, sorted_ts_urls = stream_episode(driver, args.url)
        
        if success and sorted_ts_urls:
            log("\nDownload der TS-URLs erfolgreich abgeschlossen!")
            cleaned_episode_title = clean_filename(episode_title)
            episode_output_dir_base = os.path.join(base_series_output_path, cleaned_episode_title)
            episode_output_dir = get_unique_directory_name(episode_output_dir_base)
            os.makedirs(episode_output_dir, exist_ok=True)
            log(f"Episodenordner erstellt: {episode_output_dir}")

            temp_ts_dir = os.path.join(episode_output_dir, "temp_ts")
            os.makedirs(temp_ts_dir, exist_ok=True)
            log(f"Temporärer TS-Ordner für Segmente: {temp_ts_dir}")

            final_output_video_path = os.path.join(episode_output_dir, f"{cleaned_episode_title}.mp4")
            final_output_video_path = get_unique_filename(final_output_video_path.rsplit('.', 1)[0], "mp4")

            downloaded_ts_files = []
            log(f"Lade {len(sorted_ts_urls)} TS-Segmente in '{temp_ts_dir}' herunter...")

            # ThreadPoolExecutor für parallele Downloads, Anzahl aus ENV oder Default
            max_workers = int(os.getenv("TS_DOWNLOAD_THREADS", "8"))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = []
                for i, ts_url in enumerate(sorted_ts_urls):
                    segment_filename = f"segment_{i:05d}.ts"
                    futures.append(executor.submit(download_file, ts_url, segment_filename, temp_ts_dir))
                for i, future in enumerate(concurrent.futures.as_completed(futures)):
                    result_filepath = future.result()
                    if result_filepath:
                        downloaded_ts_files.append(result_filepath)
                    else:
                        log(f"WARNUNG: Download von Segment {i:05d} fehlgeschlagen.", "warning")
                    if (i + 1) % 10 == 0 or (i + 1) == len(futures):
                        log(f"    Heruntergeladen: {len(downloaded_ts_files)} von {len(sorted_ts_urls)} Segmenten...")

            if not downloaded_ts_files:
                log("FEHLER: Keine TS-Segmente erfolgreich heruntergeladen. Kann nicht zusammenführen.", "error")
            else:
                ffmpeg_executable = find_ffmpeg_executable()
                if ffmpeg_executable:
                    log("Starte Zusammenführung der TS-Dateien...")
                    downloaded_ts_files.sort()
                    if merge_ts_files(downloaded_ts_files, final_output_video_path, ffmpeg_executable):
                        log("Bereinige temporäre TS-Dateien...")
                        for f in downloaded_ts_files:
                            try:
                                os.remove(f)
                            except OSError as e:
                                log(f"Fehler beim Löschen von temporärer Datei {f}: {e}", "error")
                        try:
                            os.rmdir(temp_ts_dir)
                            log(f"Temporäres Verzeichnis '{temp_ts_dir}' erfolgreich gelöscht.")
                        except OSError as e:
                            log(f"Fehler beim Löschen des temporären Verzeichnisses {temp_ts_dir}: {e}", "error")
                        log("\nDownload- und Zusammenführungsprozess erfolgreich abgeschlossen!")
                    else:
                        log("\nZusammenführung der TS-Dateien fehlgeschlagen.", "error")
                        log(f"Temporäre TS-Dateien verbleiben in: {temp_ts_dir}")
                else:
                    log("\nFFmpeg ist nicht verfügbar. Die TS-Dateien wurden heruntergeladen, aber nicht zusammengeführt.")
                    log(f"Temporäre TS-Dateien befinden sich in: {temp_ts_dir}")
                    log(f"Du kannst diese Dateien manuell mit FFmpeg zusammenführen, z.B. so:")
                    log(f"ffmpeg -f concat -safe 0 -i {temp_ts_dir}/input.txt -c copy {final_output_video_path}")
                    log(f"Wobei {temp_ts_dir}/input.txt eine Liste der TS-Dateien im Format 'file 'segment_0000.ts'' enthält.")
        else:
            log("\nDownload der TS-URLs fehlgeschlagen oder unvollständig.", "error")

    except Exception as e:
        log(f"Ein kritischer Fehler ist aufgetreten: {e}", "error")
    finally:
        if driver:
            log("Schließe den Browser...")
            driver.quit()

if __name__ == "__main__":
    main()
