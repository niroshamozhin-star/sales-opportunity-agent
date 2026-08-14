"""fetch_and_enrich_oos_data.py - pulls active 2026 FMCSA OOS records from the
live API, dedupes by carrier, enriches with city/state via MCMIS, saves to CSV."""

import requests
import pandas as pd
import time

OOS_API_URL = "https://data.transportation.gov/resource/p2mt-9ige.json"
MCMIS_API_URL = "https://data.transportation.gov/resource/az4n-8mr2.json"
OUTPUT_FILE = "../data/OOS_Enriched_2026.csv"  # script lives in src/, one folder below project root

PAGE_SIZE = 5000
ENRICH_BATCH_SIZE = 100
MAX_RETRIES = 5


def fetch_oos_data():
    """Pulls all matching OOS records from the live API, paginated by offset."""
    print("Fetching OOS data from the live API...")

    where_clause = (
        "status='ACTIVE' AND rescind_date IS NULL "
        "AND oos_date >= '2026-01-01' AND oos_date < '2027-01-01'"
    )

    all_rows = []
    offset = 0

    while True:
        params = {
            "$select": "dot_number,legal_name,oos_date,oos_reason",
            "$where": where_clause,
            "$limit": PAGE_SIZE,
            "$offset": offset,
        }
        response = requests.get(OOS_API_URL, params=params)
        response.raise_for_status()
        page = response.json()

        if not page:
            break

        all_rows.extend(page)
        print(f"  fetched {len(all_rows)} rows so far...")
        offset += PAGE_SIZE

    df = pd.DataFrame(all_rows)
    print(f"Total rows fetched before de-duplication: {len(df)}")
    return df


def dedupe_by_carrier(df):
    """Keeps one row per carrier (dot_number), the most recent oos_date if duplicated."""
    # Numeric conversion avoids "169538" vs " 169538" being treated as different carriers.
    df["dot_number"] = pd.to_numeric(df["dot_number"], errors="coerce")
    df["oos_date"] = pd.to_datetime(df["oos_date"])

    df = df.sort_values("oos_date", ascending=False)
    df = df.drop_duplicates(subset="dot_number", keep="first")

    print(f"Total rows after de-duplication: {len(df)}")
    return df


def enrich_with_location(dot_numbers):
    """Looks up city/state for a list of DOT numbers via batched MCMIS API calls."""
    print(f"Enriching {len(dot_numbers)} carriers with city/state...")
    location_lookup = {}

    for i in range(0, len(dot_numbers), ENRICH_BATCH_SIZE):
        batch = dot_numbers[i : i + ENRICH_BATCH_SIZE]
        quoted_numbers = ",".join(f"'{int(n)}'" for n in batch)
        where_clause = f"dot_number in ({quoted_numbers})"

        params = {
            "$select": "dot_number,phy_city,phy_state",
            "$where": where_clause,
            "$limit": ENRICH_BATCH_SIZE,
        }

        # Retry with exponential backoff on rate-limit/transient failures.
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(MCMIS_API_URL, params=params)
                response.raise_for_status()
                results = response.json()
                for rec in results:
                    location_lookup[int(rec["dot_number"])] = {
                        "city": rec.get("phy_city"),
                        "state": rec.get("phy_state"),
                    }
                print(f"  batch {i // ENRICH_BATCH_SIZE + 1}: matched {len(results)} of {len(batch)}")
                break
            except requests.exceptions.RequestException as e:
                wait_seconds = 1.5 * (2 ** attempt)   # 3s, 6s, 12s, 24s, 48s
                print(f"  batch {i // ENRICH_BATCH_SIZE + 1} attempt {attempt} failed ({e}); waiting {wait_seconds:.0f}s")
                time.sleep(wait_seconds)
        else:
            print(f"  batch {i // ENRICH_BATCH_SIZE + 1}: gave up after {MAX_RETRIES} attempts")

        time.sleep(0.3)

    return location_lookup


def main():
    oos_df = fetch_oos_data()
    oos_df = dedupe_by_carrier(oos_df)

    location_lookup = enrich_with_location(oos_df["dot_number"].tolist())

    oos_df["phy_city"] = oos_df["dot_number"].map(lambda n: location_lookup.get(int(n), {}).get("city"))
    oos_df["phy_state"] = oos_df["dot_number"].map(lambda n: location_lookup.get(int(n), {}).get("state"))

    missing = oos_df["phy_state"].isna().sum()
    print(f"Carriers with no location match: {missing} of {len(oos_df)}")

    oos_df.to_csv(OUTPUT_FILE, index=False)
    print(f"Saved {len(oos_df)} enriched records to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
