# Verwende ein offizielles Python-Image als Basis, aktualisiert auf Bullseye (Debian 11)
FROM python:3.9-slim-bullseye 

# Setze das Arbeitsverzeichnis im Container
WORKDIR /app

# Installiere Systemabhängigkeiten für FFmpeg und andere notwendige Pakete
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    build-essential \
    ffmpeg \
    libglib2.0-0 libnss3 libdbus-glib-1-2 libxcomposite1 libxrandr2 libxtst6 \
    libasound2 libfontconfig1 libfreetype6 libx11-6 libxext6 libxrender1 \
    libxi6 libsm6 libice6 libxcursor1 libxfixes3 libxinerama1 libxss1 \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libgdk-pixbuf2.0-0 libgtk-3-0 \
    libgbm-dev libdrm-dev libatspi2.0-0 libgconf-2-4 libappindicator1 \
    libsecret-1-0 libxkbcommon-x11-0 libxcomposite-dev libxrandr-dev libxtst-dev \
    && rm -rf /var/lib/apt/lists/*

# Kopiere die requirements.txt und installiere Python-Abhängigkeiten
# Der Pfad ist relativ zum Build-Kontext (dem Projekt-Root)
COPY docker/python-app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiere dein Python-Skript in den Container
# Der Pfad ist relativ zum Build-Kontext (dem Projekt-Root)
COPY app/downloadManager.py .

# Standard CMD (wird von docker-compose.yml überschrieben)
CMD ["python", "downloadManager.py", "http://186.2.175.5/redirect/18402708", "/app/Folgen"]