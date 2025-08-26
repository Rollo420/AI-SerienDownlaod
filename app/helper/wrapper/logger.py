import logging
import os
import sys


class Logging:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Logging, cls).__new__(cls)
            print("Logging Singleton instance created")
        return cls._instance
    
    def __init__(self, agentName, logfilePath) -> None:
        
        self.agentName = agentName
        self.LOGFILE_PATH = logfilePath
        self.logger = self.setup_logging()
        self.logger.info(f"Logging initialisiert für Agent: {self.agentName}")
        
        
    def setup_logging(self):
        
        # Setze den Agentennamen als Attribut der 'log'-Funktion, damit sie darauf zugreifen kann.
        # Dies ist der Mechanismus, um den Wert ohne globale Variable zu übergeben.
        

        # --- Angepasstes Logging Setup für Live-Ausgabe ---
        # Hole den Logger direkt
        logger = logging.getLogger("seriendownloader")
        logger.setLevel(logging.INFO)  # Setze das allgemeine Level für den Logger

        # Optional: Entferne alle bestehenden Handler, falls basicConfig bereits aufgerufen wurde
        # Dies ist nützlich, wenn das Skript in einer Umgebung läuft, in der Logging bereits konfiguriert sein könnte.
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
            handler.close()

        # Erstelle einen Formatter mit dem gewünschten Format, inklusive Agentenname, Dateiname und Zeilennummer
        formatter = logging.Formatter(
            "%(asctime)s %(agentName)s %(levelname)s %(filename)s:%(lineno)d - %(message)s"
        )

        # Die Pufferung wird nun vom darunterliegenden Dateisystem gehandhabt.
        file_handler = logging.FileHandler(self.LOGFILE_PATH, encoding="utf-8")
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        # Erstelle den StreamHandler (für die Konsolenausgabe)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        
        return self.logger

    def get_logger(self):
        return self.logger


    def log(self, msg, level="info"):
        """
        Schreibt eine Nachricht in die Log-Datei und auf die Konsole.
        Verwendet den Logger "seriendownloader" und fügt den Agentennamen als 'extra' Kontext hinzu.
        """
        # Angepasst: Holt 'agentName' vom Attribut der 'log'-Funktion,
        # das in der main-Funktion gesetzt wird.
        
        extra_data = {"agentName": getattr(self.agentName, "agentName", "nullAgent")}

        current_logger = self.logger

        if level == "error":
            current_logger.error(msg, extra=extra_data)
        elif level == "warning":
            current_logger.warning(msg, extra=extra_data)
        elif level == "debug":
            current_logger.debug(msg, extra=extra_data)
        else:
            current_logger.info(msg, extra=extra_data)
            
            
