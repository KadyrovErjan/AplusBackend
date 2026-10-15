import requests
import os
import json
from datetime import datetime
from dotenv import load_dotenv
import time

load_dotenv()

PILOT_API_URL = os.getenv("PILOT_API_URL") or "https://pilot.incomtek.kg/api/api.php"
PILOT_LOGIN = os.getenv("PILOT_LOGIN")
PILOT_PASSWORD = os.getenv("PILOT_PASSWORD")

TEST_IMEI = "353691842922204" 

DATE_START = "2025-12-26 09:00:00"
DATE_END = "2025-12-26 18:00:00"


def get_timestamp(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    return int(dt.timestamp())


def check_pilot():
    start_ts = get_timestamp(DATE_START)
    end_ts = get_timestamp(DATE_END)

    print(f"🔍 Checking IMEI: {TEST_IMEI}")
    print(f"🕒 Period: {DATE_START} ({start_ts}) - {DATE_END} ({end_ts})")
    print("-" * 50)

    print("👉 REQUEST 1: cmd=istatus (Statistics)")
    params_stat = {
        "cmd": "istatus",
        "node": 1,
        "imei": TEST_IMEI,
        "start": start_ts,
        "stop": end_ts
    }

    try:
        resp = requests.get(PILOT_API_URL, params=params_stat, auth=(PILOT_LOGIN, PILOT_PASSWORD))
        data = resp.json()
        print(json.dumps(data, indent=4, ensure_ascii=False))

        motorhours = data.get("motorhours")
        print(f"\n📢 MOTORHOURS FIELD: {motorhours}")

    except Exception as e:
        print(f"❌ Error: {e}")

    print("-" * 50)

    print("👉 REQUEST 2: cmd=run (Tracks)")
    params_run = {
        "cmd": "run",
        "node": 1,
        "imei": TEST_IMEI,
        "start": start_ts,
        "stop": end_ts,
        "zones": 0 
    }

    try:
        resp = requests.get(PILOT_API_URL, params=params_run, auth=(PILOT_LOGIN, PILOT_PASSWORD))
        data = resp.json()
        print("Keys found:", data.keys())
        if "msg" in data: print("Msg:", data["msg"])

        tracks = data.get("tracks", {})
        print(f"Tracks found: {len(tracks)}")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    check_pilot()