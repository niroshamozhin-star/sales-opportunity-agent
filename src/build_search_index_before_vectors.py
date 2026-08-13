"""
build_search_index_before_vectors.py

REFERENCE ONLY - this is the ORIGINAL version of build_search_index.py, kept
for comparison. It used vector embeddings for semantic search, which hit the
AI Search Free tier's 50MB storage cap partway through 13,140 records (each
record's vector took up far more space than plain text). The current
build_search_index.py replaces this with plain keyword/full-text search
instead, which fits comfortably and is a better fit for this project's
structured, factual response modes anyway.

What this version did differently:
  - For each record, asked Azure OpenAI to turn its description into a
    256-number vector (a reduced-size embedding, to try to fit the Free tier).
  - Defined the index with an extra `content_vector` field and a vector
    search configuration (HNSW algorithm + a vector search profile).
  - Uploaded each record's text AND its vector together.
"""

import os
import time

import pandas as pd
from dotenv import load_dotenv
from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SimpleField,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
)

load_dotenv()

SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
SEARCH_KEY = os.environ["AZURE_SEARCH_KEY"]
OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
OPENAI_KEY = os.environ["AZURE_OPENAI_KEY"]

INDEX_NAME = "oos-sales-index"
EMBEDDING_DEPLOYMENT = "text-embedding-3-small"
# text-embedding-3-small can produce vectors as large as 1536 numbers, but it
# also supports asking for a SMALLER vector while keeping most of the quality.
# We use 256 here instead of the full 1536 - this STILL wasn't small enough to
# fit all 13,000+ records inside the Free tier's 50MB storage cap.
EMBEDDING_DIMENSIONS = 256
INPUT_CSV = "../data/OOS_Enriched_2026.csv"
BATCH_SIZE = 100  # how many records to upload to the index per network call


def build_content_sentence(row):
    city_state = f"{row['phy_city']}, {row['phy_state']}" if pd.notna(row.get("phy_city")) else "an unknown location"
    return (
        f"{row['legal_name']} in {city_state} had an out-of-service order "
        f"on {row['oos_date']} for: {row['oos_reason']}."
    )


def create_index_if_missing(index_client):
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
        # --- this vector field is what over-filled the Free tier's storage ---
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=EMBEDDING_DIMENSIONS,
            vector_search_profile_name="default-vector-profile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="default-hnsw")],
        profiles=[
            VectorSearchProfile(
                name="default-vector-profile",
                algorithm_configuration_name="default-hnsw",
            )
        ],
    )

    index = SearchIndex(name=INDEX_NAME, fields=fields, vector_search=vector_search)
    index_client.create_index(index)
    print(f"Created index '{INDEX_NAME}'.")


def get_embeddings(openai_client, texts):
    """Asks Azure OpenAI to turn a LIST of texts into a LIST of vectors."""
    response = openai_client.embeddings.create(
        model=EMBEDDING_DEPLOYMENT, input=texts, dimensions=EMBEDDING_DIMENSIONS
    )
    return [item.embedding for item in response.data]


def main():
    print("Loading enriched CSV...")
    df = pd.read_csv(INPUT_CSV, dtype={"dot_number": str})
    print(f"Loaded {len(df)} records.")

    openai_client = AzureOpenAI(
        azure_endpoint=OPENAI_ENDPOINT,
        api_key=OPENAI_KEY,
        api_version="2024-02-01",
    )

    index_client = SearchIndexClient(endpoint=SEARCH_ENDPOINT, credential=AzureKeyCredential(SEARCH_KEY))
    create_index_if_missing(index_client)

    search_client = SearchClient(
        endpoint=SEARCH_ENDPOINT, index_name=INDEX_NAME, credential=AzureKeyCredential(SEARCH_KEY)
    )

    total = len(df)
    for start in range(0, total, BATCH_SIZE):
        batch_df = df.iloc[start : start + BATCH_SIZE]
        contents = [build_content_sentence(row) for _, row in batch_df.iterrows()]

        try:
            vectors = get_embeddings(openai_client, contents)
        except Exception as e:
            print(f"  rows {start}-{start + len(batch_df)} - embedding batch failed: {e}")
            continue

        documents = [
            {
                "id": str(row["dot_number"]),
                "dot_number": str(row["dot_number"]),
                "legal_name": str(row["legal_name"]),
                "oos_date": str(row["oos_date"]),
                "oos_reason": str(row["oos_reason"]),
                "phy_city": str(row.get("phy_city", "")),
                "phy_state": str(row.get("phy_state", "")),
                "content": content,
                "content_vector": vector,  # <-- this extra field per record is what filled 50MB
            }
            for (_, row), content, vector in zip(batch_df.iterrows(), contents, vectors)
        ]

        # This upload call is where "Storage quota has been exceeded" eventually fired
        search_client.upload_documents(documents=documents)
        print(f"  processed {min(start + BATCH_SIZE, total)} of {total}")
        time.sleep(0.2)

    print("Done. Index is ready to query.")


if __name__ == "__main__":
    main()
