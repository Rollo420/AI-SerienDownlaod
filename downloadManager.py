import subprocess
import os
import time
import re
import sys
import httpx
import zipfile
import requests
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    ElementClickInterceptedException,
)
from bs4 import BeautifulSoup
from webdriver_manager.firefox import (
    GeckoDriverManager,
)  # Import for automatic driver management


class ManageDownload:
    def __init__(self, download_target_path: str):
        # Set download_target_path for final merged video
        self.download_target_path = os.path.abspath(download_target_path)
        os.makedirs(
            self.download_target_path, exist_ok=True
        )  # Ensure target path exists
        print(f"Zielpfad für fertige Videos: {self.download_target_path}")

        # Base directory for temporary downloaded segments
        self.output_dir_segments_base = os.path.abspath(
            os.path.join(os.getcwd(), "downloaded_segments_temp")
        )
        os.makedirs(self.output_dir_segments_base, exist_ok=True)
        self.current_segment_dir = None  # Will be set dynamically per download
        print(
            f"Basisverzeichnis für temporäre Segmente: {self.output_dir_segments_base}"
        )

        self.ffmpeg_base_dir = os.path.abspath(
            os.path.join(os.getcwd(), "ffmpeg_tools")
        )
        self.ffmpeg_path = os.path.join(self.ffmpeg_base_dir, "bin", "ffmpeg.exe")

        self.segment_urls = []
        self.url = None
        self.driver = self.load_driver()  # WebDriver directly loaded

    def download_file_to_dir(
        self, url: str, directory_path: str, filename: str
    ) -> bool:
        """
        Lädt eine Datei von der angegebenen URL herunter und speichert sie im Zielverzeichnis.
        Gibt True bei Erfolg, False bei Fehler zurück.
        """
        file_path = os.path.join(directory_path, filename)
        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            print(
                f"Datei '{filename}' existiert bereits in '{directory_path}' und ist nicht leer. Überspringe Download."
            )
            return True

        try:
            print(f"Lade Datei '{filename}' von '{url}' herunter...")
            with httpx.stream(
                "GET", url, follow_redirects=True, timeout=60.0
            ) as response:
                response.raise_for_status()  # Raises an exception for HTTP errors
                total_size = int(response.headers.get("content-length", 0))
                downloaded_size = 0

                with open(file_path, "wb") as file:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        file.write(chunk)
                        downloaded_size += len(chunk)
                        if total_size > 0:
                            progress = (downloaded_size / total_size) * 100
                            print(
                                f"\rFortschritt: {progress:.2f}% ({downloaded_size}/{total_size} Bytes)",
                                end="",
                                flush=True,
                            )
                print(f"\nDatei erfolgreich heruntergeladen: {file_path}")
                return True
        except httpx.RequestError as e:
            print(
                f"\nFEHLER: Beim Herunterladen von {url} ist ein Netzwerkfehler aufgetreten: {e}"
            )
            return False
        except Exception as e:
            print(
                f"\nFEHLER: Ein unerwarteter Fehler ist beim Herunterladen von {url} aufgetreten: {e}"
            )
            return False

    def load_driver(self, headless: bool = False) -> webdriver.Firefox:
        """
        Initialisiert und konfiguriert den Selenium WebDriver für Firefox.
        Installiert uBlock Origin als Adblocker.
        """
        current_script_dir = os.path.dirname(os.path.realpath(__file__))
        adblock_xpi = os.path.join(current_script_dir, "ublock_origin.xpi")
        ublock_download_link = "https://addons.mozilla.org/firefox/downloads/file/4216633/ublock_origin-1.55.0.xpi"

        # Sicherstellen, dass uBlock Origin heruntergeladen ist
        if not self.download_file_to_dir(
            ublock_download_link, current_script_dir, "ublock_origin.xpi"
        ):
            print(
                "WARNUNG: uBlock Origin konnte nicht heruntergeladen werden. Werbung/Pop-ups könnten auftreten."
            )

        try:
            firefox_options = Options()
            if headless:
                firefox_options.add_argument("--headless")
                # firefox_options.add_argument("--mute-audio")

            # Firefox Präferenzen für Download-Verhalten und Medienwiedergabe
            firefox_options.set_preference("dom.webnotifications.enabled", False)
            firefox_options.set_preference("browser.download.folderList", 2)
            firefox_options.set_preference(
                "browser.download.manager.showWhenStarting", False
            )
            firefox_options.set_preference(
                "browser.download.dir",
                self.download_target_path,  # Keep this as download target for general browser downloads, though not directly used for segments
            )
            firefox_options.set_preference(
                "browser.helperApps.neverAsk.saveToDisk",
                "video/mp4,application/x-mpegURL,video/webm,video/x-flv,application/octet-stream,application/vnd.apple.mpegurl",
            )

            # Autoplay und Medien erlauben (wichtig für Stream-Erkennung)
            firefox_options.set_preference(
                "media.autoplay.default", 0
            )  # Erlaube Autoplay
            firefox_options.set_preference(
                "media.autoplay.allow-muted", True
            )  # Erlaube Autoplay, auch wenn stumm
            firefox_options.set_preference(
                "media.volume_scale", "0.0"
            )  # Setze Lautstärke auf 0 (stumm)
            firefox_options.set_preference(
                "media.mediasource.enabled", True
            )  # Aktiviere MediaSource API
            firefox_options.set_preference(
                "media.fragmented-mp4.enabled", True
            )  # Aktiviere Fragmented MP4 (für DASH)

            print("Initialisiere Firefox WebDriver...")
            service = webdriver.firefox.service.Service(GeckoDriverManager().install())
            driver = webdriver.Firefox(service=service, options=firefox_options)

            # Addons installieren (temporär für diese Sitzung)
            try:
                driver.install_addon(adblock_xpi, temporary=True)
                print("uBlock Origin Addon installiert.")
                time.sleep(3)  # Give adblocker time to initialize
            except WebDriverException as e:
                print(
                    f"WARNUNG: Konnte uBlock Origin Addon nicht installieren: {e}. Pop-ups/Werbung könnten weiterhin auftreten."
                )

            return driver

        except Exception as e:
            print(
                f"KRITISCHER FEHLER: Beim Initialisieren des WebDriver ist ein Problem aufgetreten: {e}"
            )
            sys.exit(1)

    def quit_driver(self):
        """Schließt den WebDriver, falls er aktiv ist."""
        if self.driver:
            print("Schließe WebDriver...")
            self.driver.quit()
            self.driver = None  # Set to None after quitting

    def get_episode_title(self, soup: BeautifulSoup) -> str:
        """Extrahiert den Titel der Episode aus dem HTML-Soup."""
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.text.strip()
            # Entferne gängige Website-Suffixe/Präfixe, die oft durch '|', '-' oder '–' getrennt sind
            cleaned_title = re.split(r"\||-|–", title)[0].strip()
            # Ersetze ungültige Zeichen durch Unterstriche für Dateinamen
            return re.sub(r'[<>:"/\\|?*]', "_", cleaned_title)
        return "Unbekannter_Video_Titel"

    def click_video_element(
        self, video_element: webdriver.remote.webelement.WebElement
    ):
        """
        Versucht, auf das Videoelement zu klicken, um die Wiedergabe zu starten oder Overlays zu entfernen.
        Wiederholt den Versuch, wenn der Klick blockiert wird.
        """
        max_click_attempts = 5
        for attempt in range(max_click_attempts):
            try:
                # Scrolle das Element in den sichtbaren Bereich
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", video_element
                )
                # Versuche, über JavaScript zu klicken, da es robuster bei Überlagerungen sein kann
                self.driver.execute_script("arguments[0].click();", video_element)
                print(
                    f"Videoelement erfolgreich geklickt (Versuch {attempt+1}/{max_click_attempts})."
                )
                time.sleep(2)  # Kurze Pause nach Klick
                return True
            except ElementClickInterceptedException:
                print(
                    f"Klick auf Videoelement blockiert (Versuch {attempt+1}/{max_click_attempts}). Versuche, Überlagerungen zu entfernen..."
                )
                # Versuche, gängige Overlay-Elemente zu finden und zu schließen
                try:
                    close_buttons = self.driver.find_elements(
                        By.XPATH,
                        "//button[contains(text(), 'Schließen') or contains(text(), 'Akzeptieren') or contains(text(), 'OK')] | //div[contains(@class, 'overlay-close') or contains(@class, 'cookie-banner')]//button",
                    )
                    for btn in close_buttons:
                        if btn.is_displayed() and btn.is_enabled():
                            print(
                                f"Klicke auf potenzielles Overlay-Element: {btn.text[:20]}..."
                            )
                            try:
                                btn.click()
                                time.sleep(1)  # Kurze Pause nach dem Klick
                                break  # Only close one overlay per attempt
                            except Exception as e:
                                print(f"Konnte Overlay-Button nicht anklicken: {e}")
                except Exception as e:
                    print(f"Fehler beim Suchen nach Overlay-Elementen: {e}")
                time.sleep(3)  # Längere Pause vor dem nächsten Klick-Versuch
            except Exception as e:
                print(
                    f"Unerwarteter Fehler beim Klicken auf Videoelement (Versuch {attempt+1}/{max_click_attempts}): {e}"
                )
                time.sleep(2)
        print(
            "WARNUNG: Konnte Videoelement nach mehreren Versuchen nicht erfolgreich anklicken."
        )
        return False

    def handle_popups_and_focus(self, main_window_handle: str):
        """
        Überprüft und schließt alle neuen Browser-Tabs (Pop-ups) und kehrt zum Haupt-Tab zurück.
        """
        try:
            handles = self.driver.window_handles
            if len(handles) > 1:
                print(
                    f"NEUE FENSTER/TABS ERKANNT: {len(handles) - 1} Pop-up(s). Schließe diese..."
                )
                for handle in handles:
                    if handle != main_window_handle:
                        try:
                            self.driver.switch_to.window(handle)
                            self.driver.close()
                            print(f"Pop-up-Tab '{handle}' geschlossen.")
                        except Exception as e:
                            print(
                                f"WARNUNG: Konnte Pop-up-Tab '{handle}' nicht schließen: {e}"
                            )
                self.driver.switch_to.window(main_window_handle)  # Zurück zum Haupt-Tab
                time.sleep(1)  # Kurze Pause nach dem Schließen
        except Exception as e:
            print(f"FEHLER: Probleme beim Verwalten von Browser-Fenstern: {e}")

    def stream_episode(self, url: str) -> str:
        """
        Navigiert zur URL, versucht das Video abzuspielen, handhabt Pop-ups und extrahiert
        fortlaufend Segment-URLs aus den Netzwerk-Logs.
        """
        self.url = url
        episode_title = "Unbekannter_Video_Titel"

        try:
            print(f"Navigiere zu URL: {self.url}")
            self.driver.get(self.url)
            main_window_handle = self.driver.current_window_handle
            wait = WebDriverWait(self.driver, 25)  # Längere Wartezeit auf Elemente

            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            print("Seite geladen.")

            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            episode_title = self.get_episode_title(soup)
            print(f"Erkannter Episodentitel: {episode_title}")

            self.handle_popups_and_focus(main_window_handle)

            video_element = None
            try:
                video_element = wait.until(
                    EC.presence_of_element_located((By.TAG_NAME, "video"))
                )
                print("Videoelement auf der Seite gefunden.")
            except TimeoutException:
                print(
                    "WARNUNG: Kein <video>-Element nach 25 Sekunden gefunden. Versuche, URLs trotzdem zu erfassen..."
                )
                print("Warte 30 Sekunden und versuche URL-Extraktion...")
                time.sleep(30)
                self.extract_segment_urls_from_performance_logs()
                return episode_title

            if video_element:
                self.click_video_element(video_element)

                self.handle_popups_and_focus(main_window_handle)

                try:
                    self.driver.execute_script(
                        "arguments[0].muted = true;", video_element
                    )
                    self.driver.execute_script("arguments[0].play();", video_element)
                    print("Video wird abgespielt und stummgeschaltet.")
                except Exception as e:
                    print(
                        f"WARNUNG: Fehler beim Abspielen/Stummschalten des Videos: {e}"
                    )

                video_duration = None
                for attempt_duration in range(5):
                    try:
                        video_duration = self.driver.execute_script(
                            "return arguments[0].duration;", video_element
                        )
                        if (
                            video_duration
                            and video_duration > 0
                            and not isinstance(video_duration, type(None))
                        ):
                            print(
                                f"Video-Dauer gefunden: {video_duration:.2f} Sekunden."
                            )
                            break
                    except Exception:
                        pass
                    time.sleep(3)

                if (
                    not video_duration
                    or video_duration == 0
                    or isinstance(video_duration, type(None))
                ):
                    print(
                        "WARNUNG: Konnte Video-Dauer nicht bestimmen. Möglicherweise ein Live-Stream oder Problem mit den Metadaten. Überwache den Stream für eine bestimmte Zeit."
                    )
                    duration_to_monitor = 120
                    start_time = time.time()
                    while (time.time() - start_time) < duration_to_monitor:
                        self.handle_popups_and_focus(main_window_handle)
                        self.extract_segment_urls_from_performance_logs()
                        print(
                            f"Überwache Stream... ({int(time.time() - start_time)}/{duration_to_monitor}s)"
                        )
                        time.sleep(10)
                    print("Überwachung des Streams beendet.")
                else:
                    print(
                        f"Starte Überwachung der Episode: {episode_title} (Dauer: {video_duration:.2f}s)"
                    )
                    last_current_time = 0
                    stalled_counter = 0

                    while True:
                        self.handle_popups_and_focus(main_window_handle)

                        try:
                            current_time = self.driver.execute_script(
                                "return arguments[0].currentTime;", video_element
                            )
                            is_paused = self.driver.execute_script(
                                "return arguments[0].paused;", video_element
                            )

                            if current_time is None or is_paused:
                                print(
                                    f"WARNUNG: Video ist pausiert oder aktueller Zeitpunkt nicht verfügbar. Versuch {stalled_counter+1}/5, zu reaktivieren..."
                                )
                                try:
                                    self.driver.execute_script(
                                        "arguments[0].play();", video_element
                                    )
                                except Exception as e_play:
                                    print(f"Fehler beim erneuten Abspielen: {e_play}")
                                stalled_counter += 1
                                time.sleep(5)
                                if stalled_counter >= 5:
                                    print(
                                        "WARNUNG: Video scheint festzuhängen oder pausiert. Beende Überwachung."
                                    )
                                    break
                                continue
                            else:
                                stalled_counter = 0

                            if current_time >= video_duration - 1.0:
                                print("Video ist beendet oder fast beendet.")
                                break

                            if current_time > last_current_time:
                                last_current_time = current_time

                            remaining_time = video_duration - current_time
                            print(
                                f"Video läuft. Aktuell: {current_time:.2f}s von {video_duration:.2f}s. Verbleibend: {remaining_time:.2f}s"
                            )

                            self.extract_segment_urls_from_performance_logs()
                            time.sleep(10)

                        except KeyboardInterrupt:
                            print(
                                "Manuelle Unterbrechung der Wiedergabe durch Benutzer."
                            )
                            break
                        except WebDriverException as e:
                            print(
                                f"FEHLER: WebDriver Fehler während des Abspielens: {e}. Das Videoelement wurde möglicherweise detached oder die Seite neu geladen."
                            )
                            break
                        except Exception as e:
                            print(f"Unerwarteter Fehler während des Abspielens: {e}")
                            break

                self.extract_segment_urls_from_performance_logs()  # Final attempt to extract URLs

            return episode_title

        except Exception as e:
            print(
                f"KRITISCHER FEHLER: Ein Problem ist beim Streamen des Videos aufgetreten: {e}"
            )
            return episode_title

    def extract_segment_urls_from_performance_logs(self):
        """
        Extrahiert URLs von Video-Ressourcen aus den Browser-Performance-Logs.
        Fügt nur neue und einzigartige URLs hinzu, die auf Videosegmente oder Playlists hinweisen.
        """
        try:
            logs = self.driver.execute_script(
                "return window.performance.getEntriesByType('resource');"
            )
            initial_count = len(self.segment_urls)
            for log in logs:
                url = log.get("name", "")
                # Filtere nach gängigen Segment- oder Playlist-Patterns
                if (
                    ".ts" in url
                    or ".m4s" in url
                    or "seg-" in url
                    or ".mpd" in url
                    or ".m3u8" in url
                ) and url not in self.segment_urls:
                    self.segment_urls.append(url)

            if len(self.segment_urls) > initial_count:
                print(
                    f"Neue Segment/Playlist-URLs gefunden. Gesamt: {len(self.segment_urls)}"
                )

        except Exception as e:
            print(
                f"FEHLER: Beim Extrahieren der Segment-URLs aus Performance-Logs: {e}"
            )
        return self.segment_urls

    def clean_filename(self, filename: str) -> str:
        """Reinigt einen String, um ihn als gültigen Dateinamen zu verwenden."""
        # Replace invalid characters with underscores
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        # Remove leading/trailing spaces, replace internal spaces with underscores
        filename = filename.strip().replace(" ", "_")
        # Ensure it doesn't end with .mp4 if it's just a title cleanup
        if filename.lower().endswith(".mp4"):
            filename = filename[:-4]
        return filename

    def download_segments(self):
        """Lädt die identifizierten Videosegmente herunter."""
        if not self.segment_urls:
            print("KEINE SEGMENT-URLS GEFUNDEN. Download der Segmente nicht möglich.")
            return

        # Create a unique temporary directory for this download
        folder_suffix = 0
        while True:
            temp_dir = os.path.join(self.output_dir_segments_base, str(folder_suffix))
            if not os.path.exists(temp_dir):
                os.makedirs(temp_dir)
                self.current_segment_dir = temp_dir
                break
            folder_suffix += 1

        print(f"Temporärer Ordner für Segmente: {self.current_segment_dir}")

        print(f"Starte Download von {len(self.segment_urls)} Segmenten...")
        for i, url in enumerate(self.segment_urls):
            # Determine file extension more robustly, focusing on actual segment types
            file_extension = ".ts"  # Default for HLS
            if ".m4s" in url.lower():
                file_extension = ".m4s"
            elif ".mpd" in url.lower():
                file_extension = (
                    ".mpd"  # This is a manifest, usually not downloaded as a segment
                )
            elif ".m3u8" in url.lower():
                file_extension = (
                    ".m3u8"  # This is a playlist, usually not downloaded as a segment
                )

            # If a segment URL has a clear extension before query parameters
            # e.g., "segment_123.mp4?key=value"
            match = re.search(r"\.(\w{2,5})(?:\?|/|$)", url.lower())
            if match:
                detected_ext = match.group(1)
                if detected_ext in ["ts", "m4s", "mp4"]:
                    file_extension = f".{detected_ext}"

            segment_filename = (
                f"segment_{i + 1:04d}{file_extension}"  # Padded with zeros for sorting
            )
            print(f"Lade Segment {i+1}/{len(self.segment_urls)}: {url}")
            self.download_file_to_dir(url, self.current_segment_dir, segment_filename)
        print("Alle Segmente heruntergeladen.")

    def download_ffmpeg_if_needed(self):
        """Prüft, ob FFmpeg vorhanden ist, lädt es herunter und entpackt es, falls nicht."""
        if os.path.exists(self.ffmpeg_path):
            print("FFmpeg ist bereits vorhanden.")
            return

        print("FFmpeg nicht gefunden. Starte Download und Entpacken...")
        # Use a more stable FFmpeg release URL if available, or the latest
        ffmpeg_zip_url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        ffmpeg_zip_filename = os.path.join(self.ffmpeg_base_dir, "ffmpeg.zip")

        os.makedirs(self.ffmpeg_base_dir, exist_ok=True)

        try:
            print(f"Lade FFmpeg ZIP von {ffmpeg_zip_url} herunter...")
            response = requests.get(ffmpeg_zip_url, stream=True, timeout=600)
            response.raise_for_status()
            with open(ffmpeg_zip_filename, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"FFmpeg ZIP erfolgreich heruntergeladen nach: {ffmpeg_zip_filename}")

            print(
                f"Entpacke FFmpeg aus {ffmpeg_zip_filename} nach {self.ffmpeg_base_dir}..."
            )
            with zipfile.ZipFile(ffmpeg_zip_filename, "r") as zip_ref:
                # Find the base directory inside the zip, e.g., "ffmpeg-master-latest-win64-gpl/"
                # and then target the 'bin' folder within it.
                # This ensures we extract correctly regardless of the exact top-level folder name in the zip.
                for member in zip_ref.namelist():
                    if member.endswith("/bin/"):  # Find the bin directory
                        zip_bin_path = member
                        break
                else:  # if loop completes without break, bin path not found
                    raise Exception("FFmpeg 'bin' directory not found in the zip file.")

                for member in zip_ref.namelist():
                    # Only extract files from the found 'bin' directory
                    if member.startswith(zip_bin_path) and not member.endswith("/"):
                        target_filename = os.path.basename(member)
                        target_path = os.path.join(
                            self.ffmpeg_base_dir, "bin", target_filename
                        )
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)

                        with zip_ref.open(member) as source, open(
                            target_path, "wb"
                        ) as target:
                            target.write(source.read())
            print(
                f"FFmpeg 'bin'-Ordner erfolgreich entpackt nach: {os.path.join(self.ffmpeg_base_dir, 'bin')}"
            )

        except requests.exceptions.RequestException as e:
            print(f"FEHLER: Beim Herunterladen von FFmpeg: {e}")
            sys.exit(1)
        except zipfile.BadZipFile:
            print("FEHLER: Heruntergeladene FFmpeg-Datei ist eine ungültige ZIP-Datei.")
            sys.exit(1)
        except Exception as e:
            print(
                f"FEHLER: Ein unerwarteter Fehler beim Download/Entpacken von FFmpeg: {e}"
            )
            sys.exit(1)

    def merge_segments_to_mp4(self, episode_title: str):
        """
        Fügt die heruntergeladenen Segmente mit FFmpeg zu einer MP4-Datei zusammen.
        """
        if not self.current_segment_dir or not os.path.exists(self.current_segment_dir):
            print(
                "Kein Verzeichnis mit heruntergeladenen Segmenten gefunden. Nichts zum Zusammenfügen."
            )
            return

        self.download_ffmpeg_if_needed()  # Sicherstellen, dass FFmpeg da ist

        cleaned_title = self.clean_filename(episode_title)
        output_file = os.path.join(self.download_target_path, f"{cleaned_title}.mp4")

        # Liste aller relevanten Segment-Dateien im aktuellen Segment-Verzeichnis
        segment_files = [
            f
            for f in os.listdir(self.current_segment_dir)
            if f.lower().endswith((".ts", ".m4s", ".mp4"))
        ]
        if not segment_files:
            print(
                f"WARNUNG: Keine Segment-Dateien (z.B. .ts, .m4s, .mp4) im Verzeichnis {self.current_segment_dir} zum Zusammenfügen gefunden."
            )
            return

        def numerical_sort(filename):
            parts = re.findall(r"[0-9]+", filename)
            return int(parts[0]) if parts else filename

        segment_files.sort(key=numerical_sort)

        # Create a concatenation list for FFmpeg
        concat_list_path = os.path.join(self.current_segment_dir, "concat_list.txt")
        # FFmpeg on Windows prefers forward slashes or escaped backslashes in concat files
        # Also, use absolute paths to avoid issues with FFmpeg's current working directory
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for seg_file in segment_files:
                # Use absolute path and ensure forward slashes for FFmpeg compatibility
                full_path = os.path.join(self.current_segment_dir, seg_file).replace(
                    os.sep, "/"
                )
                f.write(f"file '{full_path}'\n")

        print(f"Starte Zusammenführung der Segmente in {output_file} mit FFmpeg...")
        try:
            ffmpeg_command = [
                self.ffmpeg_path,
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_list_path,
                "-c:v",
                "copy",  # <--- Here: Copy video stream as is
                "-c:a",
                "copy",  # <--- Here: Copy audio stream as is
                "-bsf:a",
                "aac_adtstoasc",
                "-map_metadata",
                "-1",
                "-y",  # Overwrite output file without asking
                output_file,
            ]

            print("Ausgeführter FFmpeg-Befehl:", " ".join(ffmpeg_command))
            process = subprocess.run(
                ffmpeg_command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            print(f"ERFOLG: Video erfolgreich zusammengefügt: {output_file}")
            print("\n--- FFmpeg Standardausgabe ---")
            print(process.stdout)
            if process.stderr:
                print("\n--- FFmpeg Fehler-Ausgabe ---")
                print(process.stderr)

        except subprocess.CalledProcessError as e:
            print(
                f"FEHLER: FFmpeg-Befehl ist fehlgeschlagen mit Exit-Code {e.returncode}."
            )
            print("--- FFmpeg Standardausgabe ---")
            print(e.stdout)
            print("--- FFmpeg Fehler-Ausgabe ---")
            print(e.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"FEHLER: Ein unerwarteter Fehler beim Zusammenfügen mit FFmpeg: {e}")
            sys.exit(1)


class RunDownload:
    def __init__(self, url: str, download_path: str):
        self.url = url
        # Use the provided download_path directly for the final output
        self.download_path = os.path.abspath(download_path)
        self.md = ManageDownload(self.download_path)

    def start(self):
        try:
            self.episoden_title = self.md.stream_episode(self.url)
            self.md.download_segments()
            self.md.merge_segments_to_mp4(self.episoden_title)
        except Exception as e:
            print(
                f"Ein Fehler ist während des Download- und Merge-Prozesses aufgetreten: {e}"
            )
        finally:
            self.md.quit_driver()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Fehler: Bitte gib 2 Argumente an: URL und Zielordner.")
        print(
            'Beispiel: python your_script_name.py "https://example.com/video" "D:\\Coding\\Python\\SerienDownloader\\Folgen"'
        )
        sys.exit(1)

    url = sys.argv[1]
    download_path = sys.argv[
        2
    ]  # This will be "D:\Coding\Python\SerienDownloader\Folgen"

    run_instance = RunDownload(url, download_path)
    run_instance.start()
