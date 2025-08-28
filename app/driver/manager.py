import os
import re
import sys
import time
import json
from datetime import datetime
from helper.wrapper.logger import Logging
import requests
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


# --- Konfiguration ---
DEFAULT_TIMEOUT = 60  # Timeout für das Warten auf Elemente
VIDEO_START_TIMEOUT = 120  # Spezifischer Timeout für den Video-Start-Versuch

class driverManager:
    """
    Diese Klasse verwaltet den Browser und bietet Funktionen zum Laden von Proxys,
    Herunterladen von Dateien, Finden des FFmpeg-Executables und Zusammenführen von TS-Dateien.
    """

    def __init__(self, headless=True, proxyAddresse=None):
        self.headless = headless
        self.proxyAddresse = proxyAddresse
        self.m3u8_first_filepath = None
        self.logger = Logging()
        self.proxies = self.load_and_filter_proxies() 
        self.driver = self.initialize_driver()
        self.main_window_handle = self.driver.current_window_handle
        
        
    def initialize_driver(self):
        options = Options()
        if self.headless:
            self.logger.log("Starte Chromium im Headless-Modus (im Docker-Container)...", "info")
            options.add_argument("--headless=new")
        else:
            self.logger.log("Starte Chromium im sichtbaren Modus (im Docker-Container via VNC)...")
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
            self.logger.log(f"Konfiguriere Browser für Proxy: {self.proxyAddresse}")
            options.add_argument(f"--proxy-server={self.proxyAddresse}")

        # Adblock Plus Extension laden (Pfad im Container anpassen!)
        adblock_path = "/app/src/adblockplus.crx"
        if os.path.exists(adblock_path):
            options.add_extension(adblock_path)
        try:
            selenium_hub_url = os.getenv(
                "SELENIUM_HUB_URL", "http://selenium-chromium:4444/wd/hub"
            )
             
            self.logger.log(f"Chromium WebDriver erfolgreich mit {selenium_hub_url} verbunden.")
            return webdriver.Remote(command_executor=selenium_hub_url, options=options)
        except WebDriverException as e:
            self.logger.log(f"FEHLER beim Initialisieren des WebDriver: {e}", "error")
            sys.exit(1)


    def load_and_filter_proxies(self):
        """ 
        Lädt Proxys aus einer JSON-Datei, filtert nach "alive": true und "http"-Protokoll.
        Gibt eine Liste von Proxy-Strings zurück (z.B. "http://ip:port").
        """
        proxies_list = []
        self.logger.log("Versuche, Proxys von der API abzurufen...", "info")
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
                            self.logger.log(
                                f"Proxy geladen: {proxy_string} (Anonymität: {proxy_entry.get('anonymity')}, Land: {proxy_entry.get('ip_data', {}).get('country')})",
                                "debug",
                            )
            else:
                self.logger.log("WARNUNG: 'proxies'-Schlüssel nicht in der API-Antwort gefunden.", "warning")
        except requests.exceptions.RequestException as e:
            self.logger.log(f"FEHLER: Fehler beim Abrufen der Proxys von der API: {e}. Fahre ohne Proxys fort.", "error")
        except json.JSONDecodeError as e:
            self.logger.log(f"FEHLER: Ungültiges JSON-Format in der API-Antwort: {e}. Fahre ohne Proxys fort.", "error")
        except Exception as e:
            self.logger.log(f"Ein unerwarteter Fehler beim Laden der Proxys aufgetreten: {e}. Fahre ohne Proxys fort.","error")

        if not proxies_list:
            self.logger.log("WARNUNG: Keine gültigen Proxys gefunden oder geladen. Der Browser wird ohne Proxy gestartet.","warning")

        return proxies_list

    def handle_new_tabs_and_focus(self, main_window_handle: str):
        """
        Überprüft und schließt alle neuen Browser-Tabs (Pop-ups) und kehrt zum Haupt-Tab zurück.
        """
        try:
            handles = self.driver.window_handles
            if len(handles) > 1:
                self.logger.log(
                    f"NEUE FENSTER/TABS ERKANNT: {len(handles) - 1} Pop-up(s). Schließe diese..."
                )
                for handle in handles:
                    if handle != main_window_handle:
                        try:
                            self.driver.switch_to.window(handle)
                            self.driver.close()
                            self.logger.log(f"Pop-up-Tab '{handle}' geschlossen.")
                        except Exception as e:
                            self.logger.log(
                                f"WARNUNG: Konnte Pop-up-Tab '{handle}' nicht schließen: {e}",
                                "warning",
                            )
                self.driver.switch_to.window(main_window_handle)  # Zurück zum Haupt-Tab
                time.sleep(1)  # Kurze Pause nach dem Schließen
        except Exception as e:
            self.logger.log(f"FEHLER: Probleme beim Verwalten von Browser-Fenstern: {e}", "error")


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
            self.logger.log(
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
                    self.logger.log(f"Versuche, Popup mit Klick-Selektor '{selector}' zu schließen.")
                    self.driver.execute_script("arguments[0].click();", element)
                    # time.sleep(0.2)
                except (
                    TimeoutException,
                    NoSuchElementException,
                    ElementClickInterceptedException,
                ):
                    pass
                except Exception as e:
                    self.logger.log(
                        f"Fehler beim Schließen eines Popups mit Klick ({selector}): {e}",
                        "warning",
                    )

            # Dann versuchen, unerwünschte Elemente direkt zu entfernen
            for selector in overlay_selectors_to_remove:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed():  # Nur sichtbare Overlays entfernen
                            self.logger.log(
                                f"Versuche, Overlay mit Selektor '{selector}' direkt zu entfernen."
                            )
                            self.driver.execute_script("arguments[0].remove();", element)
                            # time.sleep(0.2)
                except (StaleElementReferenceException, NoSuchElementException):
                    pass  # Element wurde bereits entfernt oder existiert nicht mehr
                except Exception as e:
                    self.logger.log(
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
                            self.logger.log("Entferne sekundäres, sichtbares Overlay-Body-Element.")
                            self.driver.execute_script("arguments[0].remove();", body)
                            # time.sleep(0.2)
                except StaleElementReferenceException:
                    pass  # Element wurde bereits entfernt
                except Exception as e:
                    self.logger.log(
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
                        self.logger.log("Entferne potenzielles Overlay-iframe.")
                        self.driver.execute_script("arguments[0].remove();", iframe)
                        # time.sleep(0.5)
                    elif (
                        iframe.is_displayed()
                    ):  # Wenn nicht als Overlay erkannt, aber sichtbar, kann es Adblock sein
                        self.logger.log(
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
                                        self.logger.log(
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
                                    self.logger.log(f"Klicke auf Button in Iframe: {inner_sel}")
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
                                            self.logger.log(
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
                                    self.logger.log(
                                        f"Fehler beim Klicken im Iframe ({inner_sel}): {inner_e}",
                                        "warning",
                                    )

                            self.driver.switch_to.default_content()
                        except Exception as switch_e:
                            self.logger.log(
                                f"FEHLER: Konnte nicht in Iframe wechseln oder dort interagieren: {switch_e}",
                                "warning",
                            )
                            self.driver.switch_to.default_content()  # Immer zurückwechseln!

                except StaleElementReferenceException:
                    pass  # Element wurde bereits entfernt
                except Exception as e:
                    self.logger.log(f"Fehler beim Bearbeiten eines iframe: {e}", "warning")

            # Sicherstellen, dass keine neuen Tabs geöffnet wurden
            self.handle_new_tabs_and_focus(self.main_window_handle)

        except Exception as e:
            self.logger.log(f"FEHLER beim Entfernen von Overlays und iframes: {e}", "error")


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
        
        self.logger.log(f"\nNavigiere zu: {url}")
        self.driver.get(url)
        main_window_handle = self.driver.current_window_handle

        WebDriverWait(self.driver, DEFAULT_TIMEOUT).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        self.logger.log("Seite geladen. Suche nach Popups und Overlays...")
        # close_overlays_and_iframes muss ebenfalls Zugriff auf seine eigene Lernvariable haben
        # oder diese als Parameter übergeben bekommen und zurückgeben.
        # Für diese Funktion gehen wir davon aus, dass close_overlays_and_iframes entweder globalen Zustand verwaltet
        # oder keine Lernfunktion benötigt.
        self.close_overlays_and_iframes()

        episode_title = self.get_episode_title()
        self.logger.log(f"Erkannter Episodentitel: {episode_title}")

        self.logger.log(
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
            self.logger.log(
                f"Versuche Video zu starten (Zeit vergangen: {int(time.time() - start_time_attempt)}s/{max_startup_duration}s)..."
            )

            # Iteriere über die priorisierte Liste der Selektoren
            for selector in video_start_selectors_prioritized:
                current_time, duration, paused = self.get_current_video_progress()
                if duration > 0 and current_time > 0.1 and not paused:
                    video_started_successfully = True
                    self.logger.log(
                        f"Video läuft bereits nach initialen Bereinigungen. Kein weiterer Startversuch nötig."
                    )
                    break  # Video läuft, Schleife beenden

                for attempt_num in range(num_attempts_per_selector):
                    self.logger.log(
                        f"-> Versuche mit Selektor '{selector}' (Versuch {attempt_num + 1}/{num_attempts_per_selector})..."
                    )

                    if selector == "JS_play":
                        # 1. VERSUCH: Direkter JavaScript play() auf das Video-Element
                        try:
                            self.logger.log(
                                "-> Versuche Video direkt per JavaScript play() zu starten."
                            )
                            self.driver.execute_script(
                                """
                                var v = document.querySelector('video');
                                if(v) { 
                                    v.play(); 
                                    console.self.logger.log('Video play() called via JavaScript.');
                                } else {
                                    console.self.logger.log('No <video> element found for JS play() in this attempt.');
                                }
                            """
                            )
                            time.sleep(0.5)  # Kurze Pause, um JS-Effekte abzuwarten

                            current_time, duration, paused = self.get_current_video_progress()
                            if duration > 0 and current_time > 0.1 and not paused:
                                self.logger.log(
                                    f"Video per JavaScript erfolgreich gestartet bei {current_time:.2f}/{duration:.2f} Sekunden."
                                )
                                video_started_successfully = True
                                # Selektor an den Anfang der Liste verschieben (Lernfunktion)
                                if "JS_play" in video_start_selectors_prioritized:
                                    video_start_selectors_prioritized.remove("JS_play")
                                video_start_selectors_prioritized.insert(0, "JS_play")
                                break  # Erfolgreich, innere Schleife beenden
                            elif paused and duration > 0:
                                self.logger.log(
                                    f"Video pausiert nach JS-play() bei {current_time:.2f}/{duration:.2f}. Versuche erneuten JS-play() oder nächsten Selektor."
                                )
                        except Exception as e:
                            self.logger.log(
                                f"FEHLER beim JS-Startversuch: {e}", "debug"
                            )  # Debug, da oft nur Video noch nicht da

                    elif selector == "video":
                        # 2. VERSUCH: Klick auf das 'video'-Element (falls es anklickbar wird)
                        try:
                            self.logger.log("-> Versuche Klick auf den 'video'-Selektor.")
                            video_element = WebDriverWait(self.driver, 2).until(  # Kurzer Timeout für diesen Versuch
                                EC.element_to_be_clickable((By.CSS_SELECTOR, "video"))
                            )
                            self.driver.execute_script("arguments[0].click();", video_element)
                            time.sleep(0.5)  # Kurze Pause nach Klick

                            current_time, duration, paused = self.get_current_video_progress()
                            if duration > 0 and current_time > 0.1 and not paused:
                                self.logger.log(
                                    f"Video erfolgreich über Selektor 'video' gestartet bei {current_time:.2f}/{duration:.2f} Sekunden."
                                )
                                video_started_successfully = True
                                # Selektor an den Anfang der Liste verschieben (Lernfunktion)
                                if "video" in video_start_selectors_prioritized:
                                    video_start_selectors_prioritized.remove("video")
                                video_start_selectors_prioritized.insert(0, "video")
                                
                                break  # Erfolgreich, innere Schleife beenden
                            elif paused and duration > 0:
                                self.logger.log(f"Video pausiert nach 'video'-Klick bei {current_time:.2f}/{duration:.2f}. Versuche erneuten JS-play().")
                        except (
                            TimeoutException,
                            NoSuchElementException,
                            ElementClickInterceptedException,
                            StaleElementReferenceException,
                        ) as e:
                            self.logger.log(
                                f"Klick auf 'video'-Selektor nicht möglich/gefunden: {e}",
                                "debug",
                            )
                        except Exception as e:
                            self.logger.log(
                                f"Unerwarteter Fehler beim Klick auf 'video': {e}",
                                "warning",
                            )

                    elif selector == "ActionChains_video_click":
                        # 3. VERSUCH: Klick auf das 'video'-Element per ActionChains (Maus-Emulation)
                        try:
                            self.logger.log(
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
                                self.logger.log(
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
                                self.logger.log(
                                    f"Video pausiert nach ActionChains-Klick bei {current_time:.2f}/{duration:.2f}. Versuche erneuten JS-play()."
                                )
                        except (
                            TimeoutException,
                            NoSuchElementException,
                            ElementClickInterceptedException,
                            StaleElementReferenceException,
                        ) as e:
                            self.logger.log(
                                f"ActionChains-Klick auf 'video'-Selektor nicht möglich/gefunden: {e}",
                                "debug",
                            )
                        except Exception as e:
                            self.logger.log(
                                f"Unerwarteter Fehler beim ActionChains-Klick auf 'video': {e}",
                                "warning",
                            )

                    else:  # Normale Play-Button-Selektoren
                        try:
                            self.logger.log(f"-> Probiere Selektor: '{selector}'")
                            play_button = WebDriverWait(self.driver, 3).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                            )

                            self.driver.execute_script("arguments[0].click();", play_button)
                            time.sleep(0.5)

                            # close_overlays_and_iframes(driver) # Nach Klick Popups erneut schließen (kann hier weggelassen werden, da es in der Hauptschleife passiert)

                            current_time, duration, paused = self.get_current_video_progress()
                            if duration > 0 and current_time > 0.1 and not paused:
                                self.logger.log(
                                    f"Video erfolgreich über Selektor '{selector}' gestartet bei {current_time:.2f}/{duration:.2f} Sekunden."
                                )
                                video_started_successfully = True
                                # Selektor an den Anfang der Liste verschieben (Lernfunktion)
                                if selector in video_start_selectors_prioritized:
                                    video_start_selectors_prioritized.remove(selector)
                                video_start_selectors_prioritized.insert(0, selector)
                                break  # Erfolgreich, innere Schleife beenden
                            elif paused and duration > 0:
                                self.logger.log(
                                    f"Video ist nach Klick auf '{selector}' pausiert bei {current_time:.2f}/{duration:.2f}. Versuche JS-play()."
                                )
                                self.driver.execute_script(
                                    "document.querySelector('video').play();"
                                )
                                time.sleep(0.5)
                                current_time, duration, paused = self.get_current_video_progress()
                                if duration > 0 and current_time > 0.1 and not paused:
                                    self.logger.log(
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
                            self.logger.log(
                                f"Selektor '{selector}' nicht gefunden/klickbar: {e}",
                                "debug",
                            )
                        except Exception as e:
                            self.logger.log(
                                f"Unerwarteter Fehler beim Startversuch mit '{selector}': {e}",
                                "warning",
                            )

                    if video_started_successfully:
                        break  # Innere Schleife beenden, wenn Video gestartet

                if video_started_successfully:
                    m3u8_manager = m3u8(self.driver, "/app/Logs/m3u8_files")
                    self.m3u8_files_dict = m3u8_manager.m3u8_files_dict
                    self.m3u8_first_filepath = m3u8_manager.m3u8_first_filepath
                    
                    break  # Äußere Schleife beenden, wenn Video gestartet

            # Bereinigung nach jedem Schleifendurchlauf der Selektoren
            # close_overlays_and_iframes(driver) # Dies kann hier wieder aktiviert werden, wenn nötig, aber es ist bereits in der Haupt-while-Schleife.

            if not video_started_successfully:
                time.sleep(
                    1
                )  # Kurze Pause vor der nächsten Iteration des aggressiven Starts

        # Finaler Check und Abbruch
        if not video_started_successfully:
            self.logger.log(
                "FEHLER: Video konnte nach allen Versuchen nicht gestartet werden. Abbruch des Streamings.",
                "error",
            )
            return (
                False,
                episode_title,
                [],
            )  # Keine Selektoren zurückgeben, da sie lokal sind

        self.logger.log(
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
            print(f"Aktuelle Zeit: {current_time}/{duration}", "\r")

            if duration > 0 and current_time >= duration - 3.0:
                self.logger.log(
                    f"Video fast am Ende oder beendet: {current_time:.2f}/{duration:.2f}. Beende Überwachung."
                )
                break

            if paused:
                self.logger.log(
                    f"Video pausiert bei {current_time:.2f}/{duration:.2f} Sekunden, versuche es zu starten."
                )
                self.driver.execute_script("document.querySelector('video').play();")
                time.sleep(1)

            if current_time == last_current_time and current_time > 0.1:
                if time.time() - stalled_check_time > stalled_timeout:
                    self.logger.log(
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
                self.logger.log(
                    f"WARNUNG: Videodauer nicht verfügbar und Überwachung läuft seit über {max_monitoring_time_if_duration_unknown/3600:.1f} Stunden. Beende Überwachung.",
                    "warning",
                )
                break

            ts_urls.update(self.extract_segment_urls_from_performance_logs())

            time.sleep(3)  # Pause, um Browser-Aktivität zu beobachten und Logs zu sammeln

        self.logger.log(f"Überwachung beendet. Insgesamt {len(ts_urls)} einzigartige TS-URLs gefunden.")

        if not ts_urls:
            self.logger.log(
                "KEINE TS-URLs gefunden. Die Seite hat möglicherweise keine TS-Streams oder ein Problem ist aufgetreten.",
                "error",
            )
            return (
                False,
                episode_title,
                [],
            )  # Keine Selektoren zurückgeben, da sie lokal sind

        sorted_ts_urls = ts_urls #sorted(list(ts_urls))

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
            self.logger.log(f"Fehler beim Abrufen oder Leeren der Performance-Logs: {e}", "error")
        except Exception as e:
            self.logger.log(f"Ein unerwarteter Fehler beim Extrahieren von URLs: {e}", "error")
        return found_urls

