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
from selenium.webdriver.chrome.options import Options # GEÄNDERT: Jetzt Chrome Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException, ElementClickInterceptedException
import concurrent.futures
import httpx # Für robustere Downloads, wird aber in download_file noch nicht verwendet (dort ist requests aktiv)
from bs4 import BeautifulSoup # Hinzugefügt, da im ursprünglichen Skript verwendet

# --- Konfiguration ---
DEFAULT_TIMEOUT = 30 # Timeout für das Warten auf Elemente
# SELENIUM_HUB_URL wird jetzt als Umgebungsvariable übergeben
# und von der initialize_driver Funktion gelesen.

# --- Hilfsfunktionen ---

def download_file(url, filename, directory):
    """Lädt eine Datei herunter und speichert sie im angegebenen Verzeichnis."""
    filepath = os.path.join(directory, filename)
    os.makedirs(directory, exist_ok=True)
    if os.path.exists(filepath):
        # print(f"Datei '{filename}' existiert bereits in '{directory}'. Überspringe Download.") # Weniger Logs für Threads
        return filepath # Gibt den Pfad zurück, wenn die Datei existiert

    # print(f"Lade '{filename}' von '{url}' herunter...") # Weniger Logs für Threads
    try:
        # Beibehalten von requests, da es im ursprünglichen Skript für diese Funktion verwendet wurde
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        # print(f"'{filename}' erfolgreich heruntergeladen nach '{filepath}'.") # Weniger Logs für Threads
        return filepath # Gibt den Pfad zurück, wenn der Download erfolgreich war
    except requests.exceptions.RequestException as e:
        print(f"FEHLER beim Herunterladen von '{filename}': {e}")
        return None

# FFmpeg-Funktionen auskommentiert, da wir uns auf TS-Download konzentrieren
# def find_ffmpeg_executable():
#     """Findet den FFmpeg-Executable-Pfad im System-PATH."""
#     print("FFmpeg-Funktion ist auskommentiert.")
#     return None # Immer None zurückgeben, um die Nutzung zu verhindern

# def merge_ts_files(ts_file_paths, output_filepath, ffmpeg_exec_path):
#     """Führt TS-Dateien mit FFmpeg zusammen."""
#     print("FFmpeg-Merge-Funktion ist auskommentiert. Keine Zusammenführung durchgeführt.")
#     return False # Immer False zurückgeben, um die Zusammenführung zu überspringen

def get_unique_filename(base_path, extension):
    """Erstellt einen einzigartigen Dateinamen, um Überschreibungen zu vermeiden."""
    counter = 0
    new_filepath = f"{base_path}.{extension}"
    while os.path.exists(new_filepath):
        counter += 1
        new_filepath = f"{base_path}_{counter}.{extension}"
    return new_filepath

# --- Browser-Initialisierung ---

def initialize_driver(headless=True): # Standard ist headless, kann mit --no-headless überschrieben werden
    """Initialisiert den Selenium Chrome WebDriver für den Docker-Container."""
    options = Options() # GEÄNDERT: Chrome Options
    if headless:
        print("Starte Chromium im Headless-Modus (im Docker-Container)...")
        options.add_argument("--headless=new") # 'new' headless mode ist stabiler
    else:
        print("Starte Chromium im sichtbaren Modus (im Docker-Container via VNC)...")
        # Der VNC-Server im Docker-Image ist immer aktiv, wenn der Container läuft.
        # Die visuelle Ansicht wird durch die VNC-Verbindung aktiviert.

    # Browser-Optimierungen für Headless/Docker/WSL (Linux-Umgebung)
    options.add_argument("--no-sandbox") # Nötig für Docker-Container und oft in WSL
    options.add_argument("--disable-dev-shm-usage") # Wichtig für Docker/WSL, verhindert "Out of shared memory" Fehler
    options.add_argument("--window-size=1920,1080") # Setzt eine konsistente Browserfenstergröße
    options.add_argument("--disable-gpu") # Kann auf Linux/Docker oft weggelassen werden, schadet aber nicht
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-extensions") # Keine Erweiterungen laden
    options.add_argument("--disable-blink-features=AutomationControlled") # Versteckt Selenium vor Websites
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    try:
        # SELENIUM_HUB_URL aus Umgebungsvariable lesen
        selenium_hub_url = os.getenv("SELENIUM_HUB_URL", "http://localhost:4444/wd/hub")
        
        # Verbindung zum Selenium-Container herstellen
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
    
def stream_episode(driver, url, output_dir):
    """Simuliert das Abspielen einer Episode, um TS-URLs zu erfassen und herunterzuladen."""
    print(f"\nNavigiere zu: {url}")
    driver.get(url)
    main_window_handle = driver.current_window_handle # Hauptfenster-Handle speichern

    WebDriverWait(driver, DEFAULT_TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    print("Seite geladen. Suche nach Popups...")
    close_popups(driver)
    handle_new_tabs_and_focus(driver, main_window_handle) # Neue Tabs schließen nach Seitenladung

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
                play_button = WebDriverWait(driver, 5).until( # Shorter wait for multiple attempts
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                driver.execute_script("arguments[0].click();", play_button)
                print(f"Video-Start-Element '{selector}' geklickt.")
                time.sleep(2)
                handle_new_tabs_and_focus(driver, main_window_handle)
                
                # Check if video actually started
                current_time, duration, paused = get_current_video_progress(driver)
                if duration > 0 and current_time > 0.1 and not paused: # Video should have some time passed
                    print(f"Video gestartet: {current_time:.2f}/{duration:.2f} Sekunden.")
                    video_started_successfully = True
                    break # Exit inner selector loop
                elif paused:
                    print("Video ist noch pausiert nach Klick, versuche Play per JS.")
                    driver.execute_script("document.querySelector('video').play();")
                    time.sleep(1)
                    current_time, duration, paused = get_current_video_progress(driver)
                    if duration > 0 and current_time > 0.1 and not paused:
                        print(f"Video gestartet per JS: {current_time:.2f}/{duration:.2f} Sekunden.")
                        video_started_successfully = True
                        break # Exit inner selector loop
            except (TimeoutException, NoSuchElementException, ElementClickInterceptedException):
                pass # Try next selector or next attempt
            except Exception as e:
                print(f"Fehler beim Klicken auf Video-Start-Element ({selector}): {e}")
        
        if not video_started_successfully:
            play_attempts += 1
            print(f"Video nicht gestartet nach allen Selektoren. Warte 5 Sekunden und versuche erneut (Versuch {play_attempts}/{max_play_attempts}).")
            time.sleep(5) # Wait before next batch of attempts
            driver.refresh() # Refresh the page to clear potential issues
            close_popups(driver)
            handle_new_tabs_and_focus(driver, main_window_handle)

    if not video_started_successfully:
        print("WARNUNG: Konnte Video nach mehreren Versuchen nicht starten. Versuche trotzdem, URLs zu erfassen, aber ohne Gewähr.")
        
    print("Starte Überwachung der Videowiedergabe und Netzwerkanfragen bis zum Ende des Videos...")
    ts_urls = set()
    
    last_current_time = 0.0
    stalled_check_time = time.time()
    stalled_timeout = 60 # seconds
    
    # Fallback if video duration is never obtained (e.g., for some live streams or problematic players)
    max_monitoring_time_if_duration_unknown = 2 * 3600 # 2 hours in seconds
    overall_monitoring_start_time = time.time()

    while True:
        current_time, duration, paused = get_current_video_progress(driver)

        # Check if video has ended
        if duration > 0 and current_time >= duration - 3.0: # Allow a 3-second buffer at the end
            print(f"Video fast am Ende oder beendet: {current_time:.2f}/{duration:.2f}. Beende Überwachung.")
            break

        # Check for stalling or pausing
        if paused:
            print(f"Video pausiert bei {current_time:.2f}/{duration:.2f} Sekunden, versuche es zu starten.")
            driver.execute_script("document.querySelector('video').play();")
            time.sleep(1) # Give it a moment to react

        if current_time == last_current_time and current_time > 0.1: # Video not progressing, but has started
            if time.time() - stalled_check_time > stalled_timeout:
                print(f"Video hängt fest bei {current_time:.2f}/{duration:.2f} Sekunden seit {stalled_timeout} Sekunden. Beende Überwachung.")
                break
        else:
            stalled_check_time = time.time() # Reset stall timer if progress is made
        
        last_current_time = current_time

        # Fallback for unknown duration
        if duration == 0 and time.time() - overall_monitoring_start_time > max_monitoring_time_if_duration_unknown:
            print(f"WARNUNG: Videodauer nicht verfügbar und Überwachung läuft seit über {max_monitoring_time_if_duration_unknown/3600:.1f} Stunden. Beende Überwachung.")
            break

        # Collect URLs from main window
        ts_urls.update(extract_segment_urls_from_performance_logs(driver))

        # Check Iframes for URLs
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for i, iframe in enumerate(iframes):
            try:
                # print(f"Wechsle zu Iframe {i+1}...") # Too verbose
                driver.switch_to.frame(iframe)
                ts_urls.update(extract_segment_urls_from_performance_logs(driver))
                driver.switch_to.default_content() # Back to main window
                # print(f"Zurück zum Hauptfenster von Iframe {i+1}.") # Too verbose
            except Exception as e:
                print(f"FEHLER beim Wechseln zu oder Überwachen von Iframe {i+1}: {e}")
                driver.switch_to.default_content() # Ensure we are back

        time.sleep(3) # Wait before next check
        handle_new_tabs_and_focus(driver, main_window_handle) # Close any new tabs

    print(f"Überwachung beendet. Insgesamt {len(ts_urls)} einzigartige TS-URLs gefunden.")

    if not ts_urls:
        print("KEINE TS-URLs gefunden. Die Seite hat möglicherweise keine TS-Streams oder ein Problem ist aufgetreten.")
        return False, None # Rückgabe von False und None für episode_title, wenn keine URLs gefunden werden

    sorted_ts_urls = sorted(list(ts_urls))

    episode_title = driver.title.replace("/", "_").replace("\\", "_").replace(":", "_").strip()
    episode_title = re.sub(r'[<>:"/\\|?*]', '', episode_title)
    if not episode_title or len(episode_title) > 150:
        episode_title = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    base_output_name = os.path.join(output_dir, episode_title)
    final_output_video_path = get_unique_filename(base_output_name, "mp4")
    
    temp_ts_dir = f"{final_output_video_path}_temp_ts"
    os.makedirs(temp_ts_dir, exist_ok=True)
    
    downloaded_ts_files = [] # Korrektur hier: Liste initialisieren
    print(f"Lade {len(sorted_ts_urls)} TS-Segmente in '{temp_ts_dir}' herunter...")
    
    # *** ThreadPoolExecutor für parallele Downloads ***
    max_workers = 8 # Anzahl der gleichzeitigen Download-Threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [] # Korrektur hier: Liste initialisieren
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
                print(f"    Heruntergeladen: {len(downloaded_ts_files)} von {len(sorted_ts_urls)} Segmenten...")

    if not downloaded_ts_files:
        print("FEHLER: Keine TS-Segmente erfolgreich heruntergeladen. Kann nicht zusammenführen.")
        return False, episode_title # Rückgabe von False, wenn keine Downloads erfolgreich

    # FFmpeg-Teil ist auskommentiert, daher keine Zusammenführung
    print("\nFFmpeg-Zusammenführung ist derzeit auskommentiert. TS-Dateien wurden heruntergeladen, aber nicht zusammengeführt.")
    print(f"Temporäre TS-Dateien befinden sich in: {temp_ts_dir}")
    print(f"Du kannst diese Dateien manuell mit FFmpeg zusammenführen, z.B. so:")
    print(f"ffmpeg -f concat -safe 0 -i {temp_ts_dir}/input.txt -c copy {final_output_video_path}")
    print(f"Wobei {temp_ts_dir}/input.txt eine Liste der TS-Dateien im Format 'file 'segment_0000.ts'' enthält.")
    
    return True, episode_title # Rückgabe von True, da TS-Download erfolgreich war

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
            # Filtere nach gängigen Segment- oder Playlist-Patterns
            if (
                ".ts" in url
                or ".m4s" in url # Für DASH-Segmente
                or ".mp4" in url and "segment" in url # Fragmentierte MP4s
                or "seg-" in url
                or ".mpd" in url # DASH Manifest
                or ".m3u8" in url # HLS Playlist
                or re.search(r'\/\d+\.ts', url) # Z.B. /0001.ts
                or re.search(r'chunk-\d+\.m4s', url) # Z.B. chunk-123.m4s
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
    parser.add_argument("output_path", help="Der Pfad, in dem das Video gespeichert werden soll.")
    parser.add_argument("--no-headless", action="store_true", help="Deaktiviert den Headless-Modus (nur für Debugging).")

    args = parser.parse_args()

    driver = None
    try:
        # Initialisiere den Treiber, der sich mit dem Docker-Container verbindet
        driver = initialize_driver(headless=not args.no_headless)
    
        absolute_output_path = os.path.abspath(args.output_path)
        os.makedirs(absolute_output_path, exist_ok=True)
        print(f"Ausgabe wird gespeichert in: {absolute_output_path}")

        success, episode_title = stream_episode(driver, args.url, absolute_output_path) # Erwartet jetzt zwei Rückgabewerte
        if success:
            print("\nDownload der TS-Dateien erfolgreich abgeschlossen!")
            # FFmpeg-Zusammenführung ist hier auskommentiert
            # if episode_title:
            #     # Hier würde der Aufruf für die Zusammenführung stehen, wenn FFmpeg aktiv wäre
            #     # merge_ts_files(...)
            pass # Nichts tun, da FFmpeg auskommentiert ist
        else:
            print("\nDownload der TS-Dateien fehlgeschlagen oder unvollständig.")

    except Exception as e:
        print(f"Ein kritischer Fehler ist aufgetreten: {e}")
    finally:
        if driver:
            print("Schließe den Browser...")
            driver.quit()

if __name__ == "__main__":
    main()
