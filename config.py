import os


class AppConfig:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
    DATABASE = os.environ.get("INF5190_DB_PATH", "")
    CSV_CACHE = os.environ.get("INF5190_CSV_CACHE", "")
    SCHEDULER_ENABLED = os.environ.get("INF5190_SCHEDULER", "1") != "0"
    SCHEDULER_TZ = os.environ.get("INF5190_TZ", "America/Toronto")

    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "")
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "1") != "0"

    UNSUBSCRIBE_SALT = os.environ.get("UNSUBSCRIBE_SALT", "unsubscribe-restaurant")
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")

    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = int(os.environ.get("PORT", "5000"))
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"
