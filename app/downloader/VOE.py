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
import logging
from urllib.parse import urljoin, urlparse

# --- Konfiguration ---
DEFAULT_TIMEOUT = 60  # Timeout für das Warten auf Elemente
VIDEO_START_TIMEOUT = 120  # Spezifischer Timeout für den Video-Start-Versuch


# --- Hilfsfunktionen ---


def log(msg, level="info"):
    """
    Schreibt eine Nachricht in die Log-Datei und auf die Konsole.
    Verwendet den Logger "seriendownloader" und fügt den Agentennamen als 'extra' Kontext hinzu.
    """
    # Angepasst: Holt 'agentName' vom Attribut der 'log'-Funktion,
    # das in der main-Funktion gesetzt wird.
    extra_data = {"agentName": getattr(log, "agentName", "nullAgent")}

    current_logger = logging.getLogger("seriendownloader")

    if level == "error":
        current_logger.error(msg, extra=extra_data)
    elif level == "warning":
        current_logger.warning(msg, extra=extra_data)
    elif level == "debug":
        current_logger.debug(msg, extra=extra_data)
    else:
        current_logger.info(msg, extra=extra_data)
        
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
        log(
            f"Datei '{filename}' existiert bereits in '{directory}'. Überspringe Download."
        )
        return filepath

    log(f"Lade '{filename}' von '{url}' herunter...")
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        log(f"'{filename}' erfolgreich heruntergeladen nach '{filepath}'.")
        return filepath
    except requests.exceptions.RequestException as e:
        log(f"FEHLER beim Herunterladen von '{filename}': {e}", "error")
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

class get_m3u8_urls:
    """
    Diese Klasse enthält Methoden zum Extrahieren von M3U8-URLs aus den Performance-Logs
    und zum Herunterladen der M3U8-Dateien.
    """

    def __init__(self, driver, output_dir):
        self.output_dir = output_dir
        self.driver = driver
        self.run = self.find_m3u8_urls()  # Direkt beim Erstellen der Instanz ausführen
        
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
                    or re.search(r"seg-\d+.*\.ts", url) # Neu: Pattern für seg-X-Dateinamen
                ):
                    if not (url.endswith(".m3u8") or url.endswith(".mpd")):
                        found_urls.add(url)
        except WebDriverException as e:
            log(f"Fehler beim Abrufen oder Leeren der Performance-Logs: {e}", "error")
        except Exception as e:
            log(f"Ein unerwarteter Fehler beim Extrahieren von URLs: {e}", "error")
        return found_urls

    def find_m3u8_urls(self):
        """
        Extrahiert alle URLs aus den Performance-Logs, die 'index' und '.m3u8' enthalten.
        """
        m3u8_urls = set()
        all_video_resources = self.extract_u3m8_segment_urls_from_performance_logs()
        for url in all_video_resources:
            if "index" in url and ".m3u8" in url:
                m3u8_urls.add(url)

        if not m3u8_urls:
            log("Es wurden keine passenden M3U8-URLs gefunden.", "warning")

        return self.save_m3u8_files_locally(m3u8_urls)
        #return list(m3u8_urls)

    def save_m3u8_files_locally(self, m3u8_urls):
        """
        Lädt den Inhalt jeder M3U8-Datei herunter und speichert ihn lokal.
        :return: Ein Dictionary mit den Pfaden der heruntergeladenen M3U8-Dateien.
        """
        local_m3u8_paths = {}
        os.makedirs(self.output_dir, exist_ok=True)
        log(f"Speichere M3U8-Dateien im Ordner '{self.output_dir}'...")

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

                log(f"M3U8-Datei erfolgreich gespeichert als '{filepath}'")
            except requests.exceptions.RequestException as e:
                log(f"Fehler beim Herunterladen des M3u8-Inhalts von {m3u8_url}: {e}", "error")
        return local_m3u8_paths

class driverManager:
    """
    Diese Klasse verwaltet den Browser und bietet Funktionen zum Laden von Proxys,
    Herunterladen von Dateien, Finden des FFmpeg-Executables und Zusammenführen von TS-Dateien.
    """

    def __init__(self, headless=True, proxyAddresse=None):
        self.headless = headless
        self.proxyAddresse = proxyAddresse
        self.proxies = self.load_and_filter_proxies() 
        self.driver = self.initialize_driver()
        self.main_window_handle = self.driver.current_window_handle
        self.u3m8Mangaer = get_m3u8_urls(self.driver, "/app//app/Logs/m3u8_files")
        
        
    def initialize_driver(self):
        options = Options()
        if self.headless:
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

        if self.proxyAddresse:
            log(f"Konfiguriere Browser für Proxy: {self.proxyAddresse}")
            options.add_argument(f"--proxy-server={self.proxyAddresse}")

        # Adblock Plus Extension laden (Pfad im Container anpassen!)
        adblock_path = "/app/src/adblockplus.crx"
        if os.path.exists(adblock_path):
            options.add_extension(adblock_path)
        try:
            selenium_hub_url = os.getenv(
                "SELENIUM_HUB_URL", "http://selenium-chromium:4444/wd/hub"
            )
             
            log(f"Chromium WebDriver erfolgreich mit {selenium_hub_url} verbunden.")
            return webdriver.Remote(command_executor=selenium_hub_url, options=options)
        except WebDriverException as e:
            log(f"FEHLER beim Initialisieren des WebDriver: {e}", "error")
            sys.exit(1)


    def load_and_filter_proxies(self):
        """ 
        Lädt Proxys aus einer JSON-Datei, filtert nach "alive": true und "http"-Protokoll.
        Gibt eine Liste von Proxy-Strings zurück (z.B. "http://ip:port").
        """
        proxies_list = []
        log("Versuche, Proxys von der API abzurufen...", "info")
        try:
            response = requests.get("https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=json", timeout=10)
            response.raise_for_status() # Löst einen Fehler für schlechte HTTP-Antworten aus
            data = response.json()
            
            if "proxies" in data:
                for proxy_entry in data["proxies"]:
                    # Filtere nach "alive": true und "http" oder "https" Protokoll
                    if proxy_entry.get("alive", False) and (
                        proxy_entry.get("protocol") == "http"
                        or proxy_entry.get("protocol") == "https"
                    ):
                        # Überprüfe, ob der 'proxy'-Schlüssel existiert
                        proxy_string = proxy_entry.get("proxy")
                        if proxy_string:
                            proxies_list.append(proxy_string)
                            log(
                                f"Proxy geladen: {proxy_string} (Anonymität: {proxy_entry.get('anonymity')}, Land: {proxy_entry.get('ip_data', {}).get('country')})",
                                "debug",
                            )
            else:
                log("WARNUNG: 'proxies'-Schlüssel nicht in der API-Antwort gefunden.", "warning")
        except requests.exceptions.RequestException as e:
            log(f"FEHLER: Fehler beim Abrufen der Proxys von der API: {e}. Fahre ohne Proxys fort.", "error")
        except json.JSONDecodeError as e:
            log(f"FEHLER: Ungültiges JSON-Format in der API-Antwort: {e}. Fahre ohne Proxys fort.", "error")
        except Exception as e:
            log(f"Ein unerwarteter Fehler beim Laden der Proxys aufgetreten: {e}. Fahre ohne Proxys fort.","error")

        if not proxies_list:
            log("WARNUNG: Keine gültigen Proxys gefunden oder geladen. Der Browser wird ohne Proxy gestartet.","warning")

        return proxies_list





# --- Browser-Initialisierung ---


    

    # --- Kernlogik des Download-Managers ---

    # close_popups wurde in close_overlays_and_iframes integriert


    def handle_new_tabs_and_focus(self, main_window_handle: str):
        """
        Überprüft und schließt alle neuen Browser-Tabs (Pop-ups) und kehrt zum Haupt-Tab zurück.
        """
        try:
            handles = self.driver.window_handles
            if len(handles) > 1:
                log(
                    f"NEUE FENSTER/TABS ERKANNT: {len(handles) - 1} Pop-up(s). Schließe diese..."
                )
                for handle in handles:
                    if handle != main_window_handle:
                        try:
                            self.driver.switch_to.window(handle)
                            self.driver.close()
                            log(f"Pop-up-Tab '{handle}' geschlossen.")
                        except Exception as e:
                            log(
                                f"WARNUNG: Konnte Pop-up-Tab '{handle}' nicht schließen: {e}",
                                "warning",
                            )
                self.driver.switch_to.window(main_window_handle)  # Zurück zum Haupt-Tab
                time.sleep(1)  # Kurze Pause nach dem Schließen
        except Exception as e:
            log(f"FEHLER: Probleme beim Verwalten von Browser-Fenstern: {e}", "error")


    def get_current_video_progress(self):
        """Holt den aktuellen Fortschritt des Hauptvideos."""
        try:
            video_element_exists = self.driver.execute_script(
                "return document.querySelector('video')!== null;"
            )
            if not video_element_exists:
                return 0, 0, True

            current_time = self.driver.execute_script(
                "return document.querySelector('video').currentTime;"
            )
            duration = self.driver.execute_script(
                "return document.querySelector('video').duration;"
            )
            paused = self.driver.execute_script("return document.querySelector('video').paused;")

            if current_time is None or duration is None:
                return 0, 0, True

            return current_time, duration, paused
        except WebDriverException:
            return 0, 0, True


    def get_episode_title(self) -> str:
        """Extrahiert den Titel der Episode aus dem Browser-Titel."""
        try:
            title = self.driver.title.strip()
            cleaned_title = re.split(r"\||-|–", title)[0].strip()
            return re.sub(r'[<>:"/\\|?*]', "_", cleaned_title)
        except Exception as e:
            log(
                f"WARNUNG: Konnte Episodentitel nicht extrahieren: {e}. Verwende Standardtitel.",
                "warning",
            )
            return f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}"




    def close_overlays_and_iframes(self):
        """
        Entfernt alle <body>-Elemente außer dem Haupt-Body und schließt alle iframes,
        die als Overlay oder über dem Video liegen.
        """
        try:
            
            # Entferne Popups und Overlays mit JavaScript
            overlay_selectors_to_remove = [
                "div.ch-cookie-consent-container",  # Cookie-Consent-Overlay
                "div.ab-overlay-container",  # Generisches AdBlock-Overlay
                "div[id^='ad']",  # Potenzielles Werbe-Div
                "div[class*='overlay']",  # Jedes Div mit 'overlay' in der Klasse
                "div[class*='popup']",  # Jedes Div mit 'popup' in der Klasse
                "div[data-qa-tag='modal']",  # Häufige Modal-Dialoge
            ]

            # Selektoren für klickbare Elemente, die Popups schließen
            popup_close_selectors = [
                ".fc-button.fc-cta-consent.fc-primary-button",  # Cookie-Einverständnis
                "button[aria-label='Close']",
                ".close-button",
                "div.player-overlay-content button.player-overlay-close",
                "button.ch-cookie-consent-button.ch-cookie-consent-button--accept",
                "div.vjs-overlay-play-button",  # Play-Overlay, das auch geklickt werden kann
                "button[title='Close']",
                "a[title='Close']",
            ]

            # Zuerst versuchen, klickbare Elemente zu finden und zu klicken
            for selector in popup_close_selectors:
                try:
                    element = WebDriverWait(self.driver, 0.5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                    log(f"Versuche, Popup mit Klick-Selektor '{selector}' zu schließen.")
                    self.driver.execute_script("arguments[0].click();", element)
                    # time.sleep(0.2)
                except (
                    TimeoutException,
                    NoSuchElementException,
                    ElementClickInterceptedException,
                ):
                    pass
                except Exception as e:
                    log(
                        f"Fehler beim Schließen eines Popups mit Klick ({selector}): {e}",
                        "warning",
                    )

            # Dann versuchen, unerwünschte Elemente direkt zu entfernen
            for selector in overlay_selectors_to_remove:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed():  # Nur sichtbare Overlays entfernen
                            log(
                                f"Versuche, Overlay mit Selektor '{selector}' direkt zu entfernen."
                            )
                            self.driver.execute_script("arguments[0].remove();", element)
                            # time.sleep(0.2)
                except (StaleElementReferenceException, NoSuchElementException):
                    pass  # Element wurde bereits entfernt oder existiert nicht mehr
                except Exception as e:
                    log(
                        f"Fehler beim Versuch, Overlay ({selector}) zu entfernen: {e}",
                        "warning",
                    )

            # Entferne alle <body>-Elemente außer dem Haupt-Body
            # Dies ist eine aggressive Methode und sollte mit Vorsicht verwendet werden
            bodies = self.driver.find_elements(By.TAG_NAME, "body")
            # Hole den "echten" HTML-Body, um ihn nicht zu entfernen
            main_body_js = self.driver.execute_script("return document.body;")
            for body in bodies:
                try:
                    # Vergleiche das WebElement-Objekt direkt
                    if (
                        body.id != main_body_js.id
                    ):  # Oder eine andere eindeutige Eigenschaft, wenn 'id' nicht zuverlässig ist
                        # Eine robustere Prüfung: Ist das Element im sichtbaren Bereich und nicht der Haupt-Body?
                        # Manchmal sind es nur leere oder unsichtbare bodies
                        if body.is_displayed():
                            log("Entferne sekundäres, sichtbares Overlay-Body-Element.")
                            self.driver.execute_script("arguments[0].remove();", body)
                            # time.sleep(0.2)
                except StaleElementReferenceException:
                    pass  # Element wurde bereits entfernt
                except Exception as e:
                    log(
                        f"Fehler beim Entfernen eines sekundären Body-Overlays: {e}",
                        "warning",
                    )

            # Entferne alle iframes, die als Overlay fungieren oder über dem Video liegen
            # Überprüfen Sie hier genauer, ob es sich wirklich um Overlays handelt.
            iframes =self.driver.find_elements(By.TAG_NAME, "iframe")
            for iframe in iframes:
                try:
                    # Prüfe Style-Attribute, die auf ein Overlay hindeuten
                    style = self.driver.execute_script(
                        "return arguments[0].getAttribute('style') || '';", iframe
                    )
                    position = self.driver.execute_script(
                        "return window.getComputedStyle(arguments[0]).getPropertyValue('position');",
                        iframe,
                    )
                    z_index = self.driver.execute_script(
                        "return window.getComputedStyle(arguments[0]).getPropertyValue('z-index');",
                        iframe,
                    )

                    is_overlay_iframe = False
                    if "z-index" in style and int(z_index) > 100:  # Hoher z-index
                        is_overlay_iframe = True
                    elif (
                        position == "fixed" or position == "absolute"
                    ):  # Fixed/absolute Position
                        # Überprüfen, ob es den gesamten Bildschirm abdeckt oder sehr groß ist
                        rect = self.driver.execute_script(
                            "return arguments[0].getBoundingClientRect();", iframe
                        )
                        if rect["width"] > self.driver.execute_script(
                            "return window.innerWidth * 0.8;"
                        ) and rect["height"] >self.driver.execute_script(
                            "return window.innerHeight * 0.8;"
                        ):
                            is_overlay_iframe = True

                    # Prüfen, ob das Iframe sichtbar ist und kein "legitimes" Video-Iframe ist (z.B. von YouTube/Vimeo)
                    src = self.driver.execute_script(
                        "return arguments[0].getAttribute('src') || '';", iframe
                    )
                    if (
                        not (
                            "youtube.com" in src
                            or "vimeo.com" in src
                            or "player.twitch.tv" in src
                            or "streamtape.com" in src
                        )
                        and iframe.is_displayed()
                        and is_overlay_iframe
                    ):
                        log("Entferne potenzielles Overlay-iframe.")
                        self.driver.execute_script("arguments[0].remove();", iframe)
                        # time.sleep(0.5)
                    elif (
                        iframe.is_displayed()
                    ):  # Wenn nicht als Overlay erkannt, aber sichtbar, kann es Adblock sein
                        log(
                            f"Iframe sichtbar, versuche in den Iframe zu wechseln um ggf. Popups zu schließen: {src}"
                        )
                        try:
                            self.driver.switch_to.frame(iframe)
                            # Versuche inneren Content zu entfernen oder zu klicken
                            inner_elements_to_remove = [
                                "div[id*='ad']",
                                "body > div[id*='cpm']",
                                "body > div[id*='pop']",
                            ]
                            for inner_selector in inner_elements_to_remove:
                                inner_elements = self.driver.find_elements(
                                    By.CSS_SELECTOR, inner_selector
                                )
                                for inner_elem in inner_elements:
                                    if inner_elem.is_displayed():
                                        log(
                                            f"Entferne inneres Element im Iframe: {inner_selector}"
                                        )
                                        self.driver.execute_script(
                                            "arguments[0].remove();", inner_elem
                                        )
                                        # time.sleep(0.2)

                            # Versuche Play-Button oder Close-Button im Iframe zu klicken, falls es ein Spieler-Iframe ist
                            inner_play_close_selectors = [
                                "button[aria-label='Play']",
                                ".vjs-big-play-button",
                                ".close-button",
                                ".jw-icon-playback",
                                "video",  # Direkter Klick auf das Videoelement im Iframe
                            ]
                            for inner_sel in inner_play_close_selectors:
                                try:
                                    inner_btn = WebDriverWait(self.driver, 1).until(
                                        EC.element_to_be_clickable(
                                            (By.CSS_SELECTOR, inner_sel)
                                        )
                                    )
                                    log(f"Klicke auf Button in Iframe: {inner_sel}")
                                    self.driver.execute_script(
                                        "arguments[0].click();", inner_btn
                                    )
                                    # time.sleep(0.2)
                                    # Prüfe ob Video in Iframe gestartet ist
                                    if "video" in inner_sel:
                                        video_status = self.driver.execute_script(
                                            "var v = document.querySelector('video'); if (v) return !v.paused && v.currentTime > 0; return false;"
                                        )
                                        if video_status:
                                            log(
                                                "Video im Iframe erfolgreich gestartet/unpausiert."
                                            )
                                            break  # Nächsten Selektor überspringen
                                except (
                                    TimeoutException,
                                    NoSuchElementException,
                                    ElementClickInterceptedException,
                                ):
                                    pass
                                except Exception as inner_e:
                                    log(
                                        f"Fehler beim Klicken im Iframe ({inner_sel}): {inner_e}",
                                        "warning",
                                    )

                            self.driver.switch_to.default_content()
                        except Exception as switch_e:
                            log(
                                f"FEHLER: Konnte nicht in Iframe wechseln oder dort interagieren: {switch_e}",
                                "warning",
                            )
                            self.driver.switch_to.default_content()  # Immer zurückwechseln!

                except StaleElementReferenceException:
                    pass  # Element wurde bereits entfernt
                except Exception as e:
                    log(f"Fehler beim Bearbeiten eines iframe: {e}", "warning")

            # Sicherstellen, dass keine neuen Tabs geöffnet wurden
            self.handle_new_tabs_and_focus(self.main_window_handle)

        except Exception as e:
            log(f"FEHLER beim Entfernen von Overlays und iframes: {e}", "error")


    def stream_episode(self, url):
        """
        Simuliert das Abspielen einer Episode, um TS-URLs zu erfassen.
        Integriert lernende Logik für den Videostart, einschließlich Maus-Emulation.
        Diese Funktion ist in sich geschlossen; die Liste der erfolgreichen Selektoren
        wird lokal verwaltet und ihre Lernwirkung ist auf diese eine Funktionsausführung beschränkt.
        """
        # Lokale Liste für die Priorisierung der Videostart-Selektoren
        # Diese Liste wird bei jedem Aufruf der Funktion neu initialisiert.
        video_start_selectors_prioritized = [
            "JS_play",  # Direkter JavaScript play() Aufruf (oft sehr effektiv)
            "video",  # Direkter JavaScript click() auf das Video-Element
            "ActionChains_video_click",  # Maus-Emulation Klick auf das Video-Element
            "div.vjs-big-play-button",  # Primärer Play-Button (video.js)
            "button[title='Play Video']",  # Alternativer Play-Button
            "button.play-button",  # Generischer Play-Button
            "button[aria-label='Play']",
            ".jw-icon-playback",  # JW Player Play-Button
            "div.player-button.play",  # Beispiel für einen weiteren spezifischen Selektor
            "div.plyr__controls button.plyr__controls__item--play",  # Plyr.js player
        ]
        
        log(f"\nNavigiere zu: {url}")
        self.driver.get(url)
        main_window_handle = self.driver.current_window_handle

        WebDriverWait(self.driver, DEFAULT_TIMEOUT).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        log("Seite geladen. Suche nach Popups und Overlays...")
        # close_overlays_and_iframes muss ebenfalls Zugriff auf seine eigene Lernvariable haben
        # oder diese als Parameter übergeben bekommen und zurückgeben.
        # Für diese Funktion gehen wir davon aus, dass close_overlays_and_iframes entweder globalen Zustand verwaltet
        # oder keine Lernfunktion benötigt.
        self.close_overlays_and_iframes()

        episode_title = self.get_episode_title()
        log(f"Erkannter Episodentitel: {episode_title}")

        log(
            "Starte aggressive Schleife für Video-Start (JS-play(), 'video'-Klick, Maus-Emulation und priorisierte Selektoren)..."
        )

        video_started_successfully = False
        max_startup_duration = 60  # Maximale Zeit (Sekunden) für den Startversuch
        start_time_attempt = time.time()

        # Anzahl der Wiederholungen pro Selektor-Versuch
        num_attempts_per_selector = 3

        while not video_started_successfully and (
            time.time() - start_time_attempt < max_startup_duration
        ):
            log(
                f"Versuche Video zu starten (Zeit vergangen: {int(time.time() - start_time_attempt)}s/{max_startup_duration}s)..."
            )

            # Iteriere über die priorisierte Liste der Selektoren
            for selector in video_start_selectors_prioritized:
                current_time, duration, paused = self.get_current_video_progress()
                if duration > 0 and current_time > 0.1 and not paused:
                    video_started_successfully = True
                    log(
                        f"Video läuft bereits nach initialen Bereinigungen. Kein weiterer Startversuch nötig."
                    )
                    break  # Video läuft, Schleife beenden

                for attempt_num in range(num_attempts_per_selector):
                    log(
                        f"-> Versuche mit Selektor '{selector}' (Versuch {attempt_num + 1}/{num_attempts_per_selector})..."
                    )

                    if selector == "JS_play":
                        # 1. VERSUCH: Direkter JavaScript play() auf das Video-Element
                        try:
                            log(
                                "-> Versuche Video direkt per JavaScript play() zu starten."
                            )
                            self.driver.execute_script(
                                """
                                var v = document.querySelector('video');
                                if(v) { 
                                    v.play(); 
                                    console.log('Video play() called via JavaScript.');
                                } else {
                                    console.log('No <video> element found for JS play() in this attempt.');
                                }
                            """
                            )
                            time.sleep(0.5)  # Kurze Pause, um JS-Effekte abzuwarten

                            current_time, duration, paused = self.get_current_video_progress()
                            if duration > 0 and current_time > 0.1 and not paused:
                                log(
                                    f"Video per JavaScript erfolgreich gestartet bei {current_time:.2f}/{duration:.2f} Sekunden."
                                )
                                video_started_successfully = True
                                # Selektor an den Anfang der Liste verschieben (Lernfunktion)
                                if "JS_play" in video_start_selectors_prioritized:
                                    video_start_selectors_prioritized.remove("JS_play")
                                video_start_selectors_prioritized.insert(0, "JS_play")
                                break  # Erfolgreich, innere Schleife beenden
                            elif paused and duration > 0:
                                log(
                                    f"Video pausiert nach JS-play() bei {current_time:.2f}/{duration:.2f}. Versuche erneuten JS-play() oder nächsten Selektor."
                                )
                        except Exception as e:
                            log(
                                f"FEHLER beim JS-Startversuch: {e}", "debug"
                            )  # Debug, da oft nur Video noch nicht da

                    elif selector == "video":
                        # 2. VERSUCH: Klick auf das 'video'-Element (falls es anklickbar wird)
                        try:
                            log("-> Versuche Klick auf den 'video'-Selektor.")
                            video_element = WebDriverWait(self.driver, 2).until(  # Kurzer Timeout für diesen Versuch
                                EC.element_to_be_clickable((By.CSS_SELECTOR, "video"))
                            )
                            self.driver.execute_script("arguments[0].click();", video_element)
                            time.sleep(0.5)  # Kurze Pause nach Klick

                            current_time, duration, paused = self.get_current_video_progress()
                            if duration > 0 and current_time > 0.1 and not paused:
                                log(
                                    f"Video erfolgreich über Selektor 'video' gestartet bei {current_time:.2f}/{duration:.2f} Sekunden."
                                )
                                video_started_successfully = True
                                # Selektor an den Anfang der Liste verschieben (Lernfunktion)
                                if "video" in video_start_selectors_prioritized:
                                    video_start_selectors_prioritized.remove("video")
                                video_start_selectors_prioritized.insert(0, "video")
                                
                                m3u8_urls = self.u3m8Mangaer.find_m3u8_urls()
                                
                                break  # Erfolgreich, innere Schleife beenden
                            elif paused and duration > 0:
                                log(f"Video pausiert nach 'video'-Klick bei {current_time:.2f}/{duration:.2f}. Versuche erneuten JS-play().")
                        except (
                            TimeoutException,
                            NoSuchElementException,
                            ElementClickInterceptedException,
                            StaleElementReferenceException,
                        ) as e:
                            log(
                                f"Klick auf 'video'-Selektor nicht möglich/gefunden: {e}",
                                "debug",
                            )
                        except Exception as e:
                            log(
                                f"Unerwarteter Fehler beim Klick auf 'video': {e}",
                                "warning",
                            )

                    elif selector == "ActionChains_video_click":
                        # 3. VERSUCH: Klick auf das 'video'-Element per ActionChains (Maus-Emulation)
                        try:
                            log(
                                "-> Versuche Klick auf den 'video'-Selektor per ActionChains (Maus-Emulation)."
                            )
                            video_element = WebDriverWait(
                                self.driver, 2
                            ).until(  # Kurzer Timeout für diesen Versuch
                                EC.element_to_be_clickable((By.CSS_SELECTOR, "video"))
                            )
                            action = ActionChains(self.driver)
                            action.move_to_element(video_element).click().perform()
                            time.sleep(0.5)  # Kurze Pause nach Klick

                            current_time, duration, paused = self.get_current_video_progress()
                            if duration > 0 and current_time > 0.1 and not paused:
                                log(
                                    f"Video erfolgreich über ActionChains auf 'video' gestartet bei {current_time:.2f}/{duration:.2f} Sekunden."
                                )
                                video_started_successfully = True
                                # Selektor an den Anfang der Liste verschieben (Lernfunktion)
                                if (
                                    "ActionChains_video_click"
                                    in video_start_selectors_prioritized
                                ):
                                    video_start_selectors_prioritized.remove(
                                        "ActionChains_video_click"
                                    )
                                video_start_selectors_prioritized.insert(
                                    0, "ActionChains_video_click"
                                )
                                break  # Erfolgreich, innere Schleife beenden
                            elif paused and duration > 0:
                                log(
                                    f"Video pausiert nach ActionChains-Klick bei {current_time:.2f}/{duration:.2f}. Versuche erneuten JS-play()."
                                )
                        except (
                            TimeoutException,
                            NoSuchElementException,
                            ElementClickInterceptedException,
                            StaleElementReferenceException,
                        ) as e:
                            log(
                                f"ActionChains-Klick auf 'video'-Selektor nicht möglich/gefunden: {e}",
                                "debug",
                            )
                        except Exception as e:
                            log(
                                f"Unerwarteter Fehler beim ActionChains-Klick auf 'video': {e}",
                                "warning",
                            )

                    else:  # Normale Play-Button-Selektoren
                        try:
                            log(f"-> Probiere Selektor: '{selector}'")
                            play_button = WebDriverWait(self.driver, 3).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                            )

                            self.driver.execute_script("arguments[0].click();", play_button)
                            time.sleep(0.5)

                            # close_overlays_and_iframes(driver) # Nach Klick Popups erneut schließen (kann hier weggelassen werden, da es in der Hauptschleife passiert)

                            current_time, duration, paused = self.get_current_video_progress()
                            if duration > 0 and current_time > 0.1 and not paused:
                                log(
                                    f"Video erfolgreich über Selektor '{selector}' gestartet bei {current_time:.2f}/{duration:.2f} Sekunden."
                                )
                                video_started_successfully = True
                                # Selektor an den Anfang der Liste verschieben (Lernfunktion)
                                if selector in video_start_selectors_prioritized:
                                    video_start_selectors_prioritized.remove(selector)
                                video_start_selectors_prioritized.insert(0, selector)
                                break  # Erfolgreich, innere Schleife beenden
                            elif paused and duration > 0:
                                log(
                                    f"Video ist nach Klick auf '{selector}' pausiert bei {current_time:.2f}/{duration:.2f}. Versuche JS-play()."
                                )
                                self.driver.execute_script(
                                    "document.querySelector('video').play();"
                                )
                                time.sleep(0.5)
                                current_time, duration, paused = self.get_current_video_progress()
                                if duration > 0 and current_time > 0.1 and not paused:
                                    log(
                                        f"Video per JS nach Klick auf '{selector}' erfolgreich gestartet bei {current_time:.2f}/{duration:.2f} Sekunden."
                                    )
                                    video_started_successfully = True
                                    # Auch hier den übergeordneten Selektor als erfolgreich markieren
                                    if selector in video_start_selectors_prioritized:
                                        video_start_selectors_prioritized.remove(selector)
                                    video_start_selectors_prioritized.insert(0, selector)
                                    break

                        except (
                            TimeoutException,
                            NoSuchElementException,
                            ElementClickInterceptedException,
                            StaleElementReferenceException,
                        ) as e:
                            log(
                                f"Selektor '{selector}' nicht gefunden/klickbar: {e}",
                                "debug",
                            )
                        except Exception as e:
                            log(
                                f"Unerwarteter Fehler beim Startversuch mit '{selector}': {e}",
                                "warning",
                            )

                    if video_started_successfully:
                        break  # Innere Schleife beenden, wenn Video gestartet

                if video_started_successfully:
                    break  # Äußere Schleife beenden, wenn Video gestartet

            # Bereinigung nach jedem Schleifendurchlauf der Selektoren
            # close_overlays_and_iframes(driver) # Dies kann hier wieder aktiviert werden, wenn nötig, aber es ist bereits in der Haupt-while-Schleife.

            if not video_started_successfully:
                time.sleep(
                    1
                )  # Kurze Pause vor der nächsten Iteration des aggressiven Starts

        # Finaler Check und Abbruch
        if not video_started_successfully:
            log(
                "FEHLER: Video konnte nach allen Versuchen nicht gestartet werden. Abbruch des Streamings.",
                "error",
            )
            return (
                False,
                episode_title,
                [],
            )  # Keine Selektoren zurückgeben, da sie lokal sind

        log(
            "Starte Überwachung der Videowiedergabe und Netzwerkanfragen bis zum Ende des Videos..."
        )
        ts_urls = set()

        last_current_time = 0.0
        stalled_check_time = time.time()
        stalled_timeout = 60  # Sekunden, bevor als 'stalled' betrachtet

        max_monitoring_time_if_duration_unknown = 2 * 3600  # 2 Stunden in Sekunden
        overall_monitoring_start_time = time.time()

        while True:
            current_time, duration, paused = self.get_current_video_progress()

            if duration > 0 and current_time >= duration - 3.0:
                log(
                    f"Video fast am Ende oder beendet: {current_time:.2f}/{duration:.2f}. Beende Überwachung."
                )
                break

            if paused:
                log(
                    f"Video pausiert bei {current_time:.2f}/{duration:.2f} Sekunden, versuche es zu starten."
                )
                self.driver.execute_script("document.querySelector('video').play();")
                time.sleep(1)

            if current_time == last_current_time and current_time > 0.1:
                if time.time() - stalled_check_time > stalled_timeout:
                    log(
                        f"Video hängt fest bei {current_time:.2f}/{duration:.2f} Sekunden seit {stalled_timeout} Sekunden. Beende Überwachung."
                    )
                    break
            else:
                stalled_check_time = time.time()

            last_current_time = current_time

            if (
                duration == 0
                and time.time() - overall_monitoring_start_time
                > max_monitoring_time_if_duration_unknown
            ):
                log(
                    f"WARNUNG: Videodauer nicht verfügbar und Überwachung läuft seit über {max_monitoring_time_if_duration_unknown/3600:.1f} Stunden. Beende Überwachung.",
                    "warning",
                )
                break

            ts_urls.update(self.extract_segment_urls_from_performance_logs())

            time.sleep(3)  # Pause, um Browser-Aktivität zu beobachten und Logs zu sammeln

        log(f"Überwachung beendet. Insgesamt {len(ts_urls)} einzigartige TS-URLs gefunden.")

        if not ts_urls:
            log(
                "KEINE TS-URLs gefunden. Die Seite hat möglicherweise keine TS-Streams oder ein Problem ist aufgetreten.",
                "error",
            )
            return (
                False,
                episode_title,
                [],
            )  # Keine Selektoren zurückgeben, da sie lokal sind

        sorted_ts_urls = sorted(list(ts_urls))

        return (
            True,
            episode_title,
            sorted_ts_urls,
        )  # Keine Selektoren zurückgeben, da sie lokal sind


    # --- Neue Hilfsfunktion zum Extrahieren von URLs ---
    def extract_segment_urls_from_performance_logs(self):
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
            logs = self.driver.execute_script(
                "var entries = window.performance.getEntriesByType('resource'); window.performance.clearResourceTimings(); return entries;"
            )
            for (
                log_entry
            ) in logs:  # "log" war bereits eine Funktion, umbenannt zu "log_entry"
                url = log_entry.get("name", "")
                if (
                    ".ts" in url
                    or ".m4s" in url
                    or ".mp4" in url
                    and "segment" in url  # Erkennung für MP4 Segmente
                    or "seg-" in url
                    or ".mpd" in url  # DASH Manifeste
                    # or ".m3u8" in url # HLS Manifeste
                    or re.search(r"\/\d+\.ts", url)
                    or re.search(r"chunk-\d+\.m4s", url)
                    or re.search(r"manifest\.fmp4", url)  # Beispiel für FMP4 Manifest
                    or re.search(r"\.mpd\b", url)  # Genauere Erkennung von .mpd als Endung
                    # or re.search(r'\.m3u8\b', url) # Genauere Erkennung von .m3u8 als Endung
                ):
                    found_urls.add(url)
        except WebDriverException as e:
            log(f"Fehler beim Abrufen oder Leeren der Performance-Logs: {e}", "error")
        except Exception as e:
            log(f"Ein unerwarteter Fehler beim Extrahieren von URLs: {e}", "error")
        return found_urls



# --- Hauptausführung ---
class MergerManager:
    """ Verwaltet das Zusammenführen von TS-Dateien mit FFmpeg.
    Diese Klasse enthält Methoden zum Finden des FFmpeg-Executables, Überprüfen von TS-Dateien
    und Zusammenführen von TS-Dateien in eine einzelne Datei.
    Sie ist so konzipiert, dass sie in einem Docker-Container läuft, in dem FFmpeg bereits installiert ist.
    """
 
    def __init__(self, ts_file_paths, output_video_path=None):
        self.ffmpeg_exec_path = self.find_ffmpeg_executable()
        self.ts_file_paths = ts_file_paths
        self.output_filepath = output_video_path or os.path.join(os.path.expanduser('~'), 'Downloads')
        
    
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
        temp_input_file = get_unique_filename(
            os.path.join(ts_files_directory, "ffmpeg_input"), "txt"
        )

        try:
            valid_files = []
            log(f"Erstelle input.txt unter: {temp_input_file}")
            os.makedirs(
                ts_files_directory, exist_ok=True
            )  # Sicherstellen, dass der Ordner existiert
            with open(temp_input_file, "w", newline="\n") as f:
                for p in self.ts_file_paths:
                    abs_path = os.path.abspath(p)
                    exists = os.path.exists(abs_path)
                    size = os.path.getsize(abs_path) if exists else 0
                    valid_ts =self. is_valid_ts_file(abs_path) if exists and size > 0 else False
                    log(
                        f"Prüfe Segment: {abs_path} | Existiert: {exists} | Größe: {size} | MPEG-TS: {valid_ts}"
                    )
                    if exists and size > 0 and valid_ts:
                        f.write(f"file '{abs_path.replace(os.sep, '/')}'\n")
                        valid_files.append(abs_path)
                    else:
                        log(
                            f"WARNUNG: Segment fehlt, ist leer oder kein gültiges TS-Format: {abs_path}",
                            "warning",
                        )

            log("Inhalt von input.txt:")
            with open(temp_input_file, "r") as f:
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
                temp_input_file,  # Pfad zur input.txt
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
            if os.path.exists(temp_input_file):
                pass
                #os.remove(temp_input_file)
                

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

    # Setze den Agentennamen als Attribut der 'log'-Funktion, damit sie darauf zugreifen kann.
    # Dies ist der Mechanismus, um den Wert ohne globale Variable zu übergeben.
    log.agentName = args.agentName

    # --- Angepasstes Logging Setup für Live-Ausgabe ---
    # Hole den Logger direkt
    logger = logging.getLogger("seriendownloader")
    logger.setLevel(logging.INFO)  # Setze das allgemeine Level für den Logger

    # Optional: Entferne alle bestehenden Handler, falls basicConfig bereits aufgerufen wurde
    # Dies ist nützlich, wenn das Skript in einer Umgebung läuft, in der Logging bereits konfiguriert sein könnte.
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    # Erstelle einen Formatter mit dem gewünschten Format, inklusive Agentenname, Dateiname und Zeilennummer
    formatter = logging.Formatter(
        "%(asctime)s %(agentName)s %(levelname)s %(filename)s:%(lineno)d - %(message)s"
    )

    # Erstelle den FileHandler manuell.
    # ENTFERNT: 'buffering=1', da dies in Python-Versionen vor 3.9 einen TypeError verursacht.
    # Die Pufferung wird nun vom darunterliegenden Dateisystem gehandhabt.
    file_handler = logging.FileHandler(LOGFILE_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Erstelle den StreamHandler (für die Konsolenausgabe)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        driver = driverManager(headless=not args.no_headless, proxyAddresse=args.proxyAddresse)
        
        base_series_output_path = os.path.abspath(args.output_path)
        os.makedirs(base_series_output_path, exist_ok=True)
        log(f"Serien-Basisordner: {base_series_output_path}")

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
                        if (current_download_count % log_interval == 0) or (
                            current_download_count == total_segments
                        ):
                            log(
                                f"    Heruntergeladen: {current_download_count}/{total_segments} ({progress_percent:.1f}%) Segmente..."
                            )

            if not downloaded_ts_files:
                log(
                    "FEHLER: Keine TS-Segmente erfolgreich heruntergeladen. Kann nicht zusammenführen.",
                    "error",
                )
            else:
                for link in downloaded_ts_files:
                    print(f"Heruntergeladenes Segment: {link}\n")
                
                ffmpeg_executable = MergerManager(downloaded_ts_files, final_output_video_path)
                if ffmpeg_executable:
                    log("Starte Zusammenführung der TS-Dateien...")
                    downloaded_ts_files.sort()  # Wichtig für die korrekte Reihenfolge
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
