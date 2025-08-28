import os
import sys
import json
import logging
import asyncio # Für asynchrone Programmierung
import aiohttp # Für asynchrone HTTP-Anfragen
from bs4 import BeautifulSoup
from lxml import etree # Import für XPath-Unterstützung
import time
import re # Für reguläre Ausdrücke zur Staffelnummer-Extraktion
from typing import Union # Hinzugefügt für Union-Typ-Hinweis

os.makedirs("app/storage/Log", exist_ok=True)

# --- Globale Konfigurationen und Konstanten ---
# Konfigurieren des Loggings
logging.basicConfig(
    level=logging.INFO, # Setzt den Logging-Level auf INFO für übersichtliche Ausgaben. Ändern Sie dies zu logging.DEBUG, um aktive Anfragen zu sehen.
    format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s',
    handlers=[
    logging.FileHandler("app/storage/Log/series_scraper.log"), # Protokolliert in eine Datei
        logging.StreamHandler(sys.stdout)          # Protokolliert auf die Konsole
    ]
)

# Maximale Anzahl gleichzeitiger Anfragen für das Abrufen von Episoden-Links (mit aiohttp)
EPISODE_MAX_CONCURRENT_REQUESTS = 10000 # Erhöht für schnellere Verarbeitung, basierend auf Ihrer Rückmeldung
# Basis-URL für die Serie
BASE_URL = "https://186.2.175.5"

# --- Globale Statistik-Variablen ---
# Diese werden in main_async zurückgesetzt und aggregiert
global_stats = {
    "total_series_processed_successfully": 0,
    "total_series_skipped": 0,
    "total_series_failed": 0,
    "failed_items_details": [] # Speichert Details zu Fehlern (Serie, Staffel, Episode, Film, Fehlertyp)
}

# Semaphore zur Begrenzung der gleichzeitigen Anfragen
request_semaphore = asyncio.Semaphore(EPISODE_MAX_CONCURRENT_REQUESTS)
# Zähler für aktive Anfragen
active_requests_counter = 0

# --- Hilfsfunktionen für Dateiverwaltung ---

os.makedirs("app/storage/Serien", exist_ok=True)

def read_series_txt():
    """
    Liest die Seriennamen aus der Datei 'seriesNames.txt' und gibt sie als Liste zurück.

    Returns:
        list: Eine Liste von seriennamen.
    """
    try:
        with open('./seriesNames.txt', 'r', encoding='utf-8') as file:
            series = [line.strip() for line in file if line.strip()]
        logging.info(f"Erfolgreich {len(series)} Seriennamen aus 'seriesNames.txt' gelesen.")
        return series
    except FileNotFoundError:
        logging.error("Die Datei 'seriesNames.txt' wurde nicht gefunden.")
        return []
    except Exception as e:
        logging.error(f"Fehler beim Lesen von 'seriesNames.txt': {e}")
        return []

def write_json_file(data: list, filename: str):
    """
    Schreibt die Daten in eine JSON-Datei.

    Args:
        data (list): Die zu schreibenden Daten.
        filename (str): Der Name der JSON-Datei.
    """
    try:
        with open(f'app/storage/Serien/{filename}', 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4)
        logging.info(f"Daten erfolgreich in {filename} geschrieben.")
    except Exception as e:
        logging.error(f"Fehler beim Schreiben der Daten in {filename}: {e}")
    
def load_existing_series_data(filename='all_series_data.json'):
    """
    Lädt vorhandene Seriendaten aus einer JSON-Datei.

    Args:
        filename (str): Der Name der JSON-Datei.

    Returns:
        list: Eine Liste von Seriendaten, oder eine leere Liste, wenn die Datei nicht existiert oder ungültig ist.
    """
    zentraler_pfad = f'app/storage/Serien/{filename}'
    if os.path.exists(zentraler_pfad):
        try:
            with open(zentraler_pfad, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, list):
                    logging.info(f"Bestehende Daten erfolgreich aus {filename} geladen.")
                    return data
                else:
                    logging.warning(f"Bestehende Datei {filename} ist keine Liste. Beginne neu.")
                    return []
        except json.JSONDecodeError as e:
            logging.error(f"Fehler beim Dekodieren von JSON aus {filename}: {e}. Beginne neu.")
            return []
        except Exception as e:
            logging.error(f"Fehler beim Laden bestehender Daten aus {filename}: {e}. Beginne neu.")
            return []
    else:
        logging.info(f"Datei {filename} existiert nicht. Beginne mit leeren Daten.")
        return []

# --- Hilfsfunktionen für Web-Scraping ---

def find_by_xpath_lxml(soup_obj: BeautifulSoup, xpath_expr: str):
    """
    Findet Elemente in einem BeautifulSoup-Objekt mithilfe eines XPath-Ausdrucks.
    Verwendet lxml für die XPath-Verarbeitung.

    Args:
        soup_obj (BeautifulSoup): Das BeautifulSoup-Objekt, das den HTML-Inhalt enthält.
        xpath_expr (str): Der XPath-Ausdruck.

    Returns:
        list: Eine Liste von BeautifulSoup-Tag-Objekten, die dem XPath entsprechen.
    """
    try:
        # Konvertiere BeautifulSoup-Objekt in ein lxml-Element für XPath-Abfragen
        # etree.HTML erfordert einen String, also konvertieren wir soup_obj zurück in einen String
        lxml_tree = etree.HTML(str(soup_obj))
        found_elements = lxml_tree.xpath(xpath_expr)
        
        # Konvertiere lxml-Elemente zurück in BeautifulSoup-Tags
        bs_elements = []
        for el in found_elements:
            # etree.tostring gibt Bytes zurück, decode zu String
            # Dann parse den String mit BeautifulSoup und finde das erste Tag
            bs_el = BeautifulSoup(etree.tostring(el, pretty_print=True).decode(), "lxml").find()
            if bs_el:
                bs_elements.append(bs_el)
        return bs_elements
    except Exception as e:
        logging.error(f"Fehler bei der XPath-Suche mit lxml ('{xpath_expr}'): {e}", exc_info=True)
        return []

async def get_series_structure_async(session: aiohttp.ClientSession, url: str, serie_name: str):
    """
    Ermittelt die Struktur einer Serie (Staffeln und/oder Filme) basierend auf der URL.
    Diese Funktion verwendet aiohttp, um den HTML-Inhalt abzurufen und lxml für XPath-Abfragen.

    Args:
        session (aiohttp.ClientSession): Die aiohttp Client-Session.
        url (str): Die URL der Seite (z.B. erste Episode einer Serie).
        serie_name (str): Der Name der Serie für Logging und Fehlerdetails.

    Returns:
        list: Eine Liste von Dictionaries, die die Struktur der Serie beschreiben.
              Beispiel: [{'type': 'season', 'number': 1}, {'type': 'movie_collection', 'url_suffix': '/filme'}]
    """
    structure_items = []
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
            response.raise_for_status() # Löst eine Ausnahme für HTTP-Fehler (4xx oder 5xx) aus
            html_content = await response.text()
        
        soup = BeautifulSoup(html_content, "lxml") # Wichtig: lxml-Parser verwenden

        # XPath für Staffeln und Filme in der Navigationsleiste
        # Dies sollte alle li-Elemente unter dem ersten ul im #stream-Div erfassen
        target_xpath = '//*[@id="stream"]/ul[1]/li' 
        logging.debug(f"Suche Staffeln/Filme mit XPath: '{target_xpath}' auf {url} für Serie {serie_name}.")
        all_li_elements = find_by_xpath_lxml(soup, target_xpath)

        if not all_li_elements:
            logging.warning(f"Keine li-Elemente für Staffeln/Filme mit XPath '{target_xpath}' gefunden auf {url} für Serie {serie_name}.")
            global_stats["failed_items_details"].append({
                "type": "series_structure_xpath_not_found",
                "series": serie_name,
                "url": url,
                "xpath": target_xpath,
                "error": "Keine Staffeln/Filme-li-Elemente gefunden."
            })
            return []

        for li in all_li_elements:
            a_tag = li.find('a')
            if a_tag and a_tag.get('href'):
                href = a_tag.get('href')
                if "/staffel-" in href:
                    # Versuchen, die Staffelnummer aus dem Text oder dem href zu extrahieren
                    season_text = a_tag.get_text(strip=True)
                    season_number = None
                    if season_text.isdigit():
                        season_number = int(season_text)
                    else:
                        match = re.search(r'/staffel-(\d+)', href)
                        if match:
                            season_number = int(match.group(1))
                    
                    if season_number is not None:
                        structure_items.append({'type': 'season', 'number': season_number})
                        logging.debug(f"Gefunden: Staffel {season_number} für Serie {serie_name}.")
                elif "/filme" in href:
                    structure_items.append({'type': 'movie_collection', 'url_suffix': href})
                    logging.info(f"Gefunden: 'Filme'-Eintrag für Serie {serie_name} auf {url}.")
            elif li.find('span', string='Staffeln:'): # Ignoriere das "Staffeln:"-Element
                logging.debug(f"Ignoriere 'Staffeln:'-Text-Element in Staffelliste für Serie {serie_name}.")
                continue
            else:
                logging.debug(f"Ungültiges/unerwartetes Strukturelement gefunden: {li.prettify()}")
        
        # Sortiere die Staffeln nach ihrer Nummer, um eine konsistente Reihenfolge zu gewährleisten
        # Filme bleiben an ihrer gefundenen Position relativ zu den Staffeln
        structure_items.sort(key=lambda x: x['number'] if x['type'] == 'season' else float('inf'))

        return structure_items

    except aiohttp.ClientError as e:
        error_type = "network_error"
        error_msg = f"FEHLER beim Abrufen der URL {url} für Serienstruktur: {e}"
        logging.error(error_msg)
        global_stats["failed_items_details"].append({
            "type": error_type,
            "series": serie_name,
            "url": url,
            "error": str(e)
        })
        return []
    except asyncio.TimeoutError:
        error_type = "timeout_error"
        error_msg = f"Timeout beim Abrufen der URL {url} für Serienstruktur."
        logging.error(error_msg)
        global_stats["failed_items_details"].append({
            "type": error_type,
            "series": serie_name,
            "url": url,
            "error": "Timeout"
        })
        return []
    except Exception as e:
        error_type = "parsing_error"
        error_msg = f"FEHLER beim Parsen der URL {url} für Serienstruktur: {e}"
        logging.error(error_msg, exc_info=True)
        global_stats["failed_items_details"].append({
            "type": error_type,
            "series": serie_name,
            "url": url,
            "error": str(e)
        })
        return []

async def get_raw_episode_count_async(session: aiohttp.ClientSession, url: str, serie_name: str, season_num: int):
    """
    Ermittelt die rohe Anzahl der li-Elemente für Episoden.

    Args:
        session (aiohttp.ClientSession): Die aiohttp Client-Session.
        url (str): Die URL der Seite.
        serie_name (str): Der Name der Serie für Logging und Fehlerdetails.
        season_num (int): Die Staffelnummer.

    Returns:
        int: Die rohe Anzahl der li-Elemente.
    """
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
            response.raise_for_status()
            html_content = await response.text()
        
        soup = BeautifulSoup(html_content, "lxml")

        # XPath für Episoden, um direkt die li-Elemente zu zählen
        target_xpath = '//*[@id="stream"]/ul[2]/li' 
        logging.debug(f"Suche Episoden mit XPath: '{target_xpath}' auf {url} für Serie {serie_name}, Staffel {season_num}.")
        all_li_elements = find_by_xpath_lxml(soup, target_xpath)

        valid_episode_count = 0
        for li in all_li_elements:
            a_tag = li.find('a')
            # Zähle nur li-Elemente, die einen Link zu einer Episode enthalten
            # Der Link sollte die aktuelle Staffelnummer und eine Episodennummer enthalten
            if a_tag and a_tag.get('href') and f"/staffel-{season_num}/episode-" in a_tag.get('href'):
                # Überprüfe, ob der Text des a-Tags eine Zahl ist oder der href eine Episodennummer enthält
                episode_text = a_tag.get_text(strip=True)
                if episode_text.isdigit() or re.search(r'/episode-(\d+)', a_tag.get('href')):
                    valid_episode_count += 1
                else:
                    logging.debug(f"Ignoriere nicht-numerisches Episoden-Element oder ungültigen href: {li.prettify()}")
            else:
                logging.debug(f"Ignoriere li-Element ohne gültigen Episoden-Link: {li.prettify()}")

        if valid_episode_count == 0:
            logging.warning(f"Keine gültigen li-Elemente für Episoden mit XPath '{target_xpath}' gefunden auf {url} für Serie {serie_name}, Staffel {season_num}.")
            global_stats["failed_items_details"].append({
                "type": "episode_count_xpath_not_found",
                "series": serie_name,
                "season": season_num,
                "url": url,
                "xpath": target_xpath,
                "error": "Keine gültigen Episoden-li-Elemente gefunden."
            })
        
        count = valid_episode_count
        logging.debug(f"Gefunden {count} gültige Episodenelemente für Serie {serie_name}, Staffel {season_num} auf {url}.")
        return count
    except aiohttp.ClientError as e:
        error_type = "network_error"
        error_msg = f"FEHLER beim Abrufen der URL {url} für Episoden-Zählung: {e}"
        logging.error(error_msg)
        global_stats["failed_items_details"].append({
            "type": error_type,
            "series": serie_name,
            "season": season_num,
            "url": url,
            "error": str(e)
        })
        return 0
    except asyncio.TimeoutError:
        error_type = "timeout_error"
        error_msg = f"Timeout beim Abrufen der URL {url} für Episoden-Zählung."
        logging.error(error_msg)
        global_stats["failed_items_details"].append({
            "type": error_type,
            "series": serie_name,
            "season": season_num,
            "url": url,
            "error": "Timeout"
        })
        return 0
    except Exception as e:
        error_type = "parsing_error"
        error_msg = f"FEHLER beim Parsen der URL {url} für Episoden-Zählung: {e}"
        logging.error(error_msg, exc_info=True)
        global_stats["failed_items_details"].append({
            "type": error_type,
            "series": serie_name,
            "season": season_num,
            "url": url,
            "error": str(e)
        })
        return 0

async def fetch_stream_links_async(session: aiohttp.ClientSession, url: str, serie_name: str, item_type: str, item_identifier: Union[str, int]):
    """
    Sucht nach verfügbaren Streaming-Diensten für eine TV-Serie Episode oder einen Film mit aiohttp.
    Priorisiert VOE als primären Link, dann Vidoza.
    Verwendet ein Semaphor, um die Anzahl der gleichzeitigen Anfragen zu begrenzen.

    Args:
        session (aiohttp.ClientSession): Die aiohttp Client-Session.
        url (str): Die URL der Episode oder des Films.
        serie_name (str): Der Name der Serie für Logging und Fehlerdetails.
        item_type (str): 'episode' oder 'movie'.
        item_identifier (Union[str, int]): Die Episodennummer oder der Filmtitel/Nummer.

    Returns:
        dict: Ein Dictionary mit 'primary_link', 'vidoza_link' und 'voe_link' (oder None).
    """
    global active_requests_counter
    primary_link = None
    vidoza_link = None
    voe_link = None
    
    async with request_semaphore: # Erwerbe das Semaphor vor der Anfrage
        active_requests_counter += 1
        logging.debug(f"Aktive Anfragen: {active_requests_counter}/{EPISODE_MAX_CONCURRENT_REQUESTS} - Starte Abruf von {url}")
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status() # Löst eine Ausnahme für HTTP-Fehler (4xx oder 5xx) aus
                html_content = await response.text()
            
            soup = BeautifulSoup(html_content, "lxml") # Wichtig: lxml-Parser verwenden

            elements = soup.find_all("i", class_="icon")

            all_stream_services = []
            for element in elements:
                class_value = element.get("class")
                if class_value and len(class_value) > 1:
                    service_name = class_value[1]
                    link_element = element.find_parent("a")
                    if link_element:
                        href = link_element.get("href")
                        if href:
                            full_href = f'{BASE_URL}{href}'
                            all_stream_services.append({"name": service_name, "href_link": full_href})
            
            # Priorisiere VOE als primären Link, dann Vidoza
            for service in all_stream_services:
                if "VOE" in service["name"]:
                    voe_link = service["href_link"]
                    if primary_link is None:
                        primary_link = voe_link
                elif "Vidoza" in service["name"]: 
                    vidoza_link = service["href_link"]
                    if primary_link is None:
                        primary_link = vidoza_link
            
            if primary_link is None:
                logging.debug(f"Kein bevorzugter Streaming-Dienst (VOE oder Vidoza) für {item_type} {item_identifier} von {serie_name} unter {url} gefunden.")
                # Füge Fehlerdetails hinzu, wenn keine Links gefunden wurden
                global_stats["failed_items_details"].append({
                    "type": f"no_stream_links_found_{item_type}",
                    "series": serie_name,
                    "item_type": item_type,
                    "item_identifier": item_identifier,
                    "url": url,
                    "error": "Keine bevorzugten Streaming-Links (VOE/Vidoza) gefunden"
                })

            return {"primary_link": primary_link, "vidoza_link": vidoza_link, "voe_link": voe_link}
        except aiohttp.ClientError as e:
            error_type = "network_error"
            error_msg = f"FEHLER beim Abrufen von Streaming-Diensten mit aiohttp unter {url} für {item_type} {item_identifier}: {e}"
            logging.error(error_msg)
            global_stats["failed_items_details"].append({
                "type": error_type,
                "series": serie_name,
                "item_type": item_type,
                "item_identifier": item_identifier,
                "url": url,
                "error": str(e)
            })
            return {"primary_link": None, "vidoza_link": None, "voe_link": None}
        except asyncio.TimeoutError:
            error_type = "timeout_error"
            error_msg = f"Timeout beim Abrufen von Streaming-Diensten unter {url} für {item_type} {item_identifier}."
            logging.error(error_msg)
            global_stats["failed_items_details"].append({
                "type": error_type,
                "series": serie_name,
                "item_type": item_type,
                "item_identifier": item_identifier,
                "url": url,
                "error": "Timeout"
            })
            return {"primary_link": None, "vidoza_link": None, "voe_link": None}
        except Exception as e:
            error_type = "parsing_error"
            error_msg = f"FEHLER beim Parsen von Streaming-Diensten unter {url} für {item_type} {item_identifier}: {e}"
            logging.error(error_msg, exc_info=True)
            global_stats["failed_items_details"].append({
                "type": error_type,
                "series": serie_name,
                "item_type": item_type,
                "item_identifier": item_identifier,
                "url": url,
                "error": str(e)
            })
            return {"primary_link": None, "vidoza_link": None, "voe_link": None}
        finally:
            active_requests_counter -= 1
            logging.debug(f"Aktive Anfragen: {active_requests_counter}/{EPISODE_MAX_CONCURRENT_REQUESTS} - Abruf von {url} beendet.")


async def get_episode_url_per_season(serien_Name: str, season: int, current_series_index: int, total_series_count: int, existing_episode_links: list):
    """
    Sammelt alle Episode-Links für eine bestimmte Staffel einer TV-Serie,
    wobei das Suchen der Links für jede Episode parallel erfolgt.
    Berücksichtigt bereits vorhandene Episodenlinks.

    Args:
        serien_Name (str): Der Name der Serie.
        season (int): Die Staffelnummer.
        current_series_index (int): Der aktuelle Index der Serie (1-basiert).
        total_series_count (int): Die Gesamtanzahl der zu verarbeitenden Serien.
        existing_episode_links (list): Bereits vorhandene Episodenlinks für diese Staffel.

    Returns:
        list: Eine Liste von Episode-Link-Dictionaries (bestehende + neu gefundene).
    """
    
    initial_episode_url = f"{BASE_URL}/serie/stream/{serien_Name}/staffel-{season}/episode-1"
    
    # Verwende aiohttp.ClientSession für effizientes Connection Pooling
    # SSL-Verifizierung deaktiviert
    connector = aiohttp.TCPConnector(limit=EPISODE_MAX_CONCURRENT_REQUESTS, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Bestimme die rohe Gesamtanzahl der Episoden für diese Staffel
        raw_episode_count = await get_raw_episode_count_async(session, initial_episode_url, serien_Name, season)
        # Wenden Sie die -1 Anpassung hier an, wie vom Benutzer gewünscht
        total_episodes = max(0, raw_episode_count - 1)
        
        if total_episodes == 0:
            logging.warning(f"Keine Episoden für {serien_Name}, Staffel {season} gefunden. Überspringe.")
            global_stats["failed_items_details"].append({
                "type": "season_no_episodes_found",
                "series": serien_Name,
                "season": season,
                "url": initial_episode_url,
                "error": "Keine Episoden gefunden oder XPath falsch"
            })
            return existing_episode_links # Gebe vorhandene Links zurück, wenn keine neuen gefunden werden

        logging.info(f"Starte Abruf von {total_episodes} Episoden für Staffel {season} von {serien_Name} (Serie {current_series_index}/{total_series_count}).")
        
        tasks = []
        # Erstelle ein Set der bereits vorhandenen Episodennummern für schnelle Überprüfung
        existing_episode_numbers = {ep.get('episode_number') for ep in existing_episode_links if isinstance(ep, dict) and 'episode_number' in ep}

        for episode in range(1, total_episodes + 1):
            if episode in existing_episode_numbers:
                logging.debug(f"Episode {episode} von Staffel {season} für {serien_Name} bereits vorhanden. Überspringe Abruf.")
                continue # Überspringe, wenn Episode bereits vorhanden ist

            url = f"{BASE_URL}/serie/stream/{serien_Name}/staffel-{season}/episode-{episode}"
            tasks.append(fetch_stream_links_async(session, url, serien_Name, 'episode', episode))
        
        if not tasks: # Wenn alle Episoden bereits vorhanden waren
            logging.info(f"Alle Episoden für Staffel {season} von {serien_Name} bereits vorhanden. Keine neuen Abrufe.")
            return existing_episode_links

        all_episode_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        successful_fetches = 0
        failed_fetches = 0
        temp_episode_results = []
        
        for i, result in enumerate(all_episode_results):
            # Versuche, die tatsächliche Episodennummer zu verwenden, sonst den Index
            episode_num = (result.get('episode_number') if isinstance(result, dict) else None) or (i + 1)
            
            if isinstance(result, Exception):
                logging.error(f"Fehler bei Episode {episode_num} von Staffel {season} für {serien_Name}: {result}")
                failed_fetches += 1
                # Fehlerdetails werden bereits in fetch_stream_links_async hinzugefügt
            elif result and (result["primary_link"] or result["vidoza_link"] or result["voe_link"]):
                # Füge die episode_number hinzu, die wir in der JSON-Struktur benötigen
                result['episode_number'] = episode_num # Stellen Sie sicher, dass die Episodennummer im Ergebnis enthalten ist
                temp_episode_results.append(result)
                successful_fetches += 1
            else:
                # Dies sollte jetzt durch die Fehlerbehandlung in fetch_stream_links_async abgedeckt sein,
                # aber als Fallback, falls ein Ergebnis ohne Links zurückkommt, das keine Exception war.
                logging.warning(f"Episode {episode_num} von Staffel {season} für {serien_Name}: Keine Links gefunden (Fallback).")
                failed_fetches += 1 
                global_stats["failed_items_details"].append({
                    "type": "no_stream_links_found_fallback",
                    "series": serien_Name,
                    "season": season,
                    "episode": episode_num,
                    "url": f"{BASE_URL}/serie/stream/{serien_Name}/staffel-{season}/episode-{episode_num}",
                    "error": "Keine bevorzugten Streaming-Links (VOE/Vidoza) gefunden (Fallback)"
                })


        logging.info(f"Ergebnisse für Staffel {season} von {serien_Name}: Erfolgreich {successful_fetches}/{len(tasks)}, Fehlgeschlagen {failed_fetches}.")
        
        # Kombiniere bestehende und neu gefundene Episodenlinks
        combined_links = existing_episode_links + temp_episode_results
        # Die gesammelten Ergebnisse nach episode_number sortieren, um die korrekte Reihenfolge in JSON sicherzustellen
        links = sorted(combined_links, key=lambda x: x.get('episode_number', 0) if isinstance(x, dict) else 0)
            
    logging.info(f"Staffel {season} von {serien_Name} abgeschlossen.")
    
    # Aktualisiere globale Statistiken für Staffeln
    if successful_fetches > 0:
        pass # Wenn mindestens eine Episode gefunden wurde, betrachten wir die Staffel als teilweise erfolgreich
    else:
        # Wenn keine einzige Episode erfolgreich war, betrachten wir die Staffel als fehlgeschlagen
        global_stats["failed_items_details"].append({
            "type": "season_failed_all_episodes",
            "series": serien_Name,
            "season": season,
            "url": initial_episode_url,
            "error": f"Alle {total_episodes} Episoden in Staffel {season} fehlgeschlagen oder keine Links gefunden."
        })

    return links

async def get_movie_collection_details_async(serien_Name: str, movie_collection_url_suffix: str, current_series_index: int, total_series_count: int, existing_movies: list):
    """
    Sammelt Details und Links für einzelne Filme innerhalb einer Filmsammlung.

    Args:
        serien_Name (str): Der Name der Serie.
        movie_collection_url_suffix (str): Der URL-Suffix zur Filmsammlung (z.B. '/serie/stream/one-punch-man/filme').
        current_series_index (int): Der aktuelle Index der Serie (1-basiert).
        total_series_count (int): Die Gesamtanzahl der zu verarbeitenden Serien.
        existing_movies (list): Bereits vorhandene Filmlinks für diese Sammlung.

    Returns:
        list: Eine Liste von Film-Details.
    """
    
    full_movie_collection_url = f"{BASE_URL}{movie_collection_url_suffix}"

    # Verwende aiohttp.ClientSession für effizientes Connection Pooling
    connector = aiohttp.TCPConnector(limit=EPISODE_MAX_CONCURRENT_REQUESTS, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get(full_movie_collection_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status()
                html_content = await response.text()
            
            soup = BeautifulSoup(html_content, "lxml")

            # XPath für einzelne Filme innerhalb der Filmsammlung
            movie_xpath = '//*[@id="stream"]/ul[2]/li' 
            # Korrektur hier: Verwende movie_xpath statt target_xpath
            logging.debug(f"Suche einzelne Filme mit XPath: '{movie_xpath}' auf {full_movie_collection_url} für Serie {serien_Name}.")
            movie_li_elements = find_by_xpath_lxml(soup, movie_xpath)

            if not movie_li_elements:
                logging.warning(f"Keine li-Elemente für einzelne Filme mit XPath '{movie_xpath}' gefunden auf {full_movie_collection_url} für Serie {serien_Name}.")
                global_stats["failed_items_details"].append({
                    "type": "movie_collection_xpath_not_found",
                    "series": serien_Name,
                    "url": full_movie_collection_url,
                    "xpath": movie_xpath,
                    "error": "Keine Film-li-Elemente gefunden oder XPath falsch."
                })
                return [] # Gebe leere Liste zurück

            tasks = []
            valid_movies_to_process = [] # Liste zum Speichern von (movie_title, full_movie_url) für gültige Filme
            
            # Erstelle ein Set der bereits vorhandenen Filmtitel/URLs für schnelle Überprüfung
            existing_movie_identifiers = {m.get('movie_title') for m in existing_movies if isinstance(m, dict) and 'movie_title' in m}

            for li in movie_li_elements: # Iteriere direkt über die li-Elemente
                a_tag = li.find('a')
                # Überprüfe, ob es sich um ein strukturelles Element wie "Filme:" handelt
                if li.find('span', string='Filme:'):
                    logging.debug(f"Ignoriere strukturelles Element 'Filme:' in Filmsammlung für {serien_Name}: {li.prettify()}")
                    continue # Überspringe dieses Element, da es kein Film-Link ist
                
                # Nur verarbeiten, wenn es ein gültiges 'a'-Tag mit 'href' gibt
                if a_tag and a_tag.get('href'):
                    movie_title = a_tag.get_text(strip=True)
                    movie_url_suffix = a_tag.get('href')
                    full_movie_url = f"{BASE_URL}{movie_url_suffix}"

                    if movie_title in existing_movie_identifiers:
                        logging.debug(f"Film '{movie_title}' für {serien_Name} bereits vorhanden. Überspringe Abruf.")
                        continue

                    # Füge den Task und die zugehörigen Filminformationen hinzu
                    tasks.append(fetch_stream_links_async(session, full_movie_url, serien_Name, 'movie', movie_title))
                    valid_movies_to_process.append({"movie_title": movie_title, "movie_url": full_movie_url})
                else:
                    # Logge das ungültige Element auf DEBUG-Ebene, da es kein Film-Link ist, aber nicht unbedingt ein Fehler
                    logging.debug(f"Unerwartetes/ungültiges li-Element in Filmsammlung gefunden (kein gültiger Link): {li.prettify()}")
                    global_stats["failed_items_details"].append({
                        "type": "invalid_movie_element",
                        "series": serien_Name,
                        "url": full_movie_collection_url,
                        "element_html": li.prettify(),
                        "error": "Film-Element ohne gültigen Link/Titel."
                    })
            
            if not tasks:
                logging.info(f"Alle Filme für {serien_Name} bereits vorhanden oder keine neuen gefunden.")
                return existing_movies # Füge bestehende hinzu

            all_movie_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            successful_fetches = 0
            failed_fetches = 0
            temp_movie_results = []

            # Nun iteriere unter Verwendung des Index von valid_movies_to_process und all_movie_results
            for i, result in enumerate(all_movie_results):
                # Rufe die ursprünglichen Filminformationen mit demselben Index ab
                original_movie_info = valid_movies_to_process[i]
                movie_title = original_movie_info["movie_title"]
                full_movie_url = original_movie_info["movie_url"]

                if isinstance(result, Exception):
                    logging.error(f"Fehler bei Film '{movie_title}' für {serien_Name}: {result}")
                    failed_fetches += 1
                    global_stats["failed_items_details"].append({
                        "type": "stream_link_fetch_error_movie",
                        "series": serien_Name,
                        "item_type": "movie",
                        "item_identifier": movie_title,
                        "url": full_movie_url,
                        "error": str(result)
                    })
                elif result and (result["primary_link"] or result["vidoza_link"] or result["voe_link"]):
                    temp_movie_results.append({
                        "movie_title": movie_title,
                        "movie_url": full_movie_url,
                        "stream_links": result
                    })
                    successful_fetches += 1
                else:
                    logging.warning(f"Film '{movie_title}' für {serien_Name}: Keine Links gefunden (Fallback).")
                    failed_fetches += 1 
                    global_stats["failed_items_details"].append({
                        "type": "no_stream_links_found_fallback_movie",
                        "series": serien_Name, 
                        "item_type": "movie",
                        "item_identifier": movie_title,
                        "url": full_movie_url, # URL des einzelnen Films
                        "error": "Keine bevorzugten Streaming-Links (VOE/Vidoza) gefunden (Fallback)"
                    })

            logging.info(f"Ergebnisse für Filmsammlung von {serien_Name}: Erfolgreich {successful_fetches}/{len(tasks)}, Fehlgeschlagen {failed_fetches}.")
            
            # Kombiniere bestehende und neu gefundene Filme
            combined_movies = existing_movies + temp_movie_results
            # Sortiere die Filme nach Titel oder einer anderen Logik, falls nötig
            movies = sorted(combined_movies, key=lambda x: x.get('movie_title', ''))
            return movies

        except aiohttp.ClientError as e:
            error_type = "network_error_movie_collection"
            error_msg = f"FEHLER beim Abrufen der Filmsammlung {full_movie_collection_url} für {serien_Name}: {e}"
            logging.error(error_msg)
            global_stats["failed_items_details"].append({
                "type": error_type,
                "series": serien_Name,
                "url": full_movie_collection_url,
                "error": str(e)
            })
            return []
        except asyncio.TimeoutError:
            error_type = "timeout_error_movie_collection"
            error_msg = f"Timeout beim Abrufen der Filmsammlung {full_movie_collection_url} für {serien_Name}."
            logging.error(error_msg)
            global_stats["failed_items_details"].append({
                "type": error_type,
                "series": serien_Name,
                "url": full_movie_collection_url,
                "error": "Timeout"
            })
            return []
        except Exception as e:
            error_type = "parsing_error_movie_collection"
            error_msg = f"FEHLER beim Parsen der Filmsammlung {full_movie_collection_url} für {serien_Name}: {e}"
            logging.error(error_msg, exc_info=True)
            global_stats["failed_items_details"].append({
                "type": error_type,
                "series": serien_Name,
                "url": full_movie_collection_url,
                "error": str(e)
            })
            return []
    
async def process_single_series(serie_name_raw: str, current_series_index: int, total_series_count: int, existing_series_data: dict = None):
    """
    Verarbeitet eine einzelne TV-Serie: sammelt alle Staffeln und Episodenlinks, sowie Filmlinks.
    Berücksichtigt bereits vorhandene Daten für die Serie.

    Args:
        serie_name_raw (str): Der Rohname der Serie.
        current_series_index (int): Der aktuelle Index der Serie (1-basiert).
        total_series_count (int): Die Gesamtanzahl der zu verarbeitenden Serien.
        existing_series_data (dict, optional): Vorhandene Daten für diese Serie, falls geladen.

    Returns:
        dict: Ein Dictionary mit den (aktualisierten) Daten der verarbeiteten Serie.
    """
    # Initialisiere series_data mit vorhandenen Daten oder als neue Struktur
    series_data = existing_series_data if existing_series_data is not None else {
        "series_name": serie_name_raw,
        "base_url": "", # Neu hinzugefügt: Basis-URL der Serie
        "seasons": [], # Separate Liste für Staffeln
        "film": []     # Separate Liste für Filme
    }
    
    try:
        serie_name_formatted = serie_name_raw.strip().replace(" ", "-").lower()
        initial_series_url = f"{BASE_URL}/serie/stream/{serie_name_formatted}/staffel-1/episode-1"
        series_data["base_url"] = initial_series_url # Setze die base_url
        
        logging.info(f"--- Starte Verarbeitung für Serie {current_series_index}/{total_series_count}: {serie_name_raw} ---")
        
        # --- Gesamtstruktur der Serie (Staffeln und Filme) abrufen ---
        # Erstelle eine temporäre Session für diese einzelne Anfrage
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            # get_series_structure_async gibt eine Liste von Struktur-Objekten zurück
            series_structure = await get_series_structure_async(session, initial_series_url, serie_name_raw)
        
        if not series_structure:
            logging.warning(f"Konnte keine Staffeln oder Filme für {serie_name_raw} bestimmen. Überspringe Serienverarbeitung.")
            global_stats["total_series_failed"] += 1
            global_stats["failed_items_details"].append({
                "type": "series_no_structure_found",
                "series": serie_name_raw,
                "url": initial_series_url,
                "error": "Keine Staffeln oder Filme gefunden oder XPath falsch"
            })
            return series_data # Aktuelle Daten zurückgeben, wenn keine Struktur gefunden wurde

        # Verarbeite jede gefundene Struktur-Einheit (Staffel oder Filmsammlung)
        for item in series_structure:
            if item['type'] == 'season':
                season = item['number']
                # Prüfen, ob diese Staffel bereits in den vorhandenen Daten existiert
                existing_season_data = next((s for s in series_data["seasons"] if s.get('season_number') == season), None)
                
                current_season_data = {
                    "season_number": season,
                    "episode_links": []
                }

                if existing_season_data:
                    # Wenn die Staffel bereits existiert, übernehme ihre Episodenlinks
                    current_season_data["episode_links"] = existing_season_data["episode_links"]
                    logging.info(f"Staffel {season} für {serie_name_raw} bereits teilweise verarbeitet ({len(current_season_data['episode_links'])} Episoden vorhanden). Versuche fehlende Episoden zu finden.")
                else:
                    # Wenn die Staffel neu ist, füge sie zur Liste hinzu
                    series_data["seasons"].append(current_season_data)
                
                # get_episode_url_per_season aufrufen und vorhandene Episodenlinks übergeben
                updated_episode_links = await get_episode_url_per_season(
                    serie_name_formatted, 
                    season, 
                    current_series_index, 
                    total_series_count,
                    current_season_data["episode_links"] # Übergabe der bereits vorhandenen Links
                )
                current_season_data["episode_links"] = updated_episode_links
                
            elif item['type'] == 'movie_collection':
                movie_collection_url_suffix = item['url_suffix']
                
                # get_movie_collection_details_async aufrufen
                # existing_series_data['film'] enthält die bereits vorhandenen Filme
                updated_movie_list = await get_movie_collection_details_async(
                    serie_name_formatted, 
                    movie_collection_url_suffix, 
                    current_series_index, 
                    total_series_count,
                    series_data["film"] # Übergabe der bereits vorhandenen Filme
                )
                series_data["film"] = updated_movie_list # Aktualisiere die Film-Liste der Serie

        # Sortiere die Staffeln nach ihrer Nummer
        series_data["seasons"].sort(key=lambda x: x.get('season_number', float('inf')))
        # Sortiere die Filme nach Titel
        series_data["film"].sort(key=lambda x: x.get('movie_title', ''))
            
        logging.info(f"--- Alle Staffeln und Filme für {serie_name_raw} (Serie {current_series_index}/{total_series_count}) erfolgreich erfasst (oder teilweise erfasst). ---")
        global_stats["total_series_processed_successfully"] += 1
        logging.info("-" * 40)
        return series_data # Die gesammelten Daten zurückgeben
        
    except Exception as e:
        logging.error(f"Ein unbehandelter kritischer Fehler ist bei der Verarbeitung von {serie_name_raw} aufgetreten: {e}", exc_info=True)
        global_stats["total_series_failed"] += 1
        global_stats["failed_items_details"].append({
            "type": "series_unhandled_error",
            "series": serie_name_raw,
            "error": str(e)
        })
        # Die teilweise gesammelten Daten zurückgeben, auch wenn ein unbehandelter kritischer Fehler aufgetreten ist
        return series_data 

async def main_async():
    """
    Asynchrone Hauptfunktion zum Ausführen des TV-Serien-Downloaders.
    Verarbeitet Serien nacheinander, sammelt aber Episodenlinks parallel (asynchron).
    Sammelt Daten für alle Serien und speichert sie in einer einzigen JSON-Datei.
    Berücksichtigt bereits vorhandene Daten und überspringt bereits verarbeitete Serien.
    """
    start_time_overall = time.time()
    
    serien_Names = read_series_txt()
    if not serien_Names:
        logging.info("Keine Seriennamen zum Verarbeiten. Beende.")
        return

    # Bestehende Daten zu Beginn laden
    all_series_data = load_existing_series_data("all_series_data.json")
    # Konvertiere die Liste in ein Dictionary für schnellen Zugriff und einfache Aktualisierung
    series_data_map = {s['series_name'].replace(" ", "-").lower(): s for s in all_series_data}

    total_series_count = len(serien_Names)
    global_stats["total_series_processed_successfully"] = 0
    global_stats["total_series_skipped"] = 0
    global_stats["total_series_failed"] = 0
    global_stats["failed_items_details"] = [] # Zurücksetzen für jeden Lauf

    logging.info(f"Starte Verarbeitung von insgesamt {total_series_count} Serien.")

    # Jede Serie sequentiell verarbeiten
    for i, serie_raw in enumerate(serien_Names, 1):
        serie_formatted = serie_raw.strip().replace(" ", "-").lower()
        
        # Prüfen, ob die Serie bereits in den geladenen Daten existiert
        existing_series_entry = series_data_map.get(serie_formatted)

        if existing_series_entry:
            # Wir werden die Serie nicht mehr komplett überspringen, sondern versuchen, sie zu aktualisieren.
            logging.info(f"Serie '{serie_raw}' (Serie {i}/{total_series_count}) existiert bereits in 'all_series_data.json'. Versuche Aktualisierung.")
            # Die Zählung der übersprungenen Serien ist hier nicht mehr ganz zutreffend,
            # da wir sie nicht komplett überspringen, sondern aktualisieren.
            # global_stats["total_series_skipped"] += 1 # Entfernt, da wir nicht mehr komplett überspringen
            
        result = await process_single_series(serie_raw, i, total_series_count, existing_series_entry)
        
        if result:
            # Aktualisiere den Eintrag in der Map
            series_data_map[serie_formatted] = result
        else:
            logging.warning(f"process_single_series für '{serie_raw}' hat unerwartet None zurückgegeben. Diese Seriendaten werden nicht gespeichert.")
            # Fehlerstatistik wird bereits in process_single_series aktualisiert

        # Speichere den Fortschritt nach jeder Serie
        write_json_file(list(series_data_map.values()), "all_series_data.json")

        # Optional: Eine kurze Pause zwischen den Serien, um das System zu entlasten
        time.sleep(2) # 2 Sekunden Pause zwischen den Serien

    end_time_overall = time.time()
    total_duration = end_time_overall - start_time_overall

    logging.info("=" * 50)
    logging.info("Scraping-Vorgang abgeschlossen!")
    logging.info(f"Gesamtzeit: {total_duration:.2f} Sekunden")
    logging.info(f"Gesamtanzahl Serien in Liste: {total_series_count}")
    logging.info(f"Erfolgreich verarbeitete Serien: {global_stats['total_series_processed_successfully']}")
    # Die Zählung der übersprungenen Serien ist jetzt weniger relevant, da wir immer versuchen, zu aktualisieren.
    # Sie könnte sich auf Serien beziehen, die in der Vergangenheit *vollständig* verarbeitet wurden und keine Updates benötigen.
    logging.info(f"Übersprungene Serien (bereits vorhanden): {global_stats['total_series_skipped']}") 
    logging.info(f"Fehlgeschlagene Serien (keine Staffeln/Fehler): {global_stats['total_series_failed']}")
    
    if global_stats["failed_items_details"]:
        logging.info("\n--- Details zu fehlgeschlagenen Elementen ---")
        for item in global_stats["failed_items_details"]:
            detail_msg = f"Typ: {item['type']}, Serie: {item.get('series')}"
            if 'season' in item:
                detail_msg += f", Staffel: {item['season']}"
            if 'episode' in item:
                detail_msg += f", Episode: {item['episode']}"
            if 'item_type' in item:
                detail_msg += f", Elementtyp: {item['item_type']}"
            if 'item_identifier' in item:
                detail_msg += f", Element-ID: {item['item_identifier']}"
            if 'url' in item:
                detail_msg += f", URL: {item['url']}"
            if 'xpath' in item: # Angepasst für die neue Logging-Struktur
                detail_msg += f", XPath: '{item['xpath']}'"
            if 'selector_attempt' in item: # Für ältere Selektor-Fehler
                detail_msg += f", Selektor-Versuch: '{item['selector_attempt']}'"
            if 'error' in item:
                detail_msg += f", Fehler: {item['error']}"
            logging.error(detail_msg)
    else:
        logging.info("Keine spezifischen Fehler bei der Elementverarbeitung aufgetreten.")

    logging.info("=" * 50)

def main():
    """
    Synchroner Einstiegspunkt zum Starten der asynchronen Hauptfunktion.
    """
    asyncio.run(main_async())
    logging.info("Skriptausführung abgeschlossen.")

if __name__ == "__main__":
    main()
