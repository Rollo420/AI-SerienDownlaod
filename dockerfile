# Verwende ein schlankes Python-Image als Basis
FROM python:3.12-slim

# Setze das Arbeitsverzeichnis im Container
WORKDIR /app

# Installiere Systemabhängigkeiten für Firefox, GeckoDriver und FFmpeg.
# `--no-install-recommends` hilft, die Image-Größe klein zu halten.
# `xvfb` ist eine virtuelle Framebuffer-Anzeige, die oft für Headless-Browser in Containern nützlich ist.
RUN apt-get update && apt-get install -y --no-install-recommends \
    firefox-esr \
    wget \
    tar \
    ffmpeg \
    xvfb \
    # Bibliotheken, die für Firefox im Headless-Modus benötigt werden
    libgtk-3-0 \
    libdbus-glib-1-2 \
    libx11-xcb1 \
    libdbus-1-3 \
    libxt6 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libasound2 \
    libpulse0 \
    # Bereinige den APT-Cache, um die Image-Größe weiter zu reduzieren
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Installiere GeckoDriver manuell.
# HINWEIS: Dein Python-Skript verwendet `webdriver_manager.firefox.GeckoDriverManager().install()`.
# Wenn du dich ausschließlich auf `webdriver_manager` verlassen möchtest,
# könntest du diesen Block entfernen. Eine manuelle Installation im Dockerfile
# kann jedoch für eine konsistente Treiberversion im Container sorgen.
ENV GECKODRIVER_VERSION="v0.36.0"
ENV GECKODRIVER_URL="https://github.com/mozilla/geckodriver/releases/download/${GECKODRIVER_VERSION}/geckodriver-${GECKODRIVER_VERSION}-linux64.tar.gz"

RUN wget -q ${GECKODRIVER_URL} \
    && tar -xzf geckodriver-${GECKODRIVER_VERSION}-linux64.tar.gz -C /usr/local/bin/ \
    && chmod +x /usr/local/bin/geckodriver \
    && rm geckodriver-${GECKODRIVER_VERSION}-linux64.tar.gz

# Bereite den Adblocker (uBlock Origin) vor.
# Das Python-Skript wird prüfen, ob die Datei vorhanden ist, bevor es sie erneut herunterlädt.
ENV UBLOCK_XPI_URL="https://addons.mozilla.org/firefox/downloads/file/4216633/ublock_origin-1.55.0.xpi"
ENV UBLOCK_XPI_FILE="ublock_origin.xpi"

# Die Datei wird direkt in /app heruntergeladen, daher ist kein 'mv' mehr nötig.
RUN wget -q -O ${UBLOCK_XPI_FILE} ${UBLOCK_XPI_URL}

# Installiere Python-Abhängigkeiten
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiere deinen Python-Code in den Container
# Stelle sicher, dass dein Hauptskript "downloadManager.py" heißt oder passe den CMD-Befehl an.
COPY . .

# Optional: Umgebungsvariable, um Python-Ausgaben direkt anzuzeigen
ENV PYTHONUNBUFFERED=1

# Standardbefehl zum Starten deines Python-Skripts.
# Die URL und der Zielpfad werden als Argumente übergeben.
# RAM- und CPU-Limits werden beim Ausführen des Containers festgelegt, nicht hier.
CMD ["python", "downloadManager.py", "https://186.2.175.5/redirect/18366862", "/app/ausgabe"]