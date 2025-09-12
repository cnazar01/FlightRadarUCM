
# Minimal rule-based NLU for flight Q&A
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Literal, Optional

Intent = Literal["airport_live", "flight_summary", "flight_events", "help"]

# map words -> direction
DIRECTION_MAP = {
    "arrivals": "inbound", "arrival": "inbound", "arriving": "inbound", "landing": "inbound",
    "departures": "outbound", "departure": "outbound", "departing": "outbound", "takeoff": "outbound",
}

IATA3 = re.compile(r"\b([A-Z]{3})\b")

@dataclass
class Query:
    intent: Intent
    airport: Optional[str] = None      # JFK, XPL, or name like "Kennedy"
    direction: str = "both"            # inbound | outbound | both
    flight_id: Optional[str] = None    # e.g. AA3165
    limit: int = 10

def parse_query(text: str) -> Query:
    # direction
    direction = "both"
    tl = text.lower()
    for k, v in DIRECTION_MAP.items():
        if k in tl:
            direction = v
            break

    # limit (e.g., "limit 5", "top 8")
    m_lim = re.search(r"\b(?:limit|top)\s+(\d{1,2})\b", tl)
    limit = int(m_lim.group(1)) if m_lim else 10
    limit = max(1, min(limit, 50))

    # flight id (e.g., AA3165, CM2385)
    m_fid = re.search(r"\b([A-Z]{2}\d{2,4}[A-Z]?)\b", text.upper())

    # airport by code right after a preposition (preferred)
    m_iata_after_prep = re.search(r"(?:\bat|\bfrom|\bto|\bin)\s+([A-Z]{3})\b", text.upper())
    airport = m_iata_after_prep.group(1) if m_iata_after_prep else None

    if not airport:
        # fallback: any 3-letter code, filter out common false positives
        candidates = re.findall(r"\b([A-Z]{3})\b", text.upper())
        blacklist = {"EXE", "TXT", "PNG", "JPG", "PDF", "DOC", "VSC", "PS", "BAT"}
        candidates = [c for c in candidates if c not in blacklist]
        airport = candidates[0] if candidates else None

    # final intent selection
    if m_fid:
        intent = "flight_events" if "event" in tl else "flight_summary"
        return Query(intent=intent,
                     flight_id=m_fid.group(1),
                     airport=airport,            # <- keep it
                     direction=direction,        # <- keep it
                     limit=limit)

    return Query(intent="airport_live" if airport else "help",
                 airport=airport, direction=direction, limit=limit)






