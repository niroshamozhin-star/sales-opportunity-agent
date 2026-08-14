"""build_search_index.py - builds/populates the Azure AI Search index from the
enriched CSV. Uses plain keyword search (not vectors - the Free-tier 50MB cap
can't hold vector embeddings for 13,000+ records, and keyword + filters suits
this structured data fine)."""

import os
import time

import pandas as pd
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import SearchIndex, SimpleField, SearchableField, SearchFieldDataType

load_dotenv()

SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
SEARCH_KEY = os.environ["AZURE_SEARCH_KEY"]

INDEX_NAME = "oos-sales-index"
INPUT_CSV = "../data/OOS_Enriched_2026.csv"
BATCH_SIZE = 500  # how many records to upload to the index per network call


def build_content_sentence(row):
    """Turns one CSV row into a natural-language sentence for keyword search."""
    city_state = f"{row['phy_city']}, {row['phy_state']}" if pd.notna(row.get("phy_city")) else "an unknown location"
    return (
        f"{row['legal_name']} in {city_state} had an out-of-service order "
        f"on {row['oos_date']} for: {row['oos_reason']}."
    )


def create_index_if_missing(index_client):
    """Defines and creates the search index, unless it already exists."""
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
