import os
from dotenv import load_dotenv

load_dotenv()

MODEL_NAME     = os.environ["MODEL_NAME"]
MODEL_PROVIDER = os.environ["MODEL_PROVIDER"]
API_KEY        = os.environ.get("API_KEY", "")
BASE_URL       = os.environ.get("BASE_URL") or None  # None = use provider default

MIN_SECTIONS           = 5
MAX_SECTIONS           = 7
OUTPUT_DIR             = "outputs"
MAX_CONCURRENT_WORKERS = 4