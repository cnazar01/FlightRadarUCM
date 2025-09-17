# Source/Jobs/fr24_tools.py
from __future__ import annotations
import os
import re
from typing import Literal, Sequence, Any, Iterable
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv, find_dotenv
from fr24sdk.client import Client
from fr24sdk.exceptions import AuthenticationError, BadRequestError
from functools import lru_cache


# Load environment once (local dev)
load_dotenv(find_dotenv(), override=False)

Direction = Literal["inbound", "outbound", "both"]

AIRPORT_TZ = {
    # Honduras – Comayagua / Palmerola
    "XPL":  "America/Tegucigalpa",
    "MHPR": "America/Tegucigalpa",

    # USA – Florida
    "MIA":  "America/New_York",
    "KMIA": "America/New_York",
    "TPA":  "America/New_York",
    "KTPA": "America/New_York",

    # Spain
    "MAD":  "Europe/Madrid",
    "LEMD": "Europe/Madrid",
    "BCN":  "Europe/Madrid",
    "LEBL": "Europe/Madrid",
}


IATA_FLIGHT_RE = re.compile(r'^[A-Z]{2}\d{1,4}[A-Z]?$')
ICAO_CALLSIGN_RE = re.compile(r'^[A-Z]{3}\d+$')


# ---------- helpers ----------

@lru_cache(maxsize=2048)
def _to_iata(code: str | None) -> str | None:
    """
    Return a 3-letter IATA code for an airport code.
    - If it's already 3 letters -> return as-is.
    - If it's 4-letter ICAO -> resolve to IATA via FR24 airport lookup.
    - Fallback: return the original code.
    """
    if not code:
        return None
    s = str(code).strip().upper()
    if len(s) == 3:
        return s
    if len(s) == 4:
        try:
            info = resolve_airport(s)   # uses FR24 SDK
            iata = _first(info, ["iata", "iata_code", "IATA"])
            return (iata or s)
        except Exception:
            return s
    return s

# Local timezones for airports you care about (IATA and ICAO)

def flight_status(leg) -> tuple[str, str | None]:
    """
    Return (status, when_iso) where status is one of:
      - 'arrived'   -> landed/arrival time available
      - 'enroute'   -> departed but not landed yet (flight_ended False or no landed time)
      - 'scheduled' -> no reliable movement time; fall back to arrival/takeoff if present
    """
    d = _as_dict(leg)
    landed  = d.get("datetime_landed") or d.get("datetime_landing") or d.get("datetime_arrival")
    takeoff = d.get("datetime_takeoff") or d.get("first_seen")
    ended   = d.get("flight_ended")

    if landed:
        return "arrived", landed
    if ended is False or (takeoff and not landed):
        # clearly in the air or still operating
        return "enroute", takeoff
    # last resort: scheduled/unknown
    return "scheduled", (d.get("datetime_arrival") or takeoff)


def _first(obj: Any, names: Iterable[str], default=None):
    """Safely read first available field from SDK model or dict."""
    for n in names:
        if isinstance(obj, dict):
            if (v := obj.get(n)) not in (None, "", []):
                return v
        else:
            if (v := getattr(obj, n, None)) not in (None, "", []):
                return v
    return default

def _parse_iso(dt: str | None) -> datetime | None:
    if not dt:
        return None
    dt = dt.replace("Z", "+00:00")  # allow 'Z'
    try:
        return datetime.fromisoformat(dt)
    except Exception:
        return None


def _safe_zoneinfo(tzname: str | None):
    """
    Return a tzinfo. Try ZoneInfo(tzname) and common UTC aliases; if none
    are available (e.g., Windows without tzdata), fall back to timezone.utc.
    """
    # user-provided tz first
    if tzname:
        try:
            return ZoneInfo(tzname)
        except Exception:
            pass
    # then a few UTC aliases
    for candidate in ("UTC", "Etc/UTC", "GMT", "Etc/GMT"):
        try:
            return ZoneInfo(candidate)
        except Exception:
            continue
    # last resort
    return timezone.utc

def format_time_local(iso_or_dt, tz: str | None = None) -> str:
    """
    Accept an ISO string (with optional 'Z') or a datetime; format in tz.
    Example -> 'August 30, 2025 at 04:19 PM'
    """
    if iso_or_dt is None:
        return "time unknown"

    if isinstance(iso_or_dt, datetime):
        dt = iso_or_dt
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = _parse_iso(str(iso_or_dt))
        if not dt:
            return "time unknown"

    zone = _safe_zoneinfo(tz)
    dt_local = dt.astimezone(zone)
    return dt_local.strftime("%B %d, %Y at %I:%M %p").lstrip("0").replace(" 0", " ")


def _field(rec, *names):
    """
    Read first non-empty field from either a dict or a Pydantic model.
    Tries attribute access, then model_dump()/dict() if available.
    """
    for n in names:
        val = None
        if isinstance(rec, dict):
            val = rec.get(n, None)
        else:
            # attribute access
            if hasattr(rec, n):
                val = getattr(rec, n, None)
            # pydantic v2
            if val in (None, "", []) and hasattr(rec, "model_dump"):
                try:
                    val = rec.model_dump().get(n)
                except Exception:
                    pass
            # pydantic v1
            if val in (None, "", []) and hasattr(rec, "dict"):
                try:
                    val = rec.dict().get(n)
                except Exception:
                    pass

        if val not in (None, "", []):
            return val
    return None

def flight_id(rec) -> str:
    """
    Prefer canonical IATA 'flight' (e.g. 'TO4780').
    Fall back to callsign; if callsign looks ICAO, try mapping to IATA.
    """
    f = (_field(rec, "flight") or "").strip().upper()
    if f and IATA_FLIGHT_RE.match(f):
        return f

    cs = (_field(rec, "callsign") or "").strip().upper()
    if cs:
        if ICAO_CALLSIGN_RE.match(cs):
            try:
                from .bot import callsign_to_iata_flight
                mapped = callsign_to_iata_flight(cs)
                if mapped:
                    return mapped
            except Exception:
                pass
        return cs

    return "Flight"


def line_for_leg(leg, direction: str, tz: str | None) -> str:
    """
    Friendly one-liner using actual movement status, showing airports in IATA.
    """
    fid  = flight_id(leg)

    # read whatever is present, then prefer IATA
    orig_any = _field(leg, "orig_icao", "orig", "from_icao", "from") or "?"
    dest_any = _field(leg, "dest_icao", "dest", "to_icao", "to") or "?"
    orig = _to_iata(orig_any) or orig_any or "?"
    dest = _to_iata(dest_any) or dest_any or "?"

    direction = (direction or "both").lower()

    status, when_iso = flight_status(leg)
    when_txt = format_time_local(when_iso, tz) if when_iso else None

    if direction in ("inbound", "arrivals"):
        if status == "arrived":
            return f"{fid} arrived at {dest}" + (f" at {when_txt}" if when_txt else "")
        if status == "enroute":
            return f"{fid} en route to {dest}"
        return f"{fid} arriving at {dest}"

    if direction in ("outbound", "departures"):
        if status == "arrived":
            return f"{fid} arrived at {dest}" + (f" at {when_txt}" if when_txt else "")
        if status == "enroute":
            return f"{fid} departing from {orig}" + (f" at {when_txt}" if when_txt else "")
        return f"{fid} departing from {orig}"

    # both/mixed
    if status == "arrived":
        return f"{fid} arrived at {dest}" + (f" at {when_txt}" if when_txt else "")
    if status == "enroute":
        return f"{fid} en route to {dest}"
    return f"{fid} arriving at {dest}"


def _as_dict(obj):
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    # last resort: best-effort attribute scrape
    return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_")}

def _best_time_key(rec, _first):
    # choose most recent landed; else takeoff/seen to sort
    def _p(s):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    return (
        _p(_first(rec, ["datetime_landed", "datetime_landing"])) or
        _p(_first(rec, ["datetime_arrival"])) or
        _p(_first(rec, ["datetime_takeoff", "first_seen", "last_seen"])) or
        datetime.min.replace(tzinfo=timezone.utc)
    )

def flight_summary_by_callsign(
    callsign: str,
    dt_from: datetime | None = None,
    dt_to: datetime | None = None,
):
    """
    Query FR24 flight summary using a callsign (e.g., 'TVF36TM').
    The response will include the canonical IATA 'flight' code (e.g., 'TO4780').
    """
    try:
        with _client() as c:
            return c.flight_summary.get_light(
                callsigns=[callsign],
                flight_datetime_from=_fmt(dt_from),
                flight_datetime_to=_fmt(dt_to),
            ).data
    except BadRequestError as e:
        # Keep the same style as flight_summary(...)
        raise ValueError(
            "Flight summary (by callsign) needs a time window like "
            "'from 2025-09-01T00:00:00 to 2025-09-02T00:00:00' (UTC)."
        ) from e

def flight_summary_dicts(flight_id_str: str,
                         dt_from: datetime | None = None,
                         dt_to: datetime | None = None) -> list[dict]:
    """Same as flight_summary but guarantees dicts in the return value."""
    with _client() as c:
        items = c.flight_summary.get_light(
            flights=[flight_id_str],
            flight_datetime_from=_fmt(dt_from),
            flight_datetime_to=_fmt(dt_to),
        ).data
    return [_as_dict(x) for x in items]

def enrich_with_summary_time(leg):
    """
    If the live row lacks landing/arrival time, or only has a callsign,
    fetch a recent summary (prefer by callsign) and merge useful fields.
    Also copy the canonical IATA 'flight' from summary when available.
    """
    d = _as_dict(leg)

    # If we already have a usable time, we still might fix the flight number below,
    # so don't early-return yet. We will attempt to merge regardless.

    # Choose identifiers
    live_flight = (d.get("flight") or "").strip().upper()
    live_callsign = (d.get("callsign") or "").strip().upper()

    # Decide what to query:
    # Prefer callsign if that's what we have (live endpoint usually gives callsign).
    use_callsign = bool(live_callsign)
    use_iata_flight = bool(live_flight and IATA_FLIGHT_RE.match(live_flight))

    now = datetime.now(timezone.utc)
    rows = []

    try:
        if use_callsign:
            rows = flight_summary_by_callsign(live_callsign,
                                              now - timedelta(days=2),
                                              now + timedelta(days=1)) or []
        elif use_iata_flight:
            rows = flight_summary(live_flight,
                                  now - timedelta(days=2),
                                  now + timedelta(days=1)) or []
    except Exception:
        rows = []

    if not rows:
        return d

    # pick the most relevant leg from summary
    best = sorted(rows, key=lambda r: _best_time_key(r, _first))[-1]

    # merge useful fields (do not overwrite non-empty values in d)
    def _merge(k):
        v = _first(best, [k])
        if v and not d.get(k):
            d[k] = v

    for k in (
        "datetime_landed", "datetime_landing", "datetime_arrival",
        "datetime_takeoff", "orig_icao", "orig", "orig_iata",
        "dest_icao", "dest", "dest_iata",
        "flight", "callsign", "flight_ended"
    ):
        _merge(k)

    # If the summary returned a canonical IATA 'flight', prefer it over callsign
    summary_flight = (_first(best, ["flight"]) or "").strip().upper()
    if summary_flight and IATA_FLIGHT_RE.match(summary_flight):
        d["flight"] = summary_flight

    return d



# ---------- client factory ----------

def _get_token() -> str:
    token = os.getenv("FR24_API_TOKEN")
    if not token:
        raise RuntimeError(
            "FR24_API_TOKEN not found. Add it to your environment or to a .env file at the repo root."
        )
    return token


def _client() -> Client:
    key = _get_token()
    try:
        return Client(key)           # most releases
    except TypeError:
        return Client(api_key=key)   # some releases


# ---------- public API used by bot.py ----------

def resolve_airport(code_or_name: str):
    """Return an airport object/dict from the SDK."""
    with _client() as c:
        obj = c.airports.get_light(code_or_name)
    if hasattr(obj, "data"):
        return obj.data
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return obj


def live_flights(airport: str, direction: Direction = "both", limit: int = 20):
    airport = airport.strip().upper()
    direction = (direction or "both").strip().lower()

    if direction not in {"inbound", "outbound", "both"}:
        raise ValueError("direction must be 'inbound' | 'outbound' | 'both'")

    airports = (
        [f"inbound:{airport}", f"outbound:{airport}"]
        if direction == "both"
        else [f"{direction}:{airport}"]
    )

    try:
        with _client() as c:
            items = c.live.flight_positions.get_light(airports=airports, limit=limit).data
        return [_as_dict(x) for x in items]   # <- normalize
    except AuthenticationError as e:
        raise RuntimeError("FlightRadar24 auth failed. Check FR24_API_TOKEN.") from e


def _fmt(dt: datetime | None) -> str | None:
    """
    FR24 endpoints expect 'YYYY-MM-DDTHH:MM:SS' in UTC (no microseconds, no 'Z').
    Return None if dt is None.
    """
    if dt is None:
        return None
    # ensure UTC, strip microseconds & tzinfo
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(microsecond=0, tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def flight_summary(
    flight_id: str,
    dt_from: datetime | None = None,
    dt_to: datetime | None = None,
):
    try:
        with _client() as c:
            return c.flight_summary.get_light(
                flights=[flight_id],
                flight_datetime_from=_fmt(dt_from),
                flight_datetime_to=_fmt(dt_to),
            ).data
    except BadRequestError as e:
        raise ValueError(
            "Flight summary needs a time window like "
            "'from 2025-09-01T00:00:00 to 2025-09-02T00:00:00' (UTC)."
        ) from e


def flight_events(flight_id: str, event_types: Sequence[str] = ("all",)):
    with _client() as c:
        return c.historic.flight_events.get_light(
            flight_ids=[flight_id], event_types=list(event_types)
        ).data
