#!/bin/bash

# Pfad, unter dem das Neustart-Skript gespeichert wird
SCRIPT_DIR="/usr/local/bin"
REBOOT_SCRIPT_NAME="reboot-24h.sh"
REBOOT_SCRIPT_PATH="$SCRIPT_DIR/$REBOOT_SCRIPT_NAME"

# Der Inhalt des Neustart-Skripts
REBOOT_SCRIPT_CONTENT="#!/bin/bash
# Dieses Skript wird von cron nach 24 Stunden ausgeführt.
echo \"Neustart wird ausgeführt...\" >> /var/log/syslog
sudo reboot"

# Der crontab-Eintrag für den Neustart nach 24 Stunden (1440 Minuten)
# '@reboot' führt den Befehl nur beim Systemstart aus. Die 'sleep'-Verzögerung
# stellt sicher, dass der Neustart erst nach 24 Stunden (1440 Minuten) erfolgt.
REBOOT_CRON_JOB="@reboot sleep 1440m && $REBOOT_SCRIPT_PATH &"

# Stelle sicher, dass der Verzeichnispfad existiert
sudo mkdir -p "$SCRIPT_DIR"

# Erstelle das eigentliche Neustart-Skript
echo "$REBOOT_SCRIPT_CONTENT" | sudo tee "$REBOOT_SCRIPT_PATH" > /dev/null
sudo chmod +x "$REBOOT_SCRIPT_PATH"
echo "Neustart-Skript erfolgreich unter '$REBOOT_SCRIPT_PATH' erstellt."

# Entferne alte Einträge, um Duplikate zu vermeiden
(sudo crontab -l 2>/dev/null | grep -v "$REBOOT_SCRIPT_PATH") | sudo crontab -

# Füge den neuen crontab-Eintrag hinzu
(sudo crontab -l 2>/dev/null; echo "$REBOOT_CRON_JOB") | sudo crontab -

echo "Cron-Job erfolgreich für den Neustart nach 24 Stunden eingerichtet."
echo "Der Pi wird sich bei jedem Neustart in 24 Stunden automatisch neu starten."
