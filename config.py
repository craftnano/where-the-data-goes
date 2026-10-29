"""Project config. Model IDs and prices live here so they're easy to swap."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.resolve()
load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
BRAVE_API_KEY = os.environ["BRAVE_API_KEY"]

MODEL_HAIKU = "claude-haiku-5-5"
MODEL_SONNET = "claude-sonnet-5-5"

# USD per million tokens. Placeholder values — update when real 5.5 pricing is confirmed.
PRICING = {
    MODEL_HAIKU: {"input": 1.00, "output": 5.00},
    MODEL_SONNET: {"input": 3.00, "output": 15.00},
}

# Live crawl uses Sonnet directly — Haiku's first-pass rate was too low to pay off.
# Haiku stays available for archive extraction later.
LIVE_MODEL_SEQUENCE = (MODEL_SONNET,)

MAX_TOOL_CALLS = 8
MAX_TEXT_CHARS = 25_000
SEARCH_RESULTS = 8
HARD_SPEND_CAP_USD = 20.00           # per-run cap
PHASE1_SPEND_CEILING_USD = 50.00     # cumulative cap across all runs for Phase 1
PER_HOST_RPS = 2

# Shared file that persists cumulative dollars spent across every run in the project.
SPEND_LEDGER_PATH = None  # set from DATA_DIR below once that's defined

CONTACT_EMAIL = "dogsofpointroberts@gmail.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

DATA_DIR = ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
SPEND_LEDGER_PATH = DATA_DIR / "spend_ledger.json"
