import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

DB_PATH = os.getenv("ADCONTACT_DB_PATH", os.path.join(os.path.dirname(__file__), "../../adcontact.db"))
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")

# HTTP Basic Auth credentials for the web UI
ADCONTACT_USER = os.getenv("ADCONTACT_USER", "admin")
ADCONTACT_PASS = os.getenv("ADCONTACT_PASS", "Aria@2025!")
