from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from .natural_language import parse_query
from .fr24_tools import (resolve_airport, live_flights, flight_summary, flight_events, line_for_leg, enrich_with_summary_time, format_time_local)
import re

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


# ICAO->IATA mapping for common carriers.
ICAO_TO_IATA = {
    "AAL": "AA",  
    "UAL": "UA",  
    "DAL": "DL",
    "SWA": "WN",
    "JBU": "B6",
    "ASA": "AS",
    "FFT": "F9",
    "NKS": "NK",
    "SKQ": "OO",  
    "SKW": "OO",
    "ASH": "YX",  
    "RPA": "YX",
    "EDV": "9E",
    "ENY": "MQ",
    "QXE": "QX",
    "UPS": "5X",
    "ACA": "AC",

    # --- SPAIN: majors, regionals, leisure/charter (passenger) ---
    "IBE": "IB",  # Iberia :contentReference[oaicite:0]{index=0}
    "IBS": "I2",  # Iberia Express :contentReference[oaicite:1]{index=1}
    "VLG": "VY",  # Vueling :contentReference[oaicite:2]{index=2}
    "AEA": "UX",  # Air Europa :contentReference[oaicite:3]{index=3}
    "ANE": "YW",  # Air Nostrum (Iberia Regional) :contentReference[oaicite:4]{index=4}
    "VOE": "V7",  # Volotea :contentReference[oaicite:5]{index=5}
    "EVE": "E9",  # Iberojet


    # Canary Islands carriers
    "IBB": "NT",  # Binter Canarias :contentReference[oaicite:6]{index=6}
    "CNF": "PM",  # Canaryfly :contentReference[oaicite:7]{index=7}

    # Leisure / long-haul charter
    "PLM": "EB",  # Wamos Air (ex-Pullmantur Air) :contentReference[oaicite:8]{index=8}
    "EVE": "E9",  # Iberojet (ex-Evelop) :contentReference[oaicite:9]{index=9}
    "WFL": "2W",  # World2Fly :contentReference[oaicite:10]{index=10}
    "PUE": "PU",  # Plus Ultra Líneas Aéreas :contentReference[oaicite:11]{index=11}

    # Regionals / ACMI / cargo with pax callsigns sometimes seen in FR24
    "SWT": "WT",  # Swiftair (mainly cargo, but appears in feeds) :contentReference[oaicite:12]{index=12}
    "LAV": "AP",  # Albastar (charter) :contentReference[oaicite:13]{index=13}
    "PVG": "P6",  # Privilege Style (ACMI/charter) :contentReference[oaicite:14]{index=14}
    "HAT": "HT",  # Air Horizont (charter; HQ split MT/ES, commonly seen in Spain) :contentReference[oaicite:15]{index=15}

    # Air Europa’s regional brand (operated as Aeronova)
    "OVA": "X5",  # Air Europa Express (Aeronova) :contentReference[oaicite:16]{index=16}

    # Other European major airlines
    "BAW": "BA",  # British Airways
    "VIR": "VS",  # Virgin Atlantic
    "DLH": "LH",  # Lufthansa
    "AFR": "AF",  # Air France
    "KLM": "KL",  # KLM
    "SWR": "LX",  # SWISS
    "TAP": "TP",  # TAP Air Portugal
    "ITY": "AZ",  # ITA Airways
    "THY": "TK",  # Turkish Airlines
    "QTR": "QR",  # Qatar Airways (MIA/MAD)
    "UAE": "EK",  # Emirates (MIA/MAD)
    "EZY": "U2",  # easyJet (MAD)
    "RYR": "FR",  # Ryanair (MAD)
    "WZZ": "W6",  # Wizz Air (MAD)
    "LOT": "LO",  # LOT Polish (MIA; occasionally MAD long-haul)

    #Latin America majors
    "AVA": "AV",  # Avianca
    "CMP": "CM",  # Copa Airlines
    "AMX": "AM",  # Aeroméxico
    "ARG": "AR",  # Aerolíneas Argentinas
    "LPE": "LP",  # LATAM Peru (still appears; maps to LA brand)
    "LAN": "LA",  # LATAM Airlines (Chile)
    "TAM": "LA",  # LATAM Brasil (legacy TAM)
    "GLO": "G3",  # GOL Linhas Aéreas (often MIA)
    "AZU": "AD",  # Azul Brazilian (occasionally MIA)
    "BHS": "UP",  # Bahamasair
    "CAY": "KX",  # Cayman Airways
    "BWA": "BW",  # Caribbean Airlines
    "DWI": "DM",  # Arajet (MIA)
}


_callsign_icao_re = re.compile(r"^([A-Z]{3})(\d+)$")

def _best_tz(user_tz: Optional[str], airport_code: Optional[str]) -> Optional[str]:
    """Prefer explicit user tz; else use airport tz; else None (UTC fallback)."""
    if user_tz:
        return user_tz
    if airport_code:
        return AIRPORT_TZ.get(airport_code.strip().upper())
    return None

def callsign_to_iata_flight(callsign: str | None) -> str | None:
    """
    'AAL2401' -> 'AA2401' using ICAO->IATA mapping.
    Returns None if pattern not recognized or mapping unknown.
    """
    if not callsign:
        return None
    m = _callsign_icao_re.match(callsign.strip().upper())
    if not m:
        return None
    icao, num = m.groups()
    iata = ICAO_TO_IATA.get(icao)
    return f"{iata}{num}" if iata else None

def _sort_leg_most_recent(leg, _first):
    # choose latest landed; else latest takeoff/seen
    def _parse_iso(dt_str):
        if not isinstance(dt_str, str):
            return None
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except Exception:
            return None

    return (
        _parse_iso(_first(leg, ["datetime_landed", "datetime_landing"]))
        or _parse_iso(_first(leg, ["datetime_takeoff", "first_seen", "last_seen"]))
        or datetime.min.replace(tzinfo=timezone.utc)
    )


def _parse_iso(dt_str):
    """Parse 'YYYY-MM-DDTHH:MM:SSZ' or with offset to a datetime; None on failure."""
    if not isinstance(dt_str, str):
        return None
    try:
        s = dt_str.replace("Z", "+00:00")  # Python doesn't parse 'Z' directly
        return datetime.fromisoformat(s)
    except Exception:
        return None

def _resolve_codes_tuple(code_or_name: str):
    info = resolve_airport(code_or_name)
    iata = _first(info, ["iata", "iata_code", "IATA"])
    icao = _first(info, ["icao", "icao_code", "ICAO"])
    return iata, icao


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

def _ensure_iata_or_icao(code_or_name: str) -> str:
    """If a 3-letter IATA code is given, use it; otherwise resolve and prefer IATA then ICAO."""
    s = code_or_name.strip()
    if len(s) == 3 and s.isalpha():
        return s.upper()
    info = resolve_airport(code_or_name)  # SDK object/dict
    iata = _first(info, ["iata", "IATA"])
    icao = _first(info, ["icao", "ICAO"])
    return (iata or icao or s).upper()

def answer(user_text: str, tz: Optional[str] = None) -> str:
    """Main brain. tz is an IANA timezone string (or None -> UTC)."""
    q = parse_query(user_text)
    user_tz = tz  # <-- use the tz passed from the endpoint

    # HELP
    if q.intent == "help":
        return ("Try:\n"
                "• arrivals at XPL\n"
                "• departures from XPL top 5\n"
                "• AA3165 summary\n"
                "• AA3165 events")

    # AIRPORT LIVE
    elif q.intent == "airport_live" and q.airport:
        code = _ensure_iata_or_icao(q.airport)
        tz_str = _best_tz(getattr(q, "tz", None), code)  # <- pick timezone once

        flights = live_flights(code, q.direction, limit=q.limit) or []
        if not flights:
            return f"No {q.direction} flights found for {code}."

        lines = []
        for leg in flights[:q.limit]:
            leg = enrich_with_summary_time(leg)
            lines.append("• " + line_for_leg(leg, q.direction or "both", tz_str))  # <- no default_airport kwarg
        return f"Live {q.direction} flights for {code} (top {min(q.limit, len(lines))}):\n" + "\n".join(lines)

    # FLIGHT SUMMARY
    elif q.intent == "flight_summary" and q.flight_id:
        now = datetime.now(timezone.utc)
        fs = flight_summary(q.flight_id, now - timedelta(days=2), now + timedelta(days=1)) or []
        if not fs:
            return f"I couldn't find a summary for {q.flight_id}."

        legs = fs

        # Optional filter by airport/direction (e.g. “arriving at TPA”)
        if q.airport:
            iata, icao = _resolve_codes_tuple(q.airport)
            targets = {c for c in (iata, icao, q.airport.upper()) if c}

            def _matches_leg(leg):
                orig = _first(leg, ["orig_icao", "orig"])
                dest = _first(leg, ["dest_icao", "dest"])
                if q.direction == "inbound":
                    return dest in targets
                if q.direction == "outbound":
                    return orig in targets
                return (dest in targets) or (orig in targets)

            filtered = [leg for leg in legs if _matches_leg(leg)]
            if filtered:
                legs = filtered

        def _parse_iso(dt_str):
            if not isinstance(dt_str, str):
                return None
            try:
                return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except Exception:
                return None

        def _sort_key(leg):
            return (
                _parse_iso(_first(leg, ["datetime_landed", "datetime_landing"]))
                or _parse_iso(_first(leg, ["datetime_takeoff", "first_seen", "last_seen"]))
                or datetime.min.replace(tzinfo=timezone.utc)
            )

        # pick most relevant leg
        s = sorted(legs, key=_sort_key)[-1]

        # timezone to format with (user’s if provided, else airport)
        dest_code = _first(s, ["dest_icao", "dest", "to_icao", "to"])
        tz_str = _best_tz(getattr(q, "tz", None), dest_code)

        # pretty line
        return line_for_leg(s, q.direction or "both", tz_str)


    elif q.intent == "flight_events" and q.flight_id:
    # 1) Find most relevant recent leg for this flight code
        now = datetime.now(timezone.utc)
        fs = flight_summary(q.flight_id, now - timedelta(days=3), now + timedelta(days=1)) or []
        if not fs:
            return f"I couldn’t find recent events for {q.flight_id}."

        def _parse_iso(dt_str):
            if not isinstance(dt_str, str):
                return None
            try:
                return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except Exception:
                return None

        def _sort_key(leg):
            return (
                _parse_iso(_first(leg, ["datetime_landed", "datetime_landing"]))
                or _parse_iso(_first(leg, ["datetime_takeoff", "first_seen", "last_seen"]))
                or datetime.min.replace(tzinfo=timezone.utc)
            )

        s = sorted(fs, key=_sort_key)[-1]

        # 2) Pull the FR24 flight id
        fr24_id = _first(s, ["fr24_id", "fr24Id", "id"])
        if not fr24_id:
            return f"I couldn’t resolve a flight id for {q.flight_id}."

        # 3) Pick a timezone (user > dest airport > UTC)
        dest_code = _first(s, ["dest_icao", "dest", "to_icao", "to"])
        tz_str = _best_tz(getattr(q, "tz", None), dest_code)

        # 4) Fetch events for that FR24 id
        ev_containers = flight_events(fr24_id, event_types=("all",)) or []
        container = ev_containers[0] if ev_containers else {}
        events_list = _first(container, ["events"], []) or []
        if not events_list:
            return f"No events found for {q.flight_id}."

        # 5) Format a friendly list
        lines = []
        for item in events_list[: q.limit]:
            et = (_first(item, ["type"]) or "event").replace("_", " ")
            ts = _first(item, ["timestamp"])
            # pick the most useful detail if present
            d = _first(item, ["details"], {}) or {}
            det = _first(d, ["gate_ident", "takeoff_runway", "landing_runway", "entered_airspace", "exited_airspace"]) or ""
            ts_txt = format_time_local(ts, tz_str)
            lines.append(f"• {ts_txt}: {et}{f' – {det}' if det else ''}")

        return f"Recent events for {q.flight_id}:\n" + "\n".join(lines)

    # DEFAULT
    return "Sorry, I didn't understand that."