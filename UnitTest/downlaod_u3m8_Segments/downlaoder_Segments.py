import time
import os
import re
import json
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    WebDriverException,
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
)
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

# --- Konfiguration ---
# HIER DIE URL DER WEBPAGE EINFÜGEN, die das Video enthält
webpage_url = "https://jilliandescribecompany.com/e/kbp2tjivtgrj"
# HIER DEN NAMEN FÜR DIE FINALE VIDEODATEI EINFÜGEN
output_file_name = "gesamtes_video.ts"
DEFAULT_TIMEOUT = 10


def log(message, level="info"):
    """Eine einfache Log-Funktion, die Nachrichten in der Konsole ausgibt."""
    if level == "error":
        print(f"[FEHLER] {message}")
    elif level == "warning":
        print(f"[WARNUNG] {message}")
    else:
        print(f"[INFO] {message}")


def initialize_driver(headless=True):
    """
    Initialisiert den Chrome-WebDriver für die lokale Ausführung.
    """
    options = Options()
    if headless:
        log("Starte Chrome im Headless-Modus.")
        options.add_argument("--headless=new")
    else:
        log("Starte Chrome im sichtbaren Modus.")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Adblock-Erweiterung für die lokale Ausführung
    # Da der Pfad zur Erweiterung auf deinem System unbekannt ist,
    # ist dieser Teil auskommentiert.
    # adblock_path = "pfad/zu/deiner/adblockplus.crx"
    # if os.path.exists(adblock_path):
    #     options.add_extension(adblock_path)
    # else:
    #     log("Adblock-Erweiterungsdatei nicht gefunden, wird übersprungen.", "warning")

    try:
        # Hier wird der lokale ChromeDriver verwendet
        driver = webdriver.Chrome(options=options)
        log("Chrome-WebDriver erfolgreich initialisiert.")
        return driver
    except WebDriverException as e:
        log(f"FEHLER beim Initialisieren des WebDriver: {e}", "error")
        exit(1)


def get_episode_title(driver):
    """Platzhalterfunktion, die einen Titel zurückgibt."""
    return "Unbekannter Titel"


def close_overlays_and_iframes(driver):
    """
    Platzhalterfunktion für das Schließen von Popups und Overlays.
    In einer realen Anwendung müsste hier Logik mit spezifischen Selektoren stehen.
    """
    try:
        # Beispiel: Schließt einen Popup-Iframe
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for frame in iframes:
            try:
                driver.switch_to.frame(frame)
                close_button = driver.find_element(By.CSS_SELECTOR, ".close-button")
                close_button.click()
                driver.switch_to.default_content()
                print("Ein Popup-Iframe wurde geschlossen.")
            except:
                driver.switch_to.default_content()
    except Exception as e:
        pass


def get_current_video_progress(driver):
    """
    Ruft den Wiedergabestatus des Video-Elements per JavaScript ab.
    Gibt die aktuelle Zeit, die Gesamtdauer und den Pausenstatus zurück.
    """
    try:
        result = driver.execute_script(
            """
            var v = document.querySelector('video');
            if (v) {
                return [v.currentTime, v.duration, v.paused];
            }
            return [0, 0, true];
        """
        )
        return result[0], result[1], result[2]
    except Exception as e:
        log(f"Fehler beim Abrufen des Videostatus: {e}", "warning")
        return 0, 0, True


def extract_m3u8_url_from_performance_logs(driver):
    """
    Extrahiert die erste gefundene .m3u8-URL aus den Performance-Logs des Browsers.
    """
    try:
        logs = driver.get_log("performance")
        for log_entry in logs:
            try:
                message = json.loads(log_entry["message"])
                request = message["message"]["params"]["request"]
                url = request["url"]
                if ".m3u8" in url:
                    log(f"M3U8-URL in den Logs gefunden: {url}")
                    return url
            except (json.JSONDecodeError, KeyError):
                continue
    except Exception as e:
        log(f"Fehler beim Auslesen der Performance-Logs: {e}", "warning")
    return None


def stream_episode_and_find_m3u8(driver, url):
    """
    Simuliert das Abspielen einer Episode, um die .m3u8-URL zu erfassen.
    """
    # Lokale Liste für die Priorisierung der Videostart-Selektoren
    video_start_selectors_prioritized = [
        "JS_play",
        "video",
        "ActionChains_video_click",
        "div.vjs-big-play-button",
        "button[title='Play Video']",
        "button.play-button",
        "button[aria-label='Play']",
        ".jw-icon-playback",
        "div.player-button.play",
        "div.plyr__controls button.plyr__controls__item--play",
    ]

    print(f"\nNavigiere zu: {url}")
    driver.get(url)
    WebDriverWait(driver, DEFAULT_TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    print("Seite geladen. Suche nach Popups und Overlays...")
    close_overlays_and_iframes(driver)

    episode_title = get_episode_title(driver)
    print(f"Erkannter Episodentitel: {episode_title}")

    print("Starte aggressive Schleife für Video-Start und Suche nach m3u8-URL...")

    video_started_successfully = False
    m3u8_url_found = None
    max_startup_duration = 60
    start_time_attempt = time.time()
    num_attempts_per_selector = 3

    while not m3u8_url_found and (
        time.time() - start_time_attempt < max_startup_duration
    ):
        print(
            f"Versuche Video zu starten und m3u8-URL zu finden (Zeit vergangen: {int(time.time() - start_time_attempt)}s/{max_startup_duration}s)..."
        )

        for selector in video_start_selectors_prioritized:
            current_time, duration, paused = get_current_video_progress(driver)
            if duration > 0 and current_time > 0.1 and not paused:
                video_started_successfully = True
                print(
                    "Video läuft bereits nach initialen Bereinigungen. Kein weiterer Startversuch nötig."
                )
                m3u8_url_found = extract_m3u8_url_from_performance_logs(driver)
                if m3u8_url_found:
                    return m3u8_url_found
                break

            for attempt_num in range(num_attempts_per_selector):
                print(
                    f"-> Versuche mit Selektor '{selector}' (Versuch {attempt_num + 1}/{num_attempts_per_selector})..."
                )

                try:
                    if selector == "JS_play":
                        driver.execute_script(
                            "var v = document.querySelector('video'); if(v) { v.play(); }"
                        )
                    elif selector == "video":
                        video_element = WebDriverWait(driver, 2).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, "video"))
                        )
                        driver.execute_script("arguments[0].click();", video_element)
                    elif selector == "ActionChains_video_click":
                        video_element = WebDriverWait(driver, 2).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, "video"))
                        )
                        ActionChains(driver).move_to_element(
                            video_element
                        ).click().perform()
                    else:
                        play_button = WebDriverWait(driver, 3).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                        )
                        driver.execute_script("arguments[0].click();", play_button)

                    time.sleep(1)

                    current_time, duration, paused = get_current_video_progress(driver)
                    if duration > 0 and current_time > 0.1 and not paused:
                        print(f"Video erfolgreich gestartet mit '{selector}'.")
                        video_started_successfully = True
                        m3u8_url_found = extract_m3u8_url_from_performance_logs(driver)
                        if m3u8_url_found:
                            return m3u8_url_found
                        break
                    elif paused and duration > 0:
                        driver.execute_script("document.querySelector('video').play();")
                        time.sleep(0.5)
                        current_time, duration, paused = get_current_video_progress(
                            driver
                        )
                        if duration > 0 and current_time > 0.1 and not paused:
                            print(
                                f"Video nach Klick auf '{selector}' erfolgreich gestartet."
                            )
                            video_started_successfully = True
                            m3u8_url_found = extract_m3u8_url_from_performance_logs(
                                driver
                            )
                            if m3u8_url_found:
                                return m3u8_url_found
                            break
                except Exception as e:
                    print(f"Versuch mit Selektor '{selector}' fehlgeschlagen: {e}")

            if m3u8_url_found:
                break

        if not m3u8_url_found:
            time.sleep(2)

    if not m3u8_url_found:
        print(
            "FEHLER: Video konnte nicht gestartet oder M3U8-URL nicht gefunden werden. Abbruch."
        )
        return None

    return m3u8_url_found


def download_hls_stream(m3u8_url):
    """
    Lädt alle Segmente eines HLS-Streams herunter und fügt sie zusammen.
    """
    print(f"\nStarte Download von HLS-Stream mit der m3u8-Datei: {m3u8_url}")

    base_url = m3u8_url.rsplit("/", 1)[0] + "/"

    try:
        response = requests.get(m3u8_url)
        response.raise_for_status()
        m3u8_content = response.text

        ts_file_paths = []
        lines = m3u8_content.splitlines()
        for line in lines:
            if line.endswith(".ts"):
                full_segment_url = base_url + line
                ts_file_name = os.path.basename(full_segment_url)
                ts_file_path = os.path.join("downloads", ts_file_name)
                ts_file_paths.append(ts_file_path)

                print(f"Lade Segment herunter: {ts_file_name}")
                segment_response = requests.get(full_segment_url)
                segment_response.raise_for_status()

                os.makedirs("downloads", exist_ok=True)
                with open(ts_file_path, "wb") as f:
                    f.write(segment_response.content)

        print("Alle Segmente erfolgreich heruntergeladen.")

        print(f"Füge Segmente zu '{output_file_name}' zusammen...")
        with open(output_file_name, "wb") as outfile:
            for path in ts_file_paths:
                with open(path, "rb") as infile:
                    outfile.write(infile.read())
                os.remove(path)

        os.rmdir("downloads")
        print(f"Video erfolgreich als '{output_file_name}' gespeichert!")

    except requests.exceptions.RequestException as e:
        print(f"Ein Fehler ist aufgetreten: {e}")
    except Exception as e:
        print(f"Ein unerwarteter Fehler ist aufgetreten: {e}")


# --- Hauptlogik ---
if __name__ == "__main__":
    try:
        # Hier wird der lokale Treiber mit den von dir gewünschten Optionen initialisiert.
        driver = initialize_driver()
        # Hinweis: Um das Browserfenster zu sehen, ändere "initialize_driver(headless=True)"
        # in "initialize_driver(headless=False)".
    except Exception as e:
        print(f"Fehler beim Initialisieren des Webdrivers: {e}")
        exit()

    try:
        m3u8_url = stream_episode_and_find_m3u8(driver, webpage_url)
        if m3u8_url:
            download_hls_stream(m3u8_url)
        else:
            print(
                "Download konnte nicht gestartet werden, da keine M3U8-URL gefunden wurde."
            )
    finally:
        driver.quit()
