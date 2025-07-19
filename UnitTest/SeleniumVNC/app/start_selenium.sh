#!/bin/bash

# Start Xvfb (virtuelles Display) im Hintergrund
Xvfb :99 -screen 0 1920x1080x24 &

# Starte den VNC-Server im Hintergrund (ohne Passwort)
# -display :99: Verbindet sich mit dem Xvfb-Display
# -forever: Läuft unendlich
# -passwd '': Setzt ein leeres Passwort (da SE_VNC_NO_PASSWORD=true nicht mehr über ENV kommt)
# -rfbport 7900: Lauscht auf Port 7900
# -o /tmp/x11vnc.log: Schreibt VNC-Logs in eine Datei
x11vnc -display :99 -forever -passwd '' -rfbport 7900 -o /tmp/x11vnc.log &

# Starte den Selenium Standalone Server
# --log-level DEBUG: Für detaillierte Logs (DEIN DEBUG-LEVEL)
# --bind-host 0.0.0.0: Lauscht auf allen Netzwerkschnittstellen
# --port 4444: Der WebDriver-Port
# --allow-cors true: Erlaubt CORS für Remote-Verbindungen
# --override-max-sessions true --max-sessions 1: Stellt sicher, dass nur eine Session erlaubt ist (DEINE SESSION-EINSTELLUNG)
java -jar /opt/selenium/selenium-server.jar standalone \
  --log-level DEBUG \
  --bind-host 0.0.0.0 \
  --port 4444 \
  --allow-cors true \
  --override-max-sessions true \
  --max-sessions 1

# Optional: Wenn der Java-Befehl beendet wird, beende auch das Skript
exit $?
