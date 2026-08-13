"""
fetch_and_enrich_oos_data.py

What this script does, in plain English:
  1. Downloads FMCSA "Out of Service" carrier records directly from the live government
     API (no manual file download needed).
  2. Keeps only the records we care about: status is ACTIVE, no rescind date, and the
     event happened in 2026 (that's what makes it "recent").
  3. Removes duplicate carriers (a carrier should only appear once).
  4. Looks up each carrier's city/state using a second government API (MCMIS Census data),
     joining on the shared DOT_NUMBER field.
  5. Saves one clean CSV file with everything needed for the RAG index.

Python basics used here, if you're new to the language:
  - `import X` brings in a library of pre-written code so we don't reinvent it.
  - `def some_name(...):` defines a function - a reusable block of code.
  - A dictionary (`{}`) is a lookup table: `my_dict["key"]` gets the value stored under "key".
  - A DataFrame (from pandas) is basically an Excel sheet living inside Python -
    rows and columns you can filter, sort, and save to CSV.
  - `f"...{variable}..."` is an f-string - it lets you insert a variable's value into text.
"""

import requests      # lets Python make HTTP calls (talk to web APIs)
import pandas as pd   # the standard library for working with table-shaped data
import time           # lets us pause between API calls (time.sleep)

# ---------------------------------------------------------------------------
# STEP 0: Constants - the "settings" for this script, all in one place
# ---------------------------------------------------------------------------
OOS_API_URL = "https://data.transportation.gov/resource/p2mt-9ige.json"
MCMIS_API_URL = "https://data.transportation.gov/resource/az4n-8mr2.json"
# "../data/..." because this script lives in src/, one folder below the project root
OUTPUT_FILE = "../data/OOS_Enriched_2026.csv"

PAGE_SIZE = 5000        # how many OOS rows to ask for per API call
ENRICH_BATCH_SIZE = 100  # how many DOT numbers to look up per MCMIS API call
MAX_RETRIES = 5          # how many times to retry a failed API call before giving up


def fetch_oos_data():
    """
    Pulls ALL matching OOS records from the live API, one page at a time.

    Why paginate? The API won't return unlimited rows in one call, so we ask for
    PAGE_SIZE rows at a time, moving the "offset" forward each time, until a page
    comes back empty (meaning there's nothing left to fetch).
    """
    print("Fetching OOS data from the live API...")

    # This is the filter, written in the API's query language (like SQL's WHERE clause).
    # It runs on the server, so we only ever download rows we actually want.
    where_clause = (
        "status='ACTIVE' AND rescind_date IS NULL "
        "AND oos_date >= '2026-01-01' AND oos_date < '2027-01-01'"
    )

    all_rows = []      # we'll collect every page of results into this list
    offset = 0

    while True:
        params = {
            "$select": "dot_number,legal_name,oos_date,oos_reason",
            "$where": where_clause,
            "$limit": PAGE_SIZE,
            "$offset": offset,
        }
        response = requests.get(OOS_API_URL, params=params)
        response.raise_for_status()   # throws an error if the API call failed
        page = response.json()        # convert the API's JSON response into Python objects

        if not page:
            break   # empty page = we've fetched everything, stop looping

        all_rows.extend(page)
        print(f"  fetched {len(all_rows)} rows so far...")
        offset += PAGE_SIZE

    # Turn our plain list of dictionaries into a pandas DataFrame (a table)
    df = pd.DataFrame(all_rows)
    print(f"Total rows fetched before de-duplication: {len(df)}")
    return df


def dedupe_by_carrier(df):
    """
    Keeps only one row per carrier (DOT_NUMBER), keeping the most recent OOS_DATE
    if a carrier shows up more than once.
    """
    # Convert dot_number to a real number (not text) so that "169538" and " 169538"
    # are correctly treated as the SAME carrier - this is the fix for the earlier
    # mismatch we ran into with a text-only comparison.
    df["dot_number"] = pd.to_numeric(df["dot_number"], errors="coerce")

    # Convert oos_date from text into a real date, so sorting by "most recent" is
    # correct and unambiguous (not just comparing text strings).
    df["oos_date"] = pd.to_datetime(df["oos_date"])

    # Sort so the newest OOS_DATE for each carrier comes first, then keep just the
    # first row per dot_number.
    df = df.sort_values("oos_date", ascending=False)
    df = df.drop_duplicates(subset="dot_number", keep="first")

    print(f"Total rows after de-duplication: {len(df)}")
    return df


def enrich_with_location(dot_numbers):
    """
    Looks up city/state for a list of DOT numbers by calling the MCMIS API in
    batches (instead of one call per carrier, which would be slow and likely to
    get rate-limited).

    Returns a dictionary like: {169538: {"city": "DANVILLE", "state": "KY"}, ...}
    """
    print(f"Enriching {len(dot_numbers)} carriers with city/state...")
    location_lookup = {}

    for i in range(0, len(dot_numbers), ENRICH_BATCH_SIZE):
        batch = dot_numbers[i : i + ENRICH_BATCH_SIZE]
        # Build a filter like: dot_number in ('123','456',...)
        quoted_numbers = ",".join(f"'{int(n)}'" for n in batch)
        where_clause = f"dot_number in ({quoted_numbers})"

        params = {
            "$select": "dot_number,phy_city,phy_state",
            "$where": where_clause,
            "$limit": ENRICH_BATCH_SIZE,
        }

        # Try the call a few times if it fails (e.g. "429 Too Many Requests"),
        # waiting a bit longer each time before trying again.
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
                break   # success - stop retrying this batch
            except requests.exceptions.RequestException as e:
                wait_seconds = 1.5 * (2 ** attempt)   # 3s, 6s, 12s, 24s, 48s
                print(f"  batch {i // ENRICH_BATCH_SIZE + 1} attempt {attempt} failed ({e}); waiting {wait_seconds:.0f}s")
                time.sleep(wait_seconds)
        else:
            print(f"  batch {i // ENRICH_BATCH_SIZE + 1}: gave up after {MAX_RETRIES} attempts")

        time.sleep(0.3)   # be a polite API citizen between successful batches too

    return location_lookup


def main():
    oos_df = fetch_oos_data()
    oos_df = dedupe_by_carrier(oos_df)

    location_lookup = enrich_with_location(oos_df["dot_number"].tolist())

    # Add two new columns by looking each carrier up in our location dictionary.
    oos_df["phy_city"] = oos_df["dot_number"].map(lambda n: location_lookup.get(int(n), {}).get("city"))
    oos_df["phy_state"] = oos_df["dot_number"].map(lambda n: location_lookup.get(int(n), {}).get("state"))

    missing = oos_df["phy_state"].isna().sum()
    print(f"Carriers with no location match: {missing} of {len(oos_df)}")

    oos_df.to_csv(OUTPUT_FILE, index=False)
    print(f"Saved {len(oos_df)} enriched records to {OUTPUT_FILE}")


if __name__ == "__main__":
    # This check means "only run main() if this file is executed directly,
    # not if it's imported by another script." Standard Python convention.
    main()
