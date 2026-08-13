"""
build_search_index.py

What this script does, in plain English:
  1. Reads your enriched CSV file (already fetched/cleaned by fetch_and_enrich_oos_data.py).
  2. For each carrier record, writes one natural-language sentence describing it
     (e.g. "ABC Trucking in Dallas, TX had an out-of-service order on 2026-03-01
     for: 90 day failure to pay fine.") - this reads better for keyword search
     than raw CSV columns pasted together.
  3. Creates the Azure AI Search index (if it doesn't already exist), defining
     which fields it should store and how they should be searchable/filterable.
  4. Uploads every record into that index, in batches, so it's ready for the
     Foundry Agent to search against.

Note: this uses plain keyword/full-text search, not vector similarity search.
The two required response modes (summarize recent notices, generate an
outreach script for a selected carrier) are both structured factual lookups -
filter by date/state, find one specific carrier - which keyword search with
filters handles perfectly. Vector search was tried first, but the Free-tier
Search resource's 50MB storage cap can't hold vector embeddings for 13,000+
records - so this simpler, still-legitimate "RAG" approach (retrieval doesn't
require vectors) was used instead. Retrieval-Augmented Generation just means
the chat model's answer is grounded in retrieved records - keyword search is
one standard way to do that retrieval, especially well-suited to structured
data like this.

This bypasses the Azure AI Foundry portal's "Knowledge base" file-upload
feature entirely (which had a persistent bug), by talking directly to the
same underlying Azure AI Search service via its Python SDK.

Python basics, if you're new to this:
  - `os.environ["X"]` reads an environment variable - here, values loaded from
    the local .env file (never hard-code secrets directly into a script).
  - `for i, row in df.iterrows():` loops over every row of a pandas table.
  - A list comprehension like `[x for x in y]` builds a new list from another one.
"""

import os
import time

import pandas as pd
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import SearchIndex, SimpleField, SearchableField, SearchFieldDataType

# ---------------------------------------------------------------------------
# STEP 0: Load settings from the local .env file (never hard-code secrets)
# ---------------------------------------------------------------------------
load_dotenv()

SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
SEARCH_KEY = os.environ["AZURE_SEARCH_KEY"]

INDEX_NAME = "oos-sales-index"
INPUT_CSV = "../data/OOS_Enriched_2026.csv"
BATCH_SIZE = 500  # how many records to upload to the index per network call


def build_content_sentence(row):
    """
    Turns one CSV row into a natural-language sentence. This reads much better
    for keyword search than raw column values pasted together.
    """
    city_state = f"{row['phy_city']}, {row['phy_state']}" if pd.notna(row.get("phy_city")) else "an unknown location"
    return (
        f"{row['legal_name']} in {city_state} had an out-of-service order "
        f"on {row['oos_date']} for: {row['oos_reason']}."
    )


def create_index_if_missing(index_client):
    """
    Defines the shape of the search index (its fields) and creates it, unless
    an index with this name already exists.
    """
    existing = [idx.name for idx in index_client.list_indexes()]
    if INDEX_NAME in existing:
        print(f"Index '{INDEX_NAME}' already exists - skipping creation.")
        return

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="dot_number", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="legal_name", type=SearchFieldDataType.String),
        SimpleField(name="oos_date", type=SearchFieldDataType.String, filterable=True, sortable=True),
        SearchableField(name="oos_reason", type=SearchFieldDataType.String),
        SimpleField(name="phy_city", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="phy_state", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
    ]

    index = SearchIndex(name=INDEX_NAME, fields=fields)
    index_client.create_index(index)
    print(f"Created index '{INDEX_NAME}'.")


def main():
    print("Loading enriched CSV...")
    df = pd.read_csv(INPUT_CSV, dtype={"dot_number": str})
    print(f"Loaded {len(df)} records.")

    index_client = SearchIndexClient(endpoint=SEARCH_ENDPOINT, credential=AzureKeyCredential(SEARCH_KEY))
    create_index_if_missing(index_client)

    search_client = SearchClient(
        endpoint=SEARCH_ENDPOINT, index_name=INDEX_NAME, credential=AzureKeyCredential(SEARCH_KEY)
    )

    total = len(df)
    for start in range(0, total, BATCH_SIZE):
        batch_df = df.iloc[start : start + BATCH_SIZE]

        documents = [
            {
                "id": str(row["dot_number"]),
                "dot_number": str(row["dot_number"]),
                "legal_name": str(row["legal_name"]),
                "oos_date": str(row["oos_date"]),
                "oos_reason": str(row["oos_reason"]),
                "phy_city": str(row.get("phy_city", "")),
                "phy_state": str(row.get("phy_state", "")),
                "content": build_content_sentence(row),
            }
            for _, row in batch_df.iterrows()
        ]

        search_client.upload_documents(documents=documents)
        print(f"  processed {min(start + BATCH_SIZE, total)} of {total}")
        time.sleep(0.2)  # be a polite API citizen between batches

    print("Done. Index is ready to query.")


if __name__ == "__main__":
    main()
