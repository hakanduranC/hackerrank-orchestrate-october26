"""Shared identifier patterns and file-type sets, defined once.

Resource ids, email addresses and image suffixes are matched in several places
(parsers, portfolio, agent tools); keeping one definition prevents the copies from
drifting apart.
"""

from __future__ import annotations

import re

RESOURCE_ID_RE = re.compile(
    r"\b(?:account|search|support|analytics|export|data_flow|backup|audit_record|"
    r"tombstone|legal_hold|cache|marketing|email_delivery|object)_\d+\b"
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
