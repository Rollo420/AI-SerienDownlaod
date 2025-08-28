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
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from webdriver_manager.firefox import GeckoDriverManager
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
        
        
class driverManager:
    """
    Diese Klasse verwaltet den Browser und bietet Funktionen zum Laden von Proxys,
    Herunterladen von Dateien, Finden des FFmpeg-Executables und Zusammenführen von TS-Dateien.
    """

    def __init__(self, headless=False):
        self.headless = headless
        self.m3u8_first_filepath = None
        self.driver = self.initialize_driver()
        self.main_window_handle = self.driver.current_window_handle
        
        
    def initialize_driver(self):
        options = Options() # This is now Firefox Options
        if self.headless:
            log("Starte Firefox im Headless-Modus...")
            # The new headless argument for Firefox is "-headless"
            options.add_argument("-headless") 
        else:
            log("Starte Firefox im sichtbaren Modus...")

        # These arguments are generally safe for both
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-gpu")
        options.add_argument("--ignore-certificate-errors")

        # --- REMOVE THESE CHROME-ONLY LINES ---
        # options.add_argument("--disable-blink-features=AutomationControlled")
        # options.add_experimental_option("excludeSwitches", ["enable-automation"])
        # options.add_experimental_option("useAutomationExtension", False)
        
        # Adblock for Firefox uses a different format (.xpi), not .crx
        # You would need to download the .xpi file for Adblock Plus
        # adblock_path = "/path/to/adblock.xpi"
        # if os.path.exists(adblock_path):
        #     self.driver.install_addon(adblock_path, temporary=True)

        try:
            log(f"Firefox WebDriver wird initialisiert...")
            return webdriver.Firefox(
                service=Service(GeckoDriverManager().install()),
                options=options
            )
        except WebDriverException as e:
            log(f"FEHLER beim Initialisieren des WebDriver: {e}", "error")
            sys.exit(1)


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
                    #m3u8_manager = get_m3u8_urls(self.driver, "/app/Logs/m3u8_files")
                    #self.m3u8_files_dict = m3u8_manager.m3u8_files_dict
                    #self.m3u8_first_filepath = m3u8_manager.m3u8_first_filepath
                    
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
            print(f"Aktuelle Zeit: {current_time}/{duration}", "\r")

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

            #ts_urls.update(self.extract_segment_urls_from_performance_logs())

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

        sorted_ts_urls = ts_urls #sorted(list(ts_urls))

        return (
            True,
            episode_title,
            sorted_ts_urls,
        )  # Keine Selektoren zurückgeben, da sie lokal sind


def load_json_data(filename):
    print("Loading JSON data...")
    with open(filename, 'r') as file:
        data = json.load(file)
    print("JSON data loaded successfully.")
    return data

def get_series_data(serien):
    for serie in serien:
        # Serien Title
        title = serie["series_name"]
        for season in serie["seasons"]:
            # all Seasons
            season_number = season["season_number"]
            for episode in season["episode_links"]:
                # all Episodes
                episode_links = episode
                #await asyncio.sleep(random.uniform(2,5))  # Simulate processing time
                yield {
                    "title": title,
                    "season_number": season_number,
                    "episode_links": episode_links
                }

if __name__ == "__main__":
    # Make sure to activate your virtual environment before running this script:
    #   Windows: .venv\Scripts\activate
    #   Linux/Mac: source .venv/bin/activate

    filename = "all_series_data.json"  # Or whatever your JSON filename is
    
    driver = driverManager(headless=False)

    serienLinks = ["http://186.2.175.5/redirect/19261258", "http://186.2.175.5/redirect/19503708", "http://186.2.175.5/redirect/19466473", "http://186.2.175.5/serie/stream/the-last-of-us/staffel-2/episode-7"]

    for link in serienLinks:
        driver.stream_episode(link)