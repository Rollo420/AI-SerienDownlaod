import logging
import os
import sys


class Logging:
    _instance = None
    _initialized = False

    def __new__(cls, agentName=None, logfilePath=None):
        if cls._instance is None:
            cls._instance = super(Logging, cls).__new__(cls)
        return cls._instance

    def __init__(self, agentName=None, logfilePath=None):
        if not Logging._initialized:
            # Nur beim ersten Mal Parameter übernehmen
            self.agentName = agentName if agentName is not None else "nullAgent"
            self.LOGFILE_PATH = logfilePath if logfilePath is not None else "seriendownloader.log"
            self.logger = self.setup_logging()
            self.logger.info(f"Logging initialisiert für Agent: {self.agentName}")
            Logging._initialized = True
        # Bei weiteren Instanzen: keine erneute Initialisierung, keine Fehler

    def setup_logging(self):
        logger = logging.getLogger("seriendownloader")
        logger.setLevel(logging.INFO)
        # Entferne alle bestehenden Handler
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        formatter = logging.Formatter(
            "%(asctime)s %(agentName)s %(levelname)s %(filename)s:%(lineno)d - %(message)s"
        )
        file_handler = logging.FileHandler(self.LOGFILE_PATH, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        return logger

    def get_logger(self):
        return self.logger

    def log(self, msg, level="info"):
        extra_data = {"agentName": getattr(self, "agentName", "nullAgent")}
        current_logger = self.logger
        if level == "error":
            current_logger.error(msg, extra=extra_data)
        elif level == "warning":
            current_logger.warning(msg, extra=extra_data)
        elif level == "debug":
            current_logger.debug(msg, extra=extra_data)
        else:
            current_logger.info(msg, extra=extra_data)


