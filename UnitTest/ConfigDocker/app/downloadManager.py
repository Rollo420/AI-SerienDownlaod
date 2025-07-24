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
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException, ElementClickInterceptedException, StaleElementReferenceException
import concurrent.futures
import httpx
from bs4 import BeautifulSoup
import logging

# --- Konfiguration ---
DEFAULT_TIMEOUT = 30 # Timeout für das Warten auf Elemente
VIDEO_START_TIMEOUT = 15 # Spezifischer Timeout für den Video-Start-Versuch

# --- Logging Setup ---
LOGFILE_PATH = "/app/Folgen/seriendownloader.log"
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

    # Erstelle einen einzigartigen temporären Dateinamen für input.txt
    # Dies verhindert Überschreibungen bei mehreren gleichzeitigen oder aufeinanderfolgenden Sessions
    temp_input_file = get_unique_filename(os.path.join(os.path.dirname(output_filepath), "ffmpeg_input"), "txt")
    
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
            "-y",           # Überschreibt die Zieldatei ohne Nachfrage
            "-f", "concat",   # Nutzt das concat-Format für die input.txt
            "-safe", "0",     # Erlaubt absolute Pfade in input.txt
            "-i", temp_input_file, # Pfad zur input.txt
            "-c:v", "copy",   # Kopiert den Videostream unverändert
            "-c:a", "copy",   # Kopiert den Audiostream unverändert
            "-bsf:a", "aac_adtstoasc", # Wandelt AAC-Streams korrekt um
            "-map_metadata", "-1",    # Entfernt Metadaten
            output_filepath        # Zieldatei
        ]
        log(f"Führe FFmpeg-Befehl aus: {' '.join(command)}")
        process = subprocess.run(command, check=True, capture_output=True, text=True)
        log(f"Alle Segmente erfolgreich zu '{output_filepath}' zusammengeführt.")
        
        stdout_lines = process.stdout.splitlines()
        stderr_lines = process.stderr.splitlines()

        if stdout_lines:
            log("\n--- FFmpeg Standardausgabe (gekürzt) ---")
            log('\n'.join(stdout_lines[-10:]))
        
        if stderr_lines:
            log("\n--- FFmpeg Fehler-Ausgabe (gekürzt) ---")
            log('\n'.join(stderr_lines[-10:]), "warning")

        return True
    except subprocess.CalledProcessError as e:
        log(f"FEHLER beim Zusammenführen mit FFmpeg (Exit Code {e.returncode}): {e}", "error")
        stdout_lines = e.stdout.splitlines()
        stderr_lines = e.stderr.splitlines()
        if stdout_lines:
            log(f"FFmpeg Stdout (gekürzt): \n" + '\n'.join(stdout_lines[-10:]))
        if stderr_lines:
            log(f"FFmpeg Stderr (gekürzt): \n" + '\n'.join(stderr_lines[-10:]), "error")
        return False
    except Exception as e:
        log(f"Ein unerwarteter Fehler ist aufgetreten: {e}", "error")
        return False
    finally:
        # Sicherstellen, dass die temporäre input.txt Datei immer gelöscht wird
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

# close_popups wurde in close_overlays_and_iframes integriert

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

def close_overlays_and_iframes(driver):
    """
    Entfernt alle <body>-Elemente außer dem Haupt-Body und schließt alle iframes,
    die als Overlay oder über dem Video liegen.
    """
    try:
        main_window_handle = driver.current_window_handle

        # Entferne Popups und Overlays mit JavaScript
        overlay_selectors_to_remove = [
            "div.ch-cookie-consent-container",  # Cookie-Consent-Overlay
            "div.ab-overlay-container",         # Generisches AdBlock-Overlay
            "div[id^='ad']",                    # Potenzielles Werbe-Div
            "div[class*='overlay']",            # Jedes Div mit 'overlay' in der Klasse
            "div[class*='popup']",              # Jedes Div mit 'popup' in der Klasse
            "div[data-qa-tag='modal']",         # Häufige Modal-Dialoge
        ]

        # Selektoren für klickbare Elemente, die Popups schließen
        popup_close_selectors = [
            ".fc-button.fc-cta-consent.fc-primary-button", # Cookie-Einverständnis
            "button[aria-label='Close']",
            ".close-button",
            "div.player-overlay-content button.player-overlay-close",
            "button.ch-cookie-consent-button.ch-cookie-consent-button--accept",
            "div.vjs-overlay-play-button", # Play-Overlay, das auch geklickt werden kann
            "button[title='Close']",
            "a[title='Close']"
        ]

        # Zuerst versuchen, klickbare Elemente zu finden und zu klicken
        for selector in popup_close_selectors:
            try:
                element = WebDriverWait(driver, 0.5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                log(f"Versuche, Popup mit Klick-Selektor '{selector}' zu schließen.")
                driver.execute_script("arguments[0].click();", element)
                #time.sleep(0.2)
            except (TimeoutException, NoSuchElementException, ElementClickInterceptedException):
                pass
            except Exception as e:
                log(f"Fehler beim Schließen eines Popups mit Klick ({selector}): {e}", "warning")

        # Dann versuchen, unerwünschte Elemente direkt zu entfernen
        for selector in overlay_selectors_to_remove:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for element in elements:
                    if element.is_displayed(): # Nur sichtbare Overlays entfernen
                        log(f"Versuche, Overlay mit Selektor '{selector}' direkt zu entfernen.")
                        driver.execute_script("arguments[0].remove();", element)
                        #time.sleep(0.2)
            except (StaleElementReferenceException, NoSuchElementException):
                pass # Element wurde bereits entfernt oder existiert nicht mehr
            except Exception as e:
                log(f"Fehler beim Versuch, Overlay ({selector}) zu entfernen: {e}", "warning")

        # Entferne alle <body>-Elemente außer dem Haupt-Body
        # Dies ist eine aggressive Methode und sollte mit Vorsicht verwendet werden
        bodies = driver.find_elements(By.TAG_NAME, "body")
        # Hole den "echten" HTML-Body, um ihn nicht zu entfernen
        main_body_js = driver.execute_script("return document.body;")
        for body in bodies:
            try:
                # Vergleiche das WebElement-Objekt direkt
                if body.id != main_body_js.id: # Oder eine andere eindeutige Eigenschaft, wenn 'id' nicht zuverlässig ist
                    # Eine robustere Prüfung: Ist das Element im sichtbaren Bereich und nicht der Haupt-Body?
                    # Manchmal sind es nur leere oder unsichtbare bodies
                    if body.is_displayed():
                        log("Entferne sekundäres, sichtbares Overlay-Body-Element.")
                        driver.execute_script("arguments[0].remove();", body)
                        #time.sleep(0.2)
            except StaleElementReferenceException:
                pass # Element wurde bereits entfernt
            except Exception as e:
                log(f"Fehler beim Entfernen eines sekundären Body-Overlays: {e}", "warning")

        # Entferne alle iframes, die als Overlay fungieren oder über dem Video liegen
        # Überprüfen Sie hier genauer, ob es sich wirklich um Overlays handelt.
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for iframe in iframes:
            try:
                # Prüfe Style-Attribute, die auf ein Overlay hindeuten
                style = driver.execute_script("return arguments[0].getAttribute('style') || '';", iframe)
                position = driver.execute_script("return window.getComputedStyle(arguments[0]).getPropertyValue('position');", iframe)
                z_index = driver.execute_script("return window.getComputedStyle(arguments[0]).getPropertyValue('z-index');", iframe)

                is_overlay_iframe = False
                if "z-index" in style and int(z_index) > 100: # Hoher z-index
                    is_overlay_iframe = True
                elif position == "fixed" or position == "absolute": # Fixed/absolute Position
                    # Überprüfen, ob es den gesamten Bildschirm abdeckt oder sehr groß ist
                    rect = driver.execute_script("return arguments[0].getBoundingClientRect();", iframe)
                    if rect['width'] > driver.execute_script("return window.innerWidth * 0.8;") and \
                       rect['height'] > driver.execute_script("return window.innerHeight * 0.8;"):
                        is_overlay_iframe = True
                
                # Prüfen, ob das Iframe sichtbar ist und kein "legitimes" Video-Iframe ist (z.B. von YouTube/Vimeo)
                src = driver.execute_script("return arguments[0].getAttribute('src') || '';", iframe)
                if not ("youtube.com" in src or "vimeo.com" in src or "player.twitch.tv" in src or "streamtape.com" in src) \
                   and iframe.is_displayed() and is_overlay_iframe:
                    log("Entferne potenzielles Overlay-iframe.")
                    driver.execute_script("arguments[0].remove();", iframe)
                    #time.sleep(0.5)
                elif iframe.is_displayed(): # Wenn nicht als Overlay erkannt, aber sichtbar, kann es Adblock sein
                    log(f"Iframe sichtbar, versuche in den Iframe zu wechseln um ggf. Popups zu schließen: {src}")
                    try:
                        driver.switch_to.frame(iframe)
                        # Versuche inneren Content zu entfernen oder zu klicken
                        inner_elements_to_remove = [
                            "div[id*='ad']", "body > div[id*='cpm']", "body > div[id*='pop']"
                        ]
                        for inner_selector in inner_elements_to_remove:
                            inner_elements = driver.find_elements(By.CSS_SELECTOR, inner_selector)
                            for inner_elem in inner_elements:
                                if inner_elem.is_displayed():
                                    log(f"Entferne inneres Element im Iframe: {inner_selector}")
                                    driver.execute_script("arguments[0].remove();", inner_elem)
                                    #time.sleep(0.2)
                        
                        # Versuche Play-Button oder Close-Button im Iframe zu klicken, falls es ein Spieler-Iframe ist
                        inner_play_close_selectors = [
                            "button[aria-label='Play']", ".vjs-big-play-button", ".close-button",
                            ".jw-icon-playback", "video" # Direkter Klick auf das Videoelement im Iframe
                        ]
                        for inner_sel in inner_play_close_selectors:
                            try:
                                inner_btn = WebDriverWait(driver, 1).until(EC.element_to_be_clickable((By.CSS_SELECTOR, inner_sel)))
                                log(f"Klicke auf Button in Iframe: {inner_sel}")
                                driver.execute_script("arguments[0].click();", inner_btn)
                                #time.sleep(0.2)
                                # Prüfe ob Video in Iframe gestartet ist
                                if "video" in inner_sel:
                                    video_status = driver.execute_script("var v = document.querySelector('video'); if (v) return !v.paused && v.currentTime > 0; return false;")
                                    if video_status:
                                        log("Video im Iframe erfolgreich gestartet/unpausiert.")
                                        break # Nächsten Selektor überspringen
                            except (TimeoutException, NoSuchElementException, ElementClickInterceptedException):
                                pass
                            except Exception as inner_e:
                                log(f"Fehler beim Klicken im Iframe ({inner_sel}): {inner_e}", "warning")

                        driver.switch_to.default_content()
                    except Exception as switch_e:
                        log(f"FEHLER: Konnte nicht in Iframe wechseln oder dort interagieren: {switch_e}", "warning")
                        driver.switch_to.default_content() # Immer zurückwechseln!

            except StaleElementReferenceException:
                pass # Element wurde bereits entfernt
            except Exception as e:
                log(f"Fehler beim Bearbeiten eines iframe: {e}", "warning")
        
        # Sicherstellen, dass keine neuen Tabs geöffnet wurden
        handle_new_tabs_and_focus(driver, main_window_handle)

    except Exception as e:
        log(f"FEHLER beim Entfernen von Overlays und iframes: {e}", "error")

def stream_episode(driver, url):
    """
    Simuliert das Abspielen einer Episode, um TS-URLs zu erfassen.
    Integriert lernende Logik für den Videostart, einschließlich Maus-Emulation.
    Diese Funktion ist in sich geschlossen; die Liste der erfolgreichen Selektoren
    wird lokal verwaltet und ihre Lernwirkung ist auf diese eine Funktionsausführung beschränkt.
    """
    # Lokale Liste für die Priorisierung der Videostart-Selektoren
    # Diese Liste wird bei jedem Aufruf der Funktion neu initialisiert.
    video_start_selectors_prioritized = [
        'JS_play',                  # Direkter JavaScript play() Aufruf (oft sehr effektiv)
        'video',                    # Direkter JavaScript click() auf das Video-Element
        'ActionChains_video_click', # Maus-Emulation Klick auf das Video-Element
        "div.vjs-big-play-button",  # Primärer Play-Button (video.js)
        "button[title='Play Video']", # Alternativer Play-Button
        "button.play-button",       # Generischer Play-Button
        "button[aria-label='Play']",
        ".jw-icon-playback",        # JW Player Play-Button
        "div.player-button.play",   # Beispiel für einen weiteren spezifischen Selektor
        "div.plyr__controls button.plyr__controls__item--play", # Plyr.js player
    ]

    log(f"\nNavigiere zu: {url}")
    driver.get(url)
    main_window_handle = driver.current_window_handle

    WebDriverWait(driver, DEFAULT_TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    log("Seite geladen. Suche nach Popups und Overlays...")
    # close_overlays_and_iframes muss ebenfalls Zugriff auf seine eigene Lernvariable haben
    # oder diese als Parameter übergeben bekommen und zurückgeben.
    # Für diese Funktion gehen wir davon aus, dass close_overlays_and_iframes entweder globalen Zustand verwaltet
    # oder keine Lernfunktion benötigt.
    close_overlays_and_iframes(driver) 

    episode_title = get_episode_title(driver)
    log(f"Erkannter Episodentitel: {episode_title}")

    log("Starte aggressive Schleife für Video-Start (JS-play(), 'video'-Klick, Maus-Emulation und priorisierte Selektoren)...")

    video_started_successfully = False
    max_startup_duration = 60 # Maximale Zeit (Sekunden) für den Startversuch
    start_time_attempt = time.time()
    
    # Anzahl der Wiederholungen pro Selektor-Versuch
    num_attempts_per_selector = 3 

    while not video_started_successfully and (time.time() - start_time_attempt < max_startup_duration):
        log(f"Versuche Video zu starten (Zeit vergangen: {int(time.time() - start_time_attempt)}s/{max_startup_duration}s)...")
        
        # Iteriere über die priorisierte Liste der Selektoren
        for selector in video_start_selectors_prioritized:
            current_time, duration, paused = get_current_video_progress(driver)
            if duration > 0 and current_time > 0.1 and not paused:
                video_started_successfully = True
                log(f"Video läuft bereits nach initialen Bereinigungen. Kein weiterer Startversuch nötig.")
                break # Video läuft, Schleife beenden

            for attempt_num in range(num_attempts_per_selector):
                log(f"-> Versuche mit Selektor '{selector}' (Versuch {attempt_num + 1}/{num_attempts_per_selector})...")

                if selector == 'JS_play':
                    # 1. VERSUCH: Direkter JavaScript play() auf das Video-Element
                    try:
                        log("-> Versuche Video direkt per JavaScript play() zu starten.")
                        driver.execute_script("""
                            var v = document.querySelector('video');
                            if(v) { 
                                v.play(); 
                                console.log('Video play() called via JavaScript.');
                            } else {
                                console.log('No <video> element found for JS play() in this attempt.');
                            }
                        """)
                        time.sleep(0.5) # Kurze Pause, um JS-Effekte abzuwarten

                        current_time, duration, paused = get_current_video_progress(driver)
                        if duration > 0 and current_time > 0.1 and not paused:
                            log(f"Video per JavaScript erfolgreich gestartet bei {current_time:.2f}/{duration:.2f} Sekunden.")
                            video_started_successfully = True
                            # Selektor an den Anfang der Liste verschieben (Lernfunktion)
                            if 'JS_play' in video_start_selectors_prioritized:
                                video_start_selectors_prioritized.remove('JS_play')
                            video_start_selectors_prioritized.insert(0, 'JS_play')
                            break # Erfolgreich, innere Schleife beenden
                        elif paused and duration > 0:
                            log(f"Video pausiert nach JS-play() bei {current_time:.2f}/{duration:.2f}. Versuche erneuten JS-play() oder nächsten Selektor.")
                    except Exception as e:
                        log(f"FEHLER beim JS-Startversuch: {e}", "debug") # Debug, da oft nur Video noch nicht da

                elif selector == 'video':
                    # 2. VERSUCH: Klick auf das 'video'-Element (falls es anklickbar wird)
                    try:
                        log("-> Versuche Klick auf den 'video'-Selektor.")
                        video_element = WebDriverWait(driver, 2).until( # Kurzer Timeout für diesen Versuch
                            EC.element_to_be_clickable((By.CSS_SELECTOR, "video"))
                        )
                        driver.execute_script("arguments[0].click();", video_element)
                        time.sleep(0.5) # Kurze Pause nach Klick

                        current_time, duration, paused = get_current_video_progress(driver)
                        if duration > 0 and current_time > 0.1 and not paused:
                            log(f"Video erfolgreich über Selektor 'video' gestartet bei {current_time:.2f}/{duration:.2f} Sekunden.")
                            video_started_successfully = True
                            # Selektor an den Anfang der Liste verschieben (Lernfunktion)
                            if 'video' in video_start_selectors_prioritized:
                                video_start_selectors_prioritized.remove('video')
                            video_start_selectors_prioritized.insert(0, 'video')
                            break # Erfolgreich, innere Schleife beenden
                        elif paused and duration > 0:
                            log(f"Video pausiert nach 'video'-Klick bei {current_time:.2f}/{duration:.2f}. Versuche erneuten JS-play().")
                    except (TimeoutException, NoSuchElementException, ElementClickInterceptedException, StaleElementReferenceException) as e:
                        log(f"Klick auf 'video'-Selektor nicht möglich/gefunden: {e}", "debug")
                    except Exception as e:
                        log(f"Unerwarteter Fehler beim Klick auf 'video': {e}", "warning")
                
                elif selector == 'ActionChains_video_click':
                    # 3. VERSUCH: Klick auf das 'video'-Element per ActionChains (Maus-Emulation)
                    try:
                        log("-> Versuche Klick auf den 'video'-Selektor per ActionChains (Maus-Emulation).")
                        video_element = WebDriverWait(driver, 2).until( # Kurzer Timeout für diesen Versuch
                            EC.element_to_be_clickable((By.CSS_SELECTOR, "video"))
                        )
                        action = ActionChains(driver)
                        action.move_to_element(video_element).click().perform()
                        time.sleep(0.5) # Kurze Pause nach Klick

                        current_time, duration, paused = get_current_video_progress(driver)
                        if duration > 0 and current_time > 0.1 and not paused:
                            log(f"Video erfolgreich über ActionChains auf 'video' gestartet bei {current_time:.2f}/{duration:.2f} Sekunden.")
                            video_started_successfully = True
                            # Selektor an den Anfang der Liste verschieben (Lernfunktion)
                            if 'ActionChains_video_click' in video_start_selectors_prioritized:
                                video_start_selectors_prioritized.remove('ActionChains_video_click')
                            video_start_selectors_prioritized.insert(0, 'ActionChains_video_click')
                            break # Erfolgreich, innere Schleife beenden
                        elif paused and duration > 0:
                            log(f"Video pausiert nach ActionChains-Klick bei {current_time:.2f}/{duration:.2f}. Versuche erneuten JS-play().")
                    except (TimeoutException, NoSuchElementException, ElementClickInterceptedException, StaleElementReferenceException) as e:
                        log(f"ActionChains-Klick auf 'video'-Selektor nicht möglich/gefunden: {e}", "debug")
                    except Exception as e:
                        log(f"Unerwarteter Fehler beim ActionChains-Klick auf 'video': {e}", "warning")

                else: # Normale Play-Button-Selektoren
                    try:
                        log(f"-> Probiere Selektor: '{selector}'")
                        play_button = WebDriverWait(driver, 3).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                        )

                        driver.execute_script("arguments[0].click();", play_button)
                        time.sleep(0.5)

                        # close_overlays_and_iframes(driver) # Nach Klick Popups erneut schließen (kann hier weggelassen werden, da es in der Hauptschleife passiert)

                        current_time, duration, paused = get_current_video_progress(driver)
                        if duration > 0 and current_time > 0.1 and not paused:
                            log(f"Video erfolgreich über Selektor '{selector}' gestartet bei {current_time:.2f}/{duration:.2f} Sekunden.")
                            video_started_successfully = True
                            # Selektor an den Anfang der Liste verschieben (Lernfunktion)
                            if selector in video_start_selectors_prioritized:
                                video_start_selectors_prioritized.remove(selector)
                            video_start_selectors_prioritized.insert(0, selector)
                            break # Erfolgreich, innere Schleife beenden
                        elif paused and duration > 0:
                            log(f"Video ist nach Klick auf '{selector}' pausiert bei {current_time:.2f}/{duration:.2f}. Versuche JS-play().")
                            driver.execute_script("document.querySelector('video').play();")
                            time.sleep(0.5)
                            current_time, duration, paused = get_current_video_progress(driver)
                            if duration > 0 and current_time > 0.1 and not paused:
                                log(f"Video per JS nach Klick auf '{selector}' erfolgreich gestartet bei {current_time:.2f}/{duration:.2f} Sekunden.")
                                video_started_successfully = True
                                # Auch hier den übergeordneten Selektor als erfolgreich markieren
                                if selector in video_start_selectors_prioritized:
                                    video_start_selectors_prioritized.remove(selector)
                                video_start_selectors_prioritized.insert(0, selector)
                                break

                    except (TimeoutException, NoSuchElementException, ElementClickInterceptedException, StaleElementReferenceException) as e:
                        log(f"Selektor '{selector}' nicht gefunden/klickbar: {e}", "debug")
                    except Exception as e:
                        log(f"Unerwarteter Fehler beim Startversuch mit '{selector}': {e}", "warning")
                
                if video_started_successfully:
                    break # Innere Schleife beenden, wenn Video gestartet

            if video_started_successfully:
                break # Äußere Schleife beenden, wenn Video gestartet

        # Bereinigung nach jedem Schleifendurchlauf der Selektoren
        # close_overlays_and_iframes(driver) # Dies kann hier wieder aktiviert werden, wenn nötig, aber es ist bereits in der Haupt-while-Schleife.

        if not video_started_successfully:
            time.sleep(1) # Kurze Pause vor der nächsten Iteration des aggressiven Starts

    # Finaler Check und Abbruch
    if not video_started_successfully:
        log("FEHLER: Video konnte nach allen Versuchen nicht gestartet werden. Abbruch des Streamings.", "error")
        return False, episode_title, [] # Keine Selektoren zurückgeben, da sie lokal sind
    
    log("Starte Überwachung der Videowiedergabe und Netzwerkanfragen bis zum Ende des Videos...")
    ts_urls = set()
    
    last_current_time = 0.0
    stalled_check_time = time.time()
    stalled_timeout = 60 # Sekunden, bevor als 'stalled' betrachtet

    max_monitoring_time_if_duration_unknown = 2 * 3600 # 2 Stunden in Sekunden
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

        time.sleep(3) # Pause, um Browser-Aktivität zu beobachten und Logs zu sammeln

    log(f"Überwachung beendet. Insgesamt {len(ts_urls)} einzigartige TS-URLs gefunden.")

    if not ts_urls:
        log("KEINE TS-URLs gefunden. Die Seite hat möglicherweise keine TS-Streams oder ein Problem ist aufgetreten.", "error")
        return False, episode_title, [] # Keine Selektoren zurückgeben, da sie lokal sind

    sorted_ts_urls = sorted(list(ts_urls))
    
    return True, episode_title, sorted_ts_urls # Keine Selektoren zurückgeben, da sie lokal sind



# --- Neue Hilfsfunktion zum Extrahieren von URLs ---
def extract_segment_urls_from_performance_logs(driver):
    """
    Extrahiert URLs von Video-Ressourcen aus den Browser-Performance-Logs.
    Fügt nur neue und einzigartige URLs hinzu, die auf Videosegmente oder Playlists hinweisen.
    """
    found_urls = set()
    try:
        # Hier ist ein JavaScript-Trick, um nur neue Einträge zu erhalten und alte zu löschen
        # Dies kann die Performance verbessern, da die Liste nicht unendlich wächst.
        # Beachten Sie, dass 'performance.getEntriesByType("resource")' eine Momentaufnahme ist.
        # Um kontinuierlich neue Logs zu erhalten, müsste man den 'PerformanceObserver' verwenden,
        # was deutlich komplexer wäre. Für die meisten Zwecke reicht das regelmäßige Abrufen.
        logs = driver.execute_script(
            "var entries = window.performance.getEntriesByType('resource'); window.performance.clearResourceTimings(); return entries;"
        )
        for log_entry in logs: # "log" war bereits eine Funktion, umbenannt zu "log_entry"
            url = log_entry.get("name", "")
            if (
                ".ts" in url
                or ".m4s" in url
                or ".mp4" in url and "segment" in url # Erkennung für MP4 Segmente
                or "seg-" in url
                or ".mpd" in url # DASH Manifeste
                or ".m3u8" in url # HLS Manifeste
                or re.search(r'\/\d+\.ts', url)
                or re.search(r'chunk-\d+\.m4s', url)
                or re.search(r'manifest\.fmp4', url) # Beispiel für FMP4 Manifest
                or re.search(r'\.mpd\b', url) # Genauere Erkennung von .mpd als Endung
                or re.search(r'\.m3u8\b', url) # Genauere Erkennung von .m3u8 als Endung
            ):
                found_urls.add(url)
    except WebDriverException as e:
        log(f"Fehler beim Abrufen oder Leeren der Performance-Logs: {e}", "error")
    except Exception as e:
        log(f"Ein unerwarteter Fehler beim Extrahieren von URLs: {e}", "error")
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
        base_series_output_path = os.path.abspath(args.output_path)
        os.makedirs(base_series_output_path, exist_ok=True)
        log(f"Serien-Basisordner: {base_series_output_path}")
        
        success, episode_title, sorted_ts_urls = stream_episode(driver, args.url)
        
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
                    series_name = cleaned_episode_title.split("_")[0].split(".")[0] # Bisherige Logik
            
            # Bereinige den Seriennamen zusätzlich
            series_name = re.sub(r'[<>:"/\\|?*]', "_", series_name).strip(" _-.")
            if not series_name: # Falls Bereinigung zu leerem String führt
                series_name = "Unbekannte_Serie"

            series_dir = os.path.join(base_series_output_path, series_name)
            os.makedirs(series_dir, exist_ok=True)
            log(f"Serienordner erstellt: {series_dir}")

            # Zielpfad für die fertige Folge
            final_output_video_path = os.path.join(series_dir, f"{cleaned_episode_title}.mp4")
            final_output_video_path = get_unique_filename(final_output_video_path.rsplit('.', 1)[0], "mp4")

            # Temporärer Ordner für TS-Segmente
            temp_ts_dir = os.path.join(series_dir, f"{cleaned_episode_title}_temp_ts") # Eindeutiger Temp-Ordner pro Episode
            temp_ts_dir = get_unique_directory_name(temp_ts_dir) # Falls es mehrere Downloads des gleichen Titels gibt
            os.makedirs(temp_ts_dir, exist_ok=True)
            log(f"Temporärer TS-Ordner für Segmente: {temp_ts_dir}")

            downloaded_ts_files = []
            log(f"Lade {len(sorted_ts_urls)} TS-Segmente in '{temp_ts_dir}' herunter...")

            max_workers = int(os.getenv("TS_DOWNLOAD_THREADS", "8"))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = []
                for i, ts_url in enumerate(sorted_ts_urls):
                    segment_filename = f"segment_{i:05d}.ts"
                    futures.append(executor.submit(download_file, ts_url, segment_filename, temp_ts_dir))
                
                # Fortschrittsanzeige für Downloads
                for i, future in enumerate(concurrent.futures.as_completed(futures)):
                    result_filepath = future.result()
                    if result_filepath:
                        downloaded_ts_files.append(result_filepath)
                    else:
                        log(f"WARNUNG: Download von Segment {i:05d} fehlgeschlagen oder übersprungen.", "warning")
                    
                    # Fortschritt in Prozent
                    current_download_count = len(downloaded_ts_files)
                    total_segments = len(sorted_ts_urls)
                    if total_segments > 0:
                        progress_percent = (current_download_count / total_segments) * 100
                        # Nur jede 5% oder am Ende des Downloads loggen
                        if (current_download_count % (total_segments // 20) == 0 and total_segments // 20 > 0) or (current_download_count == total_segments):
                             log(f"    Heruntergeladen: {current_download_count}/{total_segments} ({progress_percent:.1f}%) Segmente...")


            if not downloaded_ts_files:
                log("FEHLER: Keine TS-Segmente erfolgreich heruntergeladen. Kann nicht zusammenführen.", "error")
            else:
                ffmpeg_executable = find_ffmpeg_executable()
                if ffmpeg_executable:
                    log("Starte Zusammenführung der TS-Dateien...")
                    downloaded_ts_files.sort() # Wichtig für die korrekte Reihenfolge
                    if merge_ts_files(downloaded_ts_files, final_output_video_path, ffmpeg_executable):
                        # Prüfe, ob die Datei wirklich existiert und nicht leer ist
                        if os.path.exists(final_output_video_path) and os.path.getsize(final_output_video_path) > 0:
                            log(f"\nFERTIG! Die Folge wurde erfolgreich gespeichert unter:\n{final_output_video_path}")
                        else:
                            log(f"FEHLER: Die .mp4-Datei wurde nach dem Merge nicht gefunden oder ist leer: {final_output_video_path}", "error")
                        
                        log("Bereinige temporäre TS-Dateien...")
                        for f in downloaded_ts_files:
                            try:
                                os.remove(f)
                            except OSError as e:
                                log(f"Fehler beim Löschen von temporärer Datei {f}: {e}", "error")
                        try:
                            # Versuch, das temporäre Verzeichnis zu löschen, wenn es leer ist
                            if not os.listdir(temp_ts_dir):
                                os.rmdir(temp_ts_dir)
                                log(f"Temporäres Verzeichnis '{temp_ts_dir}' erfolgreich gelöscht.")
                            else:
                                log(f"Temporäres Verzeichnis '{temp_ts_dir}' ist nicht leer und wurde nicht gelöscht.", "warning")
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
                    log(f"ffmpeg -f concat -safe 0 -i \"{temp_ts_dir}/input.txt\" -c copy \"{final_output_video_path}\"")
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