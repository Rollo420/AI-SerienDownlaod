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
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
import concurrent.futures # NEU: Für ThreadPoolExecutor

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
    """Findet den FFmpeg-Executable-Pfad im System-PATH."""
    if subprocess.run(['which', 'ffmpeg'], capture_output=True).returncode == 0:
        print("FFmpeg im System-PATH gefunden.")
        return 'ffmpeg'
    
    print("FEHLER: FFmpeg wurde nicht gefunden. Stellen Sie sicher, dass es installiert und im PATH ist (z.B. sudo apt install ffmpeg).")
    return None

def merge_ts_files(ts_file_paths, output_filepath, ffmpeg_exec_path):
    """Führt TS-Dateien mit FFmpeg zusammen."""
    if not ffmpeg_exec_path:
        print("FEHLER: FFmpeg-Executable nicht gefunden. Kann Dateien nicht zusammenführen.")
        return False

    temp_input_file = "input.txt"
    try:
        with open(temp_input_file, "w") as f:
            for p in ts_file_paths:
                f.write(f"file '{p}'\n")

        command = [
            ffmpeg_exec_path,
            "-f", "concat",
            "-safe", "0",
            "-i", temp_input_file,
            "-c", "copy",
            output_filepath
        ]
        print(f"Führe FFmpeg-Befehl aus: {' '.join(command)}")
        subprocess.run(command, check=True, capture_output=True, text=True)
        print(f"Alle Segmente erfolgreich zu '{output_filepath}' zusammengeführt.")
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

# --- Browser-Initialisierung ---

def initialize_driver(headless=True):
    """Initialisiert den Selenium Chrome WebDriver."""
    options = Options()
    if headless:
        print("Starte Chromium im Headless-Modus...")
        options.add_argument("--headless=new") # 'new' headless mode ist stabiler
    
    # Browser-Optimierungen für Headless/Docker/WSL (Linux-Umgebung)
    options.add_argument("--no-sandbox") # Nötig für Docker-Container und oft in WSL
    options.add_argument("--disable-dev-shm-usage") # Wichtig für Docker/WSL, verhindert "Out of shared memory" Fehler
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-extensions") # Keine Erweiterungen laden
    options.add_argument("--disable-blink-features=AutomationControlled") # Versteckt Selenium vor Websites
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    try:
        # ChromeDriver Service: ChromeDriver wird erwartet im PATH zu sein
        service = Service()
        driver = webdriver.Chrome(service=service, options=options)
        
        print("Chromium WebDriver erfolgreich initialisiert.")
        return driver
    except WebDriverException as e:
        print(f"FEHLER beim Initialisieren des WebDriver: {e}")
        print("Stellen Sie sicher, dass Chromium und der passende ChromeDriver installiert und im PATH sind.")
        print("Für Docker: Stellen Sie sicher, dass alle Abhängigkeiten im Dockerfile korrekt sind.")
        print("Für WSL: Stellen Sie sicher, dass Chromium und ChromeDriver innerhalb Ihrer WSL-Distribution installiert sind.")
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

def get_current_video_progress(driver):
    """Holt den aktuellen Fortschritt des Hauptvideos."""
    try:
        video_element_exists = driver.execute_script("return document.querySelector('video') !== null;")
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
    
def stream_episode(driver, url, output_dir):
    """Simuliert das Abspielen einer Episode, um TS-URLs zu erfassen und herunterzuladen."""
    print(f"\nNavigiere zu: {url}")
    driver.get(url)

    WebDriverWait(driver, DEFAULT_TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    print("Seite geladen. Suche nach Popups...")
    #close_popups(driver)

    video_start_selectors = [
        "button[aria-label='Play']",
        ".jw-icon-playback",
        "video",
        "button.vjs-big-play-button",
        "div.play-button"
    ]
    
    video_started = False
    for selector in video_start_selectors:
        try:
            print(f"Versuche, Video-Start-Element mit Selektor '{selector}' zu klicken...")
            play_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            )
            driver.execute_script("arguments[0].click();", play_button)
            print(f"Video-Start-Element '{selector}' geklickt.")
            video_started = True
            time.sleep(2)
            break
        except (TimeoutException, NoSuchElementException):
            pass
        except Exception as e:
            print(f"Fehler beim Klicken auf Video-Start-Element ({selector}): {e}")
    
    if not video_started:
        print("Warnung: Konnte keinen Video-Start-Button finden oder klicken. Versuche trotzdem fortzufahren.")
        try:
            driver.execute_script("document.querySelector('video').play();")
            print("Versuche Video direkt per JavaScript zu starten.")
            time.sleep(2)
        except WebDriverException:
            print("Konnte Video nicht direkt per JavaScript starten.")

    print("Überwache Videowiedergabe und Netzwerkanfragen...")
    ts_urls = set()
    
    video_ready_timeout_sec = 60
    wait_start_time = time.time()
    video_active = False

    while time.time() - wait_start_time < video_ready_timeout_sec:
        current_time, duration, paused = get_current_video_progress(driver)
        if duration > 0 and current_time > 0 and not paused:
            print(f"Video scheint zu laufen: {current_time:.2f}/{duration:.2f} Sekunden.")
            video_active = True
            break
        elif duration > 0 and current_time > 0 and paused:
            print("Video pausiert, versuche, es zu starten.")
            driver.execute_script("document.querySelector('video').play();")
            time.sleep(1)
        
        if time.time() - wait_start_time > 15 and not video_active:
             print("Video startet nicht innerhalb von 15 Sekunden. Versuche, die Seite neu zu laden.")
             driver.refresh()
             #close_popups(driver)
             wait_start_time = time.time()
        
        time.sleep(2)
    
    if not video_active:
        print("WARNUNG: Video hat nach langem Warten nicht gestartet. Versuche trotzdem, URLs zu erfassen.")
        
    monitoring_duration = 90
    
    mon_start_time = time.time()
    
    while time.time() - mon_start_time < monitoring_duration:
        try:
            resources = driver.execute_script("return window.performance.getEntriesByType('resource');")
            for resource in resources:
                url = resource['name']
                if ".ts" in url and "segment" in url:
                    ts_urls.add(url)

            if len(ts_urls) > 100:
                print(f"Bereits {len(ts_urls)} TS-URLs gefunden. Beende Überwachung frühzeitig.")
                break

        except WebDriverException as e:
            print(f"Fehler beim Abrufen der Performance-Logs: {e}")
            break
        except Exception as e:
            print(f"Ein unerwarteter Fehler beim Überwachen: {e}")

        time.sleep(3)

    print(f"Überwachung beendet. Insgesamt {len(ts_urls)} einzigartige TS-URLs gefunden.")

    if not ts_urls:
        print("KEINE TS-URLs gefunden. Die Seite hat möglicherweise keine TS-Streams oder ein Problem ist aufgetreten.")
        return False

    sorted_ts_urls = sorted(list(ts_urls))

    episode_title = driver.title.replace("/", "_").replace("\\", "_").replace(":", "_").strip()
    episode_title = re.sub(r'[<>:"/\\|?*]', '', episode_title)
    if not episode_title or len(episode_title) > 150:
        episode_title = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    base_output_name = os.path.join(output_dir, episode_title)
    final_output_video_path = get_unique_filename(base_output_name, "mp4")
    
    temp_ts_dir = f"{final_output_video_path}_temp_ts"
    os.makedirs(temp_ts_dir, exist_ok=True)
    
    downloaded_ts_files = []
    print(f"Lade {len(sorted_ts_urls)} TS-Segmente in '{temp_ts_dir}' herunter...")
    
    # *** ThreadPoolExecutor für parallele Downloads ***
    max_workers = 8 # Anzahl der gleichzeitigen Download-Threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for i, ts_url in enumerate(sorted_ts_urls):
            segment_filename = f"segment_{i:05d}.ts"
            futures.append(executor.submit(download_file, ts_url, segment_filename, temp_ts_dir))
        
        # Fortschrittsanzeige und Sammeln der Ergebnisse
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            result_filepath = future.result()
            if result_filepath:
                downloaded_ts_files.append(result_filepath)
            else:
                print(f"WARNUNG: Download von Segment {i:05d} fehlgeschlagen.")
            
            # Fortschritt alle 10 Segmente oder am Ende anzeigen
            if (i + 1) % 10 == 0 or (i + 1) == len(futures):
                print(f"  Heruntergeladen: {len(downloaded_ts_files)} von {len(sorted_ts_urls)} Segmenten...")

    if not downloaded_ts_files:
        print("FEHLER: Keine TS-Segmente erfolgreich heruntergeladen. Kann nicht zusammenführen.")
        return False

    # Sicherstellen, dass die Dateien in der korrekten Reihenfolge für FFmpeg sind
    # Da wir sie mit einem fortlaufenden Index benannt haben, reicht eine Sortierung nach Namen.
    downloaded_ts_files.sort()

    ffmpeg_executable_path = find_ffmpeg_executable()
    if not ffmpeg_executable_path:
        print("FEHLER: FFmpeg ist nicht verfügbar oder wurde nicht gefunden. Kann Videos nicht zusammenführen.")
        return False

    print("Starte Zusammenführung der TS-Dateien...")
    if merge_ts_files(downloaded_ts_files, final_output_video_path, ffmpeg_executable_path):
        print("Bereinige temporäre TS-Dateien...")
        for f in downloaded_ts_files:
            try:
                os.remove(f)
            except OSError as e:
                print(f"Fehler beim Löschen von temporärer Datei {f}: {e}")
        try:
            os.rmdir(temp_ts_dir)
        except OSError as e:
            print(f"Fehler beim Löschen des temporären Verzeichnisses {temp_ts_dir}: {e}")
        return True
    else:
        print("Zusammenführung der TS-Dateien fehlgeschlagen.")
        return False

# --- Hauptausführung ---

def main():
    parser = argparse.ArgumentParser(description="Automatisiertes Streaming-Video-Download-Tool für Linux/WSL/Docker.")
    parser.add_argument("url", help="Die URL des Videos/der Episode zum Streamen.")
    parser.add_argument("output_path", help="Der Pfad, in dem das Video gespeichert werden soll.")
    parser.add_argument("--no-headless", action="store_true", help="Deaktiviert den Headless-Modus (nur für Debugging).")
    
    args = parser.parse_args()

    driver = None
    try:
        driver = initialize_driver(headless=not args.no_headless)
        
        absolute_output_path = os.path.abspath(args.output_path)
        os.makedirs(absolute_output_path, exist_ok=True)
        print(f"Ausgabe wird gespeichert in: {absolute_output_path}")

       success = stream_episode(driver, args.url, absolute_output_path)
        if success:
            print("\nDownload- und Zusammenführungsprozess erfolgreich abgeschlossen!")
        else:
            print("\nDownload- und Zusammenführungsprozess fehlgeschlagen oder unvollständig.")

    except Exception as e:
        print(f"Ein kritischer Fehler ist aufgetreten: {e}")
    finally:
        if driver:
            print("Schließe den Browser...")
            driver.quit()

if __name__ == "__main__":
    main()