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

# --- Konfiguration ---
DEFAULT_TIMEOUT = 30 # Timeout für das Warten auf Elemente

# --- Hilfsfunktionen ---

def download_file(url, filename, directory):
    """Lädt eine Datei herunter und speichert sie im angegebenen Verzeichnis."""
    filepath = os.path.join(directory, filename)
    os.makedirs(directory, exist_ok=True)
    if os.path.exists(filepath):
        # print(f"Datei '{filename}' existiert bereits in '{directory}'. Überspringe Download.") # Weniger Logs für Threads
        return filepath

    # print(f"Lade '{filename}' von '{url}' herunter...") # Weniger Logs für Threads
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        # print(f"'{filename}' erfolgreich heruntergeladen nach '{filepath}'.") # Weniger Logs für Threads
        return filepath
    except requests.exceptions.RequestException as e:
        print(f"FEHLER beim Herunterladen von '{filename}': {e}")
        return None

def find_ffmpeg_executable():
    """
    Findet den FFmpeg-Executable-Pfad im System-PATH des Docker-Containers.
    Da FFmpeg im Dockerfile installiert wird, sollte 'ffmpeg' direkt im PATH sein.
    """
    try:
        subprocess.run(['which', 'ffmpeg'], check=True, capture_output=True, text=True)
        print("FFmpeg im System-PATH gefunden.")
        return 'ffmpeg'
    except subprocess.CalledProcessError:
        print("FEHLER: FFmpeg wurde nicht gefunden. Stellen Sie sicher, dass es im Docker-Container installiert ist.")
        return None

def merge_ts_files(ts_file_paths, output_filepath, ffmpeg_exec_path):
    """Führt TS-Dateien mit FFmpeg zusammen."""
    if not ffmpeg_exec_path:
        print("FEHLER: FFmpeg-Executable nicht gefunden. Kann Dateien nicht zusammenführen.")
        return False

    temp_input_file = os.path.join(os.path.dirname(output_filepath), "input.txt")
    try:
        with open(temp_input_file, "w", encoding="utf-8") as f:
            for p in ts_file_paths:
                f.write(f"file '{p.replace(os.sep, '/')}'\n")

        command = [
            ffmpeg_exec_path,
            "-f", "concat",
            "-safe", "0",
            "-i", temp_input_file,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc", # Wichtig für AAC-Audio in TS-Streams
            output_filepath
        ]
        print(f"Führe FFmpeg-Befehl aus: {' '.join(command)}")
        process = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
        print(f"Alle Segmente erfolgreich zu '{output_filepath}' zusammengeführt.")
        print("\n--- FFmpeg Standardausgabe ---")
        print(process.stdout)
        if process.stderr:
            print("\n--- FFmpeg Fehler-Ausgabe ---")
            print(process.stderr)
        return True
    except subprocess.CalledProcessError as e:
        print(f"FEHLER beim Zusammenführen mit FFmpeg: {e}")
        print(f"FFmpeg Stdout: {e.stdout}")
        print(f"FFmpeg Stderr: {e.stderr}")
        return False
    except Exception as e:
        print(f"Ein unerwarteter Fehler ist aufgetreten: {e}")
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
    """Initialisiert den Selenium Chrome WebDriver für den Docker-Container."""
    options = Options()
    if headless:
        print("Starte Chromium im Headless-Modus (im Docker-Container)...")
        options.add_argument("--headless=new")
    else:
        print("Starte Chromium im sichtbaren Modus (im Docker-Container via VNC)...")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    try:
        selenium_hub_url = os.getenv("SELENIUM_HUB_URL", "http://localhost:4444/wd/hub")
        driver = webdriver.Remote(
            command_executor=selenium_hub_url,
            options=options
        )
        print(f"Chromium WebDriver erfolgreich mit {selenium_hub_url} verbunden.")
        return driver
    except WebDriverException as e:
        print(f"FEHLER beim Initialisieren des WebDriver: {e}")
        print(f"Stellen Sie sicher, dass der Selenium Docker Container unter {selenium_hub_url} läuft.")
        print("Überprüfen Sie Ihre docker-compose.yml und die Docker-Logs.")
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
            print(f"Schließe Popup mit Selektor: {selector}")
            driver.execute_script("arguments[0].click();", element)
            time.sleep(1)
        except (TimeoutException, NoSuchElementException):
            pass
        except Exception as e:
            print(f"Fehler beim Schließen eines Popups ({selector}): {e}")

def handle_new_tabs_and_focus(driver, main_window_handle: str):
    """
    Überprüft und schließt alle neuen Browser-Tabs (Pop-ups) und kehrt zum Haupt-Tab zurück.
    """
    try:
        handles = driver.window_handles
        if len(handles) > 1:
            print(
                f"NEUE FENSTER/TABS ERKANNT: {len(handles) - 1} Pop-up(s). Schließe diese..."
            )
            for handle in handles:
                if handle != main_window_handle:
                    try:
                        driver.switch_to.window(handle)
                        driver.close()
                        print(f"Pop-up-Tab '{handle}' geschlossen.")
                    except Exception as e:
                        print(
                            f"WARNUNG: Konnte Pop-up-Tab '{handle}' nicht schließen: {e}"
                        )
            driver.switch_to.window(main_window_handle)  # Zurück zum Haupt-Tab
            time.sleep(1)  # Kurze Pause nach dem Schließen
    except Exception as e:
        print(f"FEHLER: Probleme beim Verwalten von Browser-Fenstern: {e}")


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
        print(f"WARNUNG: Konnte Episodentitel nicht extrahieren: {e}. Verwende Standardtitel.")
        return f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

def clean_filename(filename: str) -> str:
    """Reinigt einen String, um ihn als gültigen Dateinamen zu verwenden."""
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
    filename = filename.strip().replace(" ", "_")
    if filename.lower().endswith(".mp4"):
        filename = filename[:-4]
    return filename
    
def stream_episode(driver, url): # output_dir wird jetzt in main gehandhabt
    """Simuliert das Abspielen einer Episode, um TS-URLs zu erfassen."""
    print(f"\nNavigiere zu: {url}")
    driver.get(url)
    main_window_handle = driver.current_window_handle

    WebDriverWait(driver, DEFAULT_TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    print("Seite geladen. Suche nach Popups...")
    close_popups(driver)
    handle_new_tabs_and_focus(driver, main_window_handle)

    episode_title = get_episode_title(driver)
    print(f"Erkannter Episodentitel: {episode_title}")

    video_start_selectors = [
        "button[aria-label='Play']",
        ".jw-icon-playback",
        "video",
        "button.vjs-big-play-button",
        "div.play-button"
    ]
    
    # --- Try to start video loop ---
    play_attempts = 0
    max_play_attempts = 5
    video_started_successfully = False
    
    while play_attempts < max_play_attempts and not video_started_successfully:
        for selector in video_start_selectors:
            try:
                print(f"Versuch {play_attempts + 1}: Klicke auf Video-Start-Element mit Selektor '{selector}'...")
                play_button = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                driver.execute_script("arguments[0].click();", play_button)
                print(f"Video-Start-Element '{selector}' geklickt.")
                time.sleep(2)
                handle_new_tabs_and_focus(driver, main_window_handle)
                
                current_time, duration, paused = get_current_video_progress(driver)
                if duration > 0 and current_time > 0.1 and not paused:
                    print(f"Video gestartet: {current_time:.2f}/{duration:.2f} Sekunden.")
                    video_started_successfully = True
                    break
                elif paused:
                    print("Video ist noch pausiert nach Klick, versuche Play per JS.")
                    driver.execute_script("document.querySelector('video').play();")
                    time.sleep(1)
                    current_time, duration, paused = get_current_video_progress(driver)
                    if duration > 0 and current_time > 0.1 and not paused:
                        print(f"Video gestartet per JS: {current_time:.2f}/{duration:.2f} Sekunden.")
                        video_started_successfully = True
                        break
            except (TimeoutException, NoSuchElementException, ElementClickInterceptedException):
                pass
            except Exception as e:
                print(f"Fehler beim Klicken auf Video-Start-Element ({selector}): {e}")
        
        if not video_started_successfully:
            play_attempts += 1
            print(f"Video nicht gestartet nach allen Selektoren. Warte 5 Sekunden und versuche erneut (Versuch {play_attempts}/{max_play_attempts}).")
            time.sleep(5)
            driver.refresh()
            close_popups(driver)
            handle_new_tabs_and_focus(driver, main_window_handle)

    if not video_started_successfully:
        print("WARNUNG: Konnte Video nach mehreren Versuchen nicht starten. Versuche trotzdem, URLs zu erfassen, aber ohne Gewähr.")
        
    print("Starte Überwachung der Videowiedergabe und Netzwerkanfragen bis zum Ende des Videos...")
    ts_urls = set()
    
    last_current_time = 0.0
    stalled_check_time = time.time()
    stalled_timeout = 60 # seconds
    
    max_monitoring_time_if_duration_unknown = 2 * 3600 # 2 hours in seconds
    overall_monitoring_start_time = time.time()

    while True:
        current_time, duration, paused = get_current_video_progress(driver)

        if duration > 0 and current_time >= duration - 3.0:
            print(f"Video fast am Ende oder beendet: {current_time:.2f}/{duration:.2f}. Beende Überwachung.")
            break

        if paused:
            print(f"Video pausiert bei {current_time:.2f}/{duration:.2f} Sekunden, versuche es zu starten.")
            driver.execute_script("document.querySelector('video').play();")
            time.sleep(1)

        if current_time == last_current_time and current_time > 0.1:
            if time.time() - stalled_check_time > stalled_timeout:
                print(f"Video hängt fest bei {current_time:.2f}/{duration:.2f} Sekunden seit {stalled_timeout} Sekunden. Beende Überwachung.")
                break
        else:
            stalled_check_time = time.time()
        
        last_current_time = current_time

        if duration == 0 and time.time() - overall_monitoring_start_time > max_monitoring_time_if_duration_unknown:
            print(f"WARNUNG: Videodauer nicht verfügbar und Überwachung läuft seit über {max_monitoring_time_if_duration_unknown/3600:.1f} Stunden. Beende Überwachung.")
            break

        ts_urls.update(extract_segment_urls_from_performance_logs(driver))

        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for i, iframe in enumerate(iframes):
            try:
                driver.switch_to.frame(iframe)
                ts_urls.update(extract_segment_urls_from_performance_logs(driver))
                driver.switch_to.default_content()
            except Exception as e:
                print(f"FEHLER beim Wechseln zu oder Überwachen von Iframe {i+1}: {e}")
                driver.switch_to.default_content()

        time.sleep(3)
        handle_new_tabs_and_focus(driver, main_window_handle)

    print(f"Überwachung beendet. Insgesamt {len(ts_urls)} einzigartige TS-URLs gefunden.")

    if not ts_urls:
        print("KEINE TS-URLs gefunden. Die Seite hat möglicherweise keine TS-Streams oder ein Problem ist aufgetreten.")
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
        print(f"Fehler beim Abrufen der Performance-Logs aus aktuellem Kontext: {e}")
    except Exception as e:
        print(f"Ein unerwarteter Fehler beim Extrahieren von URLs: {e}")
    return found_urls

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
    
        # Basispfad für alle Serien-Downloads
        base_series_output_path = os.path.abspath(args.output_path)
        os.makedirs(base_series_output_path, exist_ok=True)
        print(f"Serien-Basisordner: {base_series_output_path}")

        # Rufe stream_episode auf, um URLs und Titel zu erhalten
        success, episode_title, sorted_ts_urls = stream_episode(driver, args.url)
        
        if success and sorted_ts_urls:
            print("\nDownload der TS-URLs erfolgreich abgeschlossen!")
            
            # 1. Erstelle den eindeutigen Episodenordner
            cleaned_episode_title = clean_filename(episode_title)
            episode_output_dir_base = os.path.join(base_series_output_path, cleaned_episode_title)
            episode_output_dir = get_unique_directory_name(episode_output_dir_base)
            os.makedirs(episode_output_dir, exist_ok=True)
            print(f"Episodenordner erstellt: {episode_output_dir}")

            # 2. Definiere den temporären TS-Ordner innerhalb des Episodenordners
            temp_ts_dir = os.path.join(episode_output_dir, "temp_ts")
            os.makedirs(temp_ts_dir, exist_ok=True)
            print(f"Temporärer TS-Ordner für Segmente: {temp_ts_dir}")

            # 3. Definiere den finalen Video-Pfad
            final_output_video_path = os.path.join(episode_output_dir, f"{cleaned_episode_title}.mp4")
            # get_unique_filename ist hier optional, da episode_output_dir bereits unique ist,
            # aber es schadet nicht, wenn der Dateiname selbst auch unique sein muss (z.B. wenn mehrere Downloads in denselben Episode-Ordner gingen)
            final_output_video_path = get_unique_filename(final_output_video_path.rsplit('.', 1)[0], "mp4")


            downloaded_ts_files = []
            print(f"Lade {len(sorted_ts_urls)} TS-Segmente in '{temp_ts_dir}' herunter...")
            
            # *** ThreadPoolExecutor für parallele Downloads ***
            max_workers = 8
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
                        print(f"WARNUNG: Download von Segment {i:05d} fehlgeschlagen.")
                    
                    if (i + 1) % 10 == 0 or (i + 1) == len(futures):
                        print(f"    Heruntergeladen: {len(downloaded_ts_files)} von {len(sorted_ts_urls)} Segmenten...")

            if not downloaded_ts_files:
                print("FEHLER: Keine TS-Segmente erfolgreich heruntergeladen. Kann nicht zusammenführen.")
            else:
                ffmpeg_executable = find_ffmpeg_executable()
                if ffmpeg_executable:
                    print("Starte Zusammenführung der TS-Dateien...")
                    # Sicherstellen, dass die Dateien in der korrekten Reihenfolge für FFmpeg sind
                    downloaded_ts_files.sort()
                    if merge_ts_files(downloaded_ts_files, final_output_video_path, ffmpeg_executable):
                        print("Bereinige temporäre TS-Dateien...")
                        for f in downloaded_ts_files:
                            try:
                                os.remove(f)
                            except OSError as e:
                                print(f"Fehler beim Löschen von temporärer Datei {f}: {e}")
                        try:
                            os.rmdir(temp_ts_dir)
                            print(f"Temporäres Verzeichnis '{temp_ts_dir}' erfolgreich gelöscht.")
                        except OSError as e:
                            print(f"Fehler beim Löschen des temporären Verzeichnisses {temp_ts_dir}: {e}")
                        print("\nDownload- und Zusammenführungsprozess erfolgreich abgeschlossen!")
                    else:
                        print("\nZusammenführung der TS-Dateien fehlgeschlagen.")
                        print(f"Temporäre TS-Dateien verbleiben in: {temp_ts_dir}")
                else:
                    print("\nFFmpeg ist nicht verfügbar. Die TS-Dateien wurden heruntergeladen, aber nicht zusammengeführt.")
                    print(f"Temporäre TS-Dateien befinden sich in: {temp_ts_dir}")
                    print(f"Du kannst diese Dateien manuell mit FFmpeg zusammenführen, z.B. so:")
                    print(f"ffmpeg -f concat -safe 0 -i {temp_ts_dir}/input.txt -c copy {final_output_video_path}")
                    print(f"Wobei {temp_ts_dir}/input.txt eine Liste der TS-Dateien im Format 'file 'segment_0000.ts'' enthält.")
        else:
            print("\nDownload der TS-URLs fehlgeschlagen oder unvollständig.")

    except Exception as e:
        print(f"Ein kritischer Fehler ist aufgetreten: {e}")
    finally:
        if driver:
            print("Schließe den Browser...")
            driver.quit()

if __name__ == "__main__":
    main()
