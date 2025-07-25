import os
import sys
import json
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException


def find_my_element(driver, css_path: str):
    """
    Sucht ein HTML-Element anhand des angegebenen CSS-Pfads.

    Args:
        driver (webdriver.Firefox): Der WebDriver.
        css_path (str): Der CSS-Pfad des Elements.

    Returns:
        WebElement: Das gefundene WebElement.
    """
    element = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, css_path))
    )
    return element
def get_all_episodes(driver, xpath):
    """
    Ermittelt die Gesamtanzahl der Episoden für eine TV-Serie.

    Args:
        driver (webdriver.Firefox): Der WebDriver.
        xpath (str): Der XPath des Elements, das die Episoden enthält.

    Returns:
        int: Die Gesamtanzahl der Episoden.
    """
    my_element = find_my_element(driver, xpath)

    if my_element:
        li_elements = my_element.find_elements(By.XPATH, ".//li")
        return len(li_elements)
    else:
        print("Das Element wurde nicht gefunden oder hat keine untergeordneten <li> Elemente.")
        return 1      


def initialize_driver():
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
    print("Starte Chromium im Headless-Modus (im Docker-Container)...")
    #options.add_argument("--headless=new")
    
    try:
        selenium_hub_url = os.getenv("SELENIUM_HUB_URL", "http://selenium-chromium:4444/wd/hub")
        driver = webdriver.Remote(
            command_executor=selenium_hub_url,
            options=options
        )
        print(f"Chromium WebDriver erfolgreich mit {selenium_hub_url} verbunden.")
        return driver
    
    except WebDriverException as e:
        print(f"FEHLER beim Initialisieren des WebDriver: {e}", "error")
        sys.exit(1)

def find_video_stream_service(driver, url):
    """
    Sucht nach verfügbaren Streaming-Diensten für eine TV-Serie.

    Args:
        driver (webdriver.Firefox): Der WebDriver.
        url (str): Die URL der TV-Serie.

    Returns:
        None
    """
    all_stream_services = []

    try:
        driver.get(url)
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
        soup = BeautifulSoup(driver.page_source, "html.parser")

        elements = soup.find_all("i", class_="icon")

        for element in elements:
            class_value = element.get("class")
            link_element = element.find_parent("a")
            href = link_element.get("href")

            all_stream_services.append({"name": class_value[1], "href_link": href})

        
        for service in all_stream_services:
            if "Vidoza" in service["name"]:
                actual_link = f'https://186.2.175.5{service["href_link"]}'
                #ShowService.vidoza_mode(actual_link)
                #break

            elif "VOE" in service["name"]:
                actual_link = f'https://186.2.175.5{service["href_link"]}'
                #ShowService.voe_mode(actual_link)

        return actual_link


    finally:
        driver.quit()

    
def main():
    """
    Hauptfunktion zum Ausführen des TV-Serien-Downloaders.
    """
    url_list = []
    
    name = os.getenv("SERIE_NAME", "the-witcher").strip().replace(" ", "-").lower() or "the-witcher"
    driver = initialize_driver()

    url = f"https://186.2.175.5/serie/stream/{name}/staffel-1/episode-1"
    print("Die URL ist:", url)
    
    driver.get(url)
    
    
    episodes = get_all_episodes(driver, "/html/body/div[3]/div[2]/div[2]/div[3]/ul[2]") - 1
    staffel = get_all_episodes(driver, '//*[@id="stream"]/ul[1]') - 1
    

    print(f"Anzahl der Episoden: {episodes}")
    print(f"Anzahl der Episoden: {episodes}")
    print(f"Anzahl der Episoden: {episodes}")
    print(f"Anzahl der Episoden: {episodes}")
    print(f"Anzahl der Episoden: {episodes}")
    
    
    print(f"Anzahl Staffel: {staffel}")
    print(f"Anzahl Staffel: {staffel}")
    print(f"Anzahl Staffel: {staffel}")
    print(f"Anzahl Staffel: {staffel}")
    print(f"Anzahl Staffel: {staffel}")
            
    actual_link = find_video_stream_service(driver, url)
    
    print(f"Gefundener Video-Stream-Link: {actual_link}")
    print(f"Gefundener Video-Stream-Link: {actual_link}")
    print(f"Gefundener Video-Stream-Link: {actual_link}")
    print(f"Gefundener Video-Stream-Link: {actual_link}")
    print(f"Gefundener Video-Stream-Link: {actual_link}")

if __name__ == "__main__":
    main()
    sys.exit(0)