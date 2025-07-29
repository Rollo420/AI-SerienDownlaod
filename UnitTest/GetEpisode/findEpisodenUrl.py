import os
import sys
import json
import logging
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException, InvalidSessionIdException
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("series_scraper.log"), # Log to a file
        logging.StreamHandler(sys.stdout)          # Log to console
    ]
)

def read_series_txt():
    """
    Liest die Seriennamen aus der Datei 'seriesNames.txt' und gibt sie als Liste zurück.

    Returns:
        list: Eine Liste von seriennamen.
    """
    try:
        with open('./seriesNames.txt', 'r', encoding='utf-8') as file:
            series = [line.strip() for line in file if line.strip()] # Read lines and remove empty ones
        logging.info(f"Successfully read {len(series)} series names from 'seriesNames.txt'.")
        return series
    except FileNotFoundError:
        logging.error("The file 'seriesNames.txt' was not found.")
        return []
    except Exception as e:
        logging.error(f"Error reading 'seriesNames.txt': {e}")
        return []

def find_my_element(driver, xpath: str):
    """
    Sucht ein HTML-Element anhand des angegebenen XPath-Pfads.

    Args:
        driver (webdriver.Remote): Der WebDriver.
        xpath (str): Der XPath des Elements.

    Returns:
        WebElement: Das gefundene WebElement, oder None, wenn nicht gefunden.
    """
    try:
        element = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
        return element
    except Exception as e:
        logging.warning(f"Element with XPath '{xpath}' not found after waiting: {e}")
        return None

def get_all_episodes_or_seasons(driver, xpath):
    """
    Ermittelt die Gesamtanzahl der Episoden für eine TV-Serie oder Staffeln.
    Diese Funktion wird sowohl für die Gesamtanzahl der Staffeln als auch für die Gesamtanzahl der Episoden innerhalb einer Staffel verwendet.

    Args:
        driver (webdriver.Remote): Der WebDriver.
        xpath (str): Der XPath des Elements, das die Episoden/Staffeln enthält.

    Returns:
        int: Die Gesamtanzahl der Episoden/Staffeln. Gibt 0 zurück, wenn das Element nicht gefunden wurde oder keine Elemente enthält.
    """
    my_element = find_my_element(driver, xpath)

    if my_element:
        try:
            li_elements = my_element.find_elements(By.XPATH, ".//li")
            count = len(li_elements)
            logging.info(f"Found {count} items for XPath '{xpath}'.")
            return count
        except Exception as e:
            logging.error(f"Error counting <li> elements for XPath '{xpath}': {e}")
            return 0
    else:
        logging.warning(f"Element for XPath '{xpath}' not found. Returning 0 items.")
        return 0

def initialize_driver():
    """
    Initialisiert und konfiguriert den Chromium WebDriver.

    Returns:
        webdriver.Remote: Der initialisierte WebDriver.
    """
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--headless=new") # Ensure headless mode is explicitly enabled

    logging.info("Starting Chromium in headless mode (in Docker container)...")

    try:
        selenium_hub_url = os.getenv("SELENIUM_HUB_URL", "http://selenium-chromium:4444/wd/hub")
        driver = webdriver.Remote(
            command_executor=selenium_hub_url,
            options=options
        )
        logging.info(f"Chromium WebDriver successfully connected to {selenium_hub_url}.")
        return driver
    except WebDriverException as e:
        logging.error(f"ERROR initializing WebDriver: {e}")
        raise # Re-raise the exception to be caught by the calling function

def find_video_stream_service_threaded(url):
    """
    Sucht nach verfügbaren Streaming-Diensten für eine TV-Serie.
    Diese Version initialisiert ihren eigenen Treiber für Threading-Zwecke.

    Args:
        url (str): Die URL der TV-Serie.

    Returns:
        str: Der Link des bevorzugten Streaming-Dienstes (Vidoza oder VOE), oder None.
    """
    driver = None
    actual_link = None
    try:
        driver = initialize_driver() # Initialize driver for each thread
        all_stream_services = []

        logging.info(f"Navigating to URL to find stream services: {url}")
        driver.get(url)
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
        soup = BeautifulSoup(driver.page_source, "html.parser")

        elements = soup.find_all("i", class_="icon")

        for element in elements:
            class_value = element.get("class")
            if class_value and len(class_value) > 1: # Ensure class_value exists and has at least two elements
                service_name = class_value[1]
                link_element = element.find_parent("a")
                if link_element:
                    href = link_element.get("href")
                    if href:
                        all_stream_services.append({"name": service_name, "href_link": href})
        
        logging.info(f"Found {len(all_stream_services)} potential streaming services for {url}.")

        # Prioritize Vidoza, then VOE
        for service in all_stream_services:
            if "Vidoza" in service["name"]:
                actual_link = f'https://186.2.175.5{service["href_link"]}'
                logging.info(f"Found Vidoza link for {url}: {actual_link}")
                break
            elif "VOE" in service["name"]:
                actual_link = f'https://186.2.175.5{service["href_link"]}'
                logging.info(f"Found VOE link for {url}: {actual_link}")
                # Don't break here, continue to check for Vidoza if it's preferred
        
        if not actual_link:
            logging.warning(f"No preferred streaming service (Vidoza or VOE) found for {url}. Found services: {all_stream_services}")

        return actual_link
    except Exception as e:
        logging.error(f"ERROR while searching for streaming services at {url}: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
                logging.info(f"WebDriver for {url} quit.")
            except InvalidSessionIdException:
                logging.warning(f"WebDriver for {url} already quit or session invalid when attempting to quit.")
            except Exception as e:
                logging.error(f"Error quitting WebDriver for {url}: {e}")


def get_episode_url_per_season(driver_main, serien_Name, season):
    """
    Sammelt alle Episode-Links für eine bestimmte Staffel einer TV-Serie,
    wobei das Suchen der Links für jede Episode parallel erfolgt.

    Args:
        driver_main (webdriver.Remote): Der Haupt-WebDriver für die Serien-/Staffelnavigation.
        serien_Name (str): Der Name der Serie.
        season (int): Die Staffelnummer.

    Returns:
        tuple: Ein Tupel (list, webdriver.Remote) mit einer Liste von Episode-Links und dem (potenziell neu initialisierten) Haupt-WebDriver.
    """
    links = []
    
    initial_episode_url = f"https://186.2.175.5/serie/stream/{serien_Name}/staffel-{season}/episode-1"
    
    # --- NEUE OPTIMIERUNG: Retry-Logik für den Haupt-WebDriver bei Staffel-Navigation ---
    max_main_driver_retries_per_season = 3
    for retry_attempt in range(max_main_driver_retries_per_season):
        try:
            logging.info(f"Navigating main driver to {initial_episode_url} (Attempt {retry_attempt + 1}/{max_main_driver_retries_per_season})")
            driver_main.get(initial_episode_url)
            WebDriverWait(driver_main, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
            break # Break from retry loop if successful
        except InvalidSessionIdException:
            logging.warning(f"Main WebDriver session for {serien_Name}, Season {season} became invalid during navigation. Attempting to re-initialize.")
            if driver_main:
                try:
                    driver_main.quit()
                except Exception as e:
                    logging.error(f"Error quitting invalid main driver for {serien_Name}, Season {season}: {e}")
            driver_main = initialize_driver() # Re-initialize the main driver
            time.sleep(5) # Wait a bit before retrying
        except Exception as e:
            logging.error(f"Could not load initial episode URL for {serien_Name}, Season {season} after retries: {e}", exc_info=True)
            return links, driver_main # Return empty links and current driver if navigation fails after retries
    else: # This else block executes if the for-loop completes without a 'break' (i.e., all retries failed)
        logging.error(f"Failed to load initial episode URL for {serien_Name}, Season {season} after {max_main_driver_retries_per_season} retries. Skipping season.")
        return links, driver_main
    # --- ENDE NEUE OPTIMIERUNG ---

    # XPath for episodes list within a season
    episodes_list_xpath = "/html/body/div[3]/div[2]/div[2]/div[3]/ul[2]"
    total_episodes = get_all_episodes_or_seasons(driver_main, episodes_list_xpath)

    # The website seems to count 1 more than actual episodes, or the first episode is always "1"
    # Adjusting based on previous logic (total_episodes - 1)
    # If total_episodes is 0, this will result in -1, so handle that.
    if total_episodes > 0:
        total_episodes -= 1 # Adjusting for 0-based indexing or website's extra item
    else:
        logging.warning(f"No episodes found for {serien_Name}, Season {season}. Skipping.")
        return links, driver_main

    logging.info(f"{serien_Name}, Season {season} has a total of {total_episodes} episodes.")
    
    # Use ThreadPoolExecutor to fetch episode links concurrently
    # Adjust max_workers as needed for concurrent episode link fetching.
    # Be cautious: A high number of workers means many browser instances, which can
    # overload the Selenium Hub or the system running the browsers.
    episode_max_workers = 3 # Reduced from 5 to 3 to mitigate system load issues
    logging.info(f"Starting ThreadPoolExecutor for episodes with max_workers={episode_max_workers}.")

    with ThreadPoolExecutor(max_workers=episode_max_workers) as executor:
        futures = []
        for episode in range(1, total_episodes):
            url = f"https://186.2.175.5/serie/stream/{serien_Name}/staffel-{season}/episode-{episode}"
            # Submit each episode link fetching task to the executor
            futures.append(executor.submit(find_video_stream_service_threaded, url))
        
        # Collect results as they complete
        # Note: Using as_completed means results might not be in order of episode number.
        # If order is crucial, you would need to store results with episode number and sort.
        for future in as_completed(futures):
            try:
                episode_link = future.result()
                if episode_link:
                    links.append(episode_link)
                    # Logging for individual episode link is now inside find_video_stream_service_threaded
            except Exception as e:
                logging.error(f"An error occurred while fetching an episode link: {e}")
            
    return links, driver_main # Return the links and the (potentially updated) driver_main
        
def write_json_file(data, filename):
    """
    Schreibt die Daten in eine JSON-Datei.

    Args:
        data (dict): Die zu schreibenden Daten.
        filename (str): Der Name der JSON-Datei.
    """
    try:
        with open(filename, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4)
        logging.info(f"Data successfully written to {filename}.")
    except Exception as e:
        logging.error(f"Error writing data to {filename}: {e}")
    
def load_existing_series_data(filename='all_series_data.json'):
    """
    Lädt vorhandene Seriendaten aus einer JSON-Datei.

    Args:
        filename (str): Der Name der JSON-Datei.

    Returns:
        list: Eine Liste von Seriendaten, oder eine leere Liste, wenn die Datei nicht existiert oder ungültig ist.
    """
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, list):
                    logging.info(f"Successfully loaded existing data from {filename}.")
                    return data
                else:
                    logging.warning(f"Existing file {filename} is not a list. Starting fresh.")
                    return []
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding JSON from {filename}: {e}. Starting fresh.")
            return []
        except Exception as e:
            logging.error(f"Error loading existing data from {filename}: {e}. Starting fresh.")
            return []
    else:
        logging.info(f"File {filename} does not exist. Starting with empty data.")
        return []

def process_single_series(serie_name_raw):
    """
    Verarbeitet eine einzelne TV-Serie: initialisiert den Driver,
    sammelt alle Staffeln und Episodenlinks.

    Returns:
        dict: Ein Dictionary mit den Daten der verarbeiteten Serie, auch wenn ein Fehler auftritt.
    """
    driver = None
    series_data = {
        "series_name": serie_name_raw,
        "seasons": []
    }
    
    try:
        serie_name_formatted = serie_name_raw.strip().replace(" ", "-").lower()
        
        logging.info(f"Starting processing for series: {serie_name_raw}")
        
        # Initialize the main driver for series/season navigation
        driver = initialize_driver() 

        # --- NEUE OPTIMIERUNG: Gesamtanzahl der Staffeln einmalig abrufen ---
        total_seasons = 0
        max_initial_driver_retries = 3
        for retry_attempt in range(max_initial_driver_retries):
            try:
                # Navigate to the first episode of the first season to get total seasons
                initial_series_url = f"https://186.2.175.5/serie/stream/{serie_name_formatted}/staffel-1/episode-1"
                driver.get(initial_series_url)
                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
                
                # XPath for seasons list (assuming it's available on the first episode page)
                seasons_list_xpath = '//*[@id="stream"]/ul[1]'
                total_seasons = get_all_episodes_or_seasons(driver, seasons_list_xpath)
                if total_seasons > 0:
                    total_seasons -= 1 # Adjusting for 0-based indexing or website's extra item
                
                logging.info(f"{serie_name_raw} has a total of {total_seasons} seasons.")
                break # Break if successful
            except InvalidSessionIdException:
                logging.warning(f"Main WebDriver session for {serie_name_raw} became invalid during initial season count. Attempting to re-initialize (Attempt {retry_attempt + 1}/{max_initial_driver_retries}).")
                if driver:
                    try:
                        driver.quit()
                    except Exception as e:
                        logging.error(f"Error quitting invalid main driver during initial count for {serie_name_raw}: {e}")
                driver = initialize_driver() # Re-initialize main driver
                time.sleep(5) # Wait a bit before retrying
            except Exception as e:
                logging.error(f"Error determining total seasons for {serie_name_raw}: {e}", exc_info=True)
                total_seasons = 0 # Set to 0 to skip season processing
                break # Break from retry loop if general error occurs

        if total_seasons == 0:
            logging.warning(f"Could not determine total seasons for {serie_name_raw}. Skipping season processing.")
            return series_data # Return current data if no seasons found

        # --- ENDE NEUE OPTIMIERUNG ---

        # Loop through seasons using the determined total_seasons
        for season in range(1, total_seasons):
            # Check if this season was already partially processed
            if any(s['season_number'] == season for s in series_data["seasons"]):
                logging.info(f"Season {season} for {serie_name_raw} already partially processed. Skipping.")
                continue

            current_season_data = {
                "season_number": season,
                "episode_links": []
            }
            
            logging.info(f"Starting Season {season} of {serie_name_raw}")
            
            # Call get_episode_url_per_season and update the main driver
            episode_links, driver = get_episode_url_per_season(driver, serie_name_formatted, season)
            current_season_data["episode_links"] = episode_links
            series_data["seasons"].append(current_season_data)
            logging.info(f"Finished Season {season} of {serie_name_raw} with {len(episode_links)} links.")
            
        logging.info(f"All episodes for {serie_name_raw} successfully captured (or partially captured).")
        logging.info("-" * 40)
        return series_data # Return the collected data
        
    except Exception as e:
        logging.error(f"An unhandled critical error occurred while processing {serie_name_raw}: {e}", exc_info=True)
        # Return the partially collected data even if an unhandled critical error occurred
        return series_data 
    finally:
        if driver:
            try:
                driver.quit()
                logging.info(f"WebDriver for {serie_name_raw} quit.")
            except InvalidSessionIdException:
                logging.warning(f"WebDriver for {serie_name_raw} already quit or session invalid when attempting to quit.")
            except Exception as e:
                logging.error(f"Error quitting WebDriver for {serie_name_raw}: {e}")

def main():
    """
    Hauptfunktion zum Ausführen des TV-Serien-Downloaders.
    Verarbeitet Serien nacheinander, aber sammelt Episodenlinks parallel.
    Sammelt Daten für alle Serien und speichert sie in einer einzigen JSON-Datei.
    Berücksichtigt bereits vorhandene Daten und überspringt bereits verarbeitete Serien.
    """
    serien_Names = read_series_txt()
    if not serien_Names:
        logging.info("No series names to process. Exiting.")
        sys.exit(0)

    # Load existing data at the beginning
    all_series_data = load_existing_series_data()
    existing_series_names = {s['series_name'] for s in all_series_data} # Use a set for faster lookups

    # Process each series sequentially
    for serie_raw in serien_Names:
        serie_formatted = serie_raw.strip().replace(" ", "-").lower()
        
        if serie_formatted in existing_series_names:
            logging.info(f"Series '{serie_raw}' already exists in 'all_series_data.json'. Skipping.")
            continue # Skip to the next series if already processed

        logging.info(f"Processing new series sequentially: {serie_raw}")
        result = process_single_series(serie_raw)
        # Always append the result, even if it's partial due to an error.
        # The 'result' will be the series_data dictionary, which might have incomplete seasons/episodes.
        if result: # Check if result is not None (e.g., if process_single_series had an unhandled critical error before initializing series_data)
            all_series_data.append(result)
            # Update the set of existing names to reflect the newly added series
            existing_series_names.add(result['series_name'])
        else:
            logging.warning(f"process_single_series for '{serie_raw}' returned None unexpectedly. This series data will not be saved.")


    # Write all collected data (including old and newly added) to a single JSON file
    if all_series_data:
        write_json_file(all_series_data, "all_series_data.json")
    else:
        logging.warning("No series data was collected to write to a JSON file.")

    logging.info("All series processing tasks completed.")

if __name__ == "__main__":
    main()
    logging.info("Script finished execution.")
    sys.exit(0)
