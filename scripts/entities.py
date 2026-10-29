"""Shared entity-resolution and purpose-classification helpers."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ALIASES_PATH = ROOT / "data" / "aliases.yaml"


# Keyword classifier for purpose_category. Order matters: higher-priority
# matches first (ai_inference beats hosting when both would match, etc.).
_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("ai_inference", [
        "intelligent features", "generative", "llm", "large language",
        "speech-to-text", "speech to text", "text-to-speech", "text to speech",
        "transcription", "voice ai", "openai", "anthropic", "ai service",
        "ai speech", "voice assistant", "machine learning", "ai inference",
    ]),
    ("payments", [
        "payment", "billing", "invoic", "card process", "stripe", "paypal", "braintree",
    ]),
    ("security", [
        "security", "authentication", "authoriz", "sso", "iam", "fraud",
        "ddos", "firewall", "waf", "mfa", "encryption", "identity",
    ]),
    ("communications", [
        "email", "sms", "notification", "voice calling", "video calling",
        "messaging", "chat", "telephony", "communications", "mail delivery",
    ]),
    ("analytics", [
        "analytics", "metrics", "telemetry", "logging", "monitoring",
        "tracking", "survey", "feedback", "reviews", "quality assurance",
        "observability",
    ]),
    ("support", [
        "support", "helpdesk", "help desk", "ticketing", "customer service",
        "contact center", "call center",
    ]),
    ("hosting", [
        "cloud service", "cloud provider", "cloud infrastructure", "hosting",
        "cdn", "content delivery", "data center", "database", "storage",
        "compute", "aws", "azure", "gcp", "google cloud",
    ]),
]


def classify_purpose(purpose_text: str) -> str:
    if not purpose_text or not purpose_text.strip():
        return "unnamed"
    low = purpose_text.lower()
    for category, keywords in _CATEGORY_RULES:
        for kw in keywords:
            if kw in low:
                return category
    return "other"


@lru_cache(maxsize=1)
def _alias_map() -> dict[str, str]:
    """Lowercased raw name -> canonical name."""
    raw = yaml.safe_load(ALIASES_PATH.read_text()) or {}
    out: dict[str, str] = {}
    for canonical, aliases in raw.items():
        out[canonical.lower().strip()] = canonical
        for a in aliases or []:
            out[a.lower().strip()] = canonical
    return out


_SUFFIXES = re.compile(
    r",?\s+(inc\.?|llc\.?|ltd\.?|limited|corp\.?|corporation|gmbh|sarl|s\.a\.|s\.a\.s\.|plc|co\.?|pty\.?|ag|b\.?v\.?)\s*$",
    re.IGNORECASE,
)

# Raw names that collapse to AWS *unless* the purpose text clearly describes
# retail or marketplace activity (in which case the parent Amazon brand likely
# refers to the retail arm, not AWS).
_AWS_CONDITIONAL = {"amazon", "amazon inc. (connect)", "amazon inc (connect)"}
_RETAIL_KEYWORDS = ("retail", "marketplace", "e-commerce", "ecommerce", "fulfillment", "shopping")

_SEED_ALIASES = {
    "alphabet inc. (class a)": "Alphabet",
    "alphabet inc. (class c)": "Alphabet",
}

# When a seed is Meta and the entry text matches one of these patterns, treat it
# as a Meta affiliate (edge to the "Meta affiliates" sentinel) rather than
# dropping it as a self-reference. Context-dependent: only applied when the seed
# itself is Meta — if some other company lists Facebook as a sub-processor, that
# edge still goes to a plain Facebook / Meta Platforms node, not Meta affiliates.
# Broad: any string that begins with "Meta " followed by a word (catches Meta
# Canada Ltd, Meta Operations LLC, Meta Platforms Ireland, Meta Platforms Inc.,
# Meta France Sarl, etc.) plus the explicit sibling-brand names. Applied only
# when the seed itself is Meta, so false positives from unrelated "Meta Agency"
# style names in other companies' lists are gated out by context.
_META_AFFILIATE_RE = re.compile(
    r"(^meta\s+\w|^meta,|^meta$|\bfacebook\b|\binstagram\b|\bwhatsapp\b|\boculus\b|\breality\s+labs\b|\bnovi\b)",
    re.IGNORECASE,
)


def is_meta_affiliate(raw_name: str) -> bool:
    return bool(_META_AFFILIATE_RE.search(raw_name or ""))


def canonicalize_seed(name: str) -> str:
    """Canonical name for a seed company (from the seeds CSV). Collapses dual-class
    tickers like Alphabet Inc. (Class A/C) that reference the same parent.
    """
    if not name:
        return name
    return _SEED_ALIASES.get(name.strip().lower(), name.strip())


def canonicalize(raw_name: str, purpose_text: str = "") -> str:
    if not raw_name:
        return raw_name
    key = raw_name.strip().lower()
    purpose = (purpose_text or "").lower()

    # Honor the AWS collapse exception: if the raw name is one of the ambiguous
    # Amazon strings and the purpose is clearly retail/marketplace, don't fold
    # into AWS — keep the raw brand so the retail node remains distinct.
    if key in _AWS_CONDITIONAL and any(kw in purpose for kw in _RETAIL_KEYWORDS):
        return raw_name.strip()

    m = _alias_map()
    if key in m:
        return m[key]
    # Try again after stripping a trailing legal-entity suffix.
    stripped = _SUFFIXES.sub("", raw_name.strip()).strip()
    if stripped and stripped.lower() in m:
        return m[stripped.lower()]
    return stripped or raw_name.strip()
