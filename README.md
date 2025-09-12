# FlightRadarUCM
This repository is used for creating a pipeline to the Flight Radar API and transforming data to be used with a chatbot. This is a lightweight FastAPI service that answers natural-language questions about flights using the FlightRadar24 API (via fr24sdk). It’s designed to run locally for development and as a container in Azure Container Apps for production. Most of the logic in this app is tailored for the TPA, MIA, MAD, XPL, and BCN airports. If you want another airport, you will have to update the logic in the bot.py file.

## Purpose of the app

1. Natural-language questions like:

    - arrivals TPA, departures from XPL top 5
    - UA2476 summary
    - AM1740 events
    - Clean, user-friendly replies like "AA2401 arrived at KTPA on September 10, 2025 at 2:12 AM"

2. “En route” vs “arrived” logic: If a live row has no landing time, we provide say that the flight is en route flight summary. The estimated time of arrival logic is not built in on this app as of this latest release. Moreover, the logic works as follows: 

    - If an aircraft does not have the a landed time, the app will show “en route to …”.
    - If a landing time is available, the app will display 

3. Readable times with timezone support:

    - If you pass ?tz=America/New_York, we format in that timezone. Otherwise we fall back to a best-guess airport timezone for a few airports (XPL, MIA, TPA, MAD, BCN), then UTC.

4. Callsign to Flight number mapping:

    - Converts ICAO callsigns like AAL2401 → IATA AA2401 for user-friendly display. You can find this on the ICAO_TO_IATA function in the bot.py module. If an airline is not included in here, we will receive a "status unknown" message on the bot response. If you add the missing airline to the ICAO_TO_IATA function mapping, you will receive a normal response with the arrivals, departure, flight summary, and events.  

5. Events (gate departure, takeoff, cruising, FIR transitions, etc.):

    - We first resolve the fr24_id via Flight Summary, then fetch the historic events.

## Key Modules

    1. Source/Jobs/app_main.py – FastAPI app and endpoints.

    2. Source/Jobs/bot.py – Orchestration, NLU glue, formatting, intent handlers.

    3. Source/Jobs/fr24_tools.py – Thin, robust wrappers around fr24sdk (+ helpers).

    4. Source/Jobs/natural_language.py – Very small parser that extracts intent and slots.

Though it is not a key module, the flight_radar_api_connection.ipynb can be used for testing the flight radar 24 API and inspecting the JSON response. This can be used for debugging or optimizing the key modules above so that you receive an expected response from the bot. 

## Requirements

- Python 3.11+
- A FlightRadar24 API key (keep it secret).
- (Optional) Docker if you want to containerize locally.
- (Optional) Azure Container Registry and Azure Container Apps for cloud deploys.

## Configuration

The service reads your API key from the environment: FR24_API_TOKEN=<your FR24 API key>

## Timezone

If the caller provides ?tz=... (e.g., America/New_York), we format times there. Otherwise, the bot is programmed to give the arrivals and departures in the airport's local timezone. For now, this feature is only available for the following ariports: 

XPL / MIA / TPA / MAD / BCN

If an airport is not added to the AIRPORT_TZ function in bot.py, we fall back to UTC.

To add more airports, extend the airport timezone map in bot.py (look for AIRPORT_TZ) or where _best_tz() is defined.

## Run Locally 

Create your virtual environment: 

```bash
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
pip install -r requirements.txt
```

Start the bot: 

```bash
uvicorn Source.Jobs.app_main:app --reload --port 8000 --log-level info
```

Open the docs: http://localhost:8000/docs

Example Calls: 

Arrivals: 
```bash
curl -X POST "http://localhost:8000/ask" \
  -H "Content-Type: application/json" \
  -d '{"question":"arrivals TPA"}'
```

Events: 
```bash
curl -X POST "http://localhost:8000/ask" \
  -H "Content-Type: application/json" \
  -d '{"question":"AM1740 events"}'
```
