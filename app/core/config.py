"""Environment-backed application settings."""

import os
import secrets

APP_NAME = os.getenv("APP_NAME", "Hooshyar")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_urlsafe(32)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
