from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time 

chrome_options = Options()
# Diese Optionen werden generell für Chrome/Chromium in Docker empfohlen
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("window-size=1920,1080") # Setzt eine konsistente Browserfenstergröße

# Verbindung zum Selenium-Container herstellen, der auf localhost:4444 läuft
# Für Selenium 4.15 und höher:
driver = webdriver.Remote(
    command_executor="http://localhost:4444/wd/hub",
    options=chrome_options
)
# Für ältere Selenium-Versionen:
# driver = webdriver.Remote(
#     command_executor="http://localhost:4444/wd/hub",
#     desired_capabilities=webdriver.DesiredCapabilities.CHROME
# )

driver.get("https://www.google.com")
print(f"Browser-Titel: {driver.title}")
# Führen Sie hier Ihre Testaktionen aus

time.sleep(180)
#...
driver.quit()