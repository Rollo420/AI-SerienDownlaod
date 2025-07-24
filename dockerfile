# Verwende ein Ubuntu 24.04 LTS-Image als Basis
FROM ubuntu:24.04

# Setze das Arbeitsverzeichnis im Container
WORKDIR /app

# Upgrade system und installiere allgemeine Tools, Python, pip
# Installiere auch `unzip` (für ChromeDriver, falls manuell heruntergeladen) und `xvfb` für Headless
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    software-properties-common \
    python3 \
    python3-pip \
    python3-venv \
    wget \
    tar \
    unzip \
    ffmpeg \
    xvfb \
    # Chromium und Chrome WebDriver installieren
    # `chromium-browser` und `chromium-chromedriver` sind die richtigen Pakete für Ubuntu 24.04
    chromium-browser \
    chromium-chromedriver \
    # Umfassende Liste von Bibliotheken, die für Headless Chrome/Chromium benötigt werden
    # Angepasst an Paketnamen von Ubuntu 24.04 LTS (mit `t64`-Suffixen)
    libgtk-3-0t64 \
    libdbus-glib-1-2 \
    libx11-xcb1 \
    libdbus-1-3 \
    libxt6t64 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libasound2t64 \
    libpulse0 \
    libfontconfig1 \
    libfreetype6 \
    libxkbcommon0 \
    libnss3 \
    libxss1 \
    libgbm1 \
    libu2f-udev \
    libvulkan1 \
    libglib2.0-0t64 \
    libnspr4 \
    libayatana-appindicator1 \
    libcurl4 \
    libexpat1 \
    libsecret-1-0t64 \
    libssl-dev \
    libunwind8 \
    fonts-liberation \
    xdg-utils \
    # Bereinige den APT-Cache, um die Image-Größe zu reduzieren
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Stelle sicher, dass `google-chrome` und `chromedriver` im PATH sind.
# `chromium-browser` wird oft mit einem Symlink zu `google-chrome` erwartet.
# `chromium-chromedriver` sollte den WebDriver in `/usr/lib/chromium-browser/chromedriver` platzieren.
# Wir erstellen hier vorsichtshalber zusätzliche Symlinks.
RUN ln -s /usr/bin/chromium-browser /usr/bin/google-chrome || true && \
    ln -s /usr/lib/chromium-browser/chromedriver /usr/local/bin/chromedriver || true

# Python-Abhängigkeiten aus requirements.txt installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiere deinen Python-Code in den Container
COPY . .

# Optional: Umgebungsvariable, um Python-Ausgaben sofort anzuzeigen
ENV PYTHONUNBUFFERED=1

# Standardbefehl zum Starten deines Python-Skripts.
# `xvfb-run` startet einen virtuellen Display-Server, der für Headless-Browser notwendig ist.
CMD ["xvfb-run", "--auto-display", "--server-num=1", "--server-args='-screen 0 1920x1080x24'", "python", "downloadManager.py", "https://186.2.175.5/redirect/18366862", "/app/ausgabe"]