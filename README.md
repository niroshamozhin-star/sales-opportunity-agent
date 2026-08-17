# Sales Opportunity Agent

A knowledge-only "Sales Opportunity GPT" built on Azure AI Foundry, that helps a trucking sales team spot recent FMCSA out-of-service (OOS) carriers and generate outreach for them.

## What it does

- **Summarize recent notices** — e.g. "Summarize the last 5 notices in Texas"
- **Generate an outreach script** for a selected carrier — e.g. "Write an outreach script for [carrier name]"

Both modes are grounded in real, indexed FMCSA data — the model never invents a carrier, date, or reason.

## How it's built

- **Data**: FMCSA Out-of-Service Orders (2026, active, deduped) enriched with city/state via the MCMIS Census API — see `src/fetch_and_enrich_oos_data.py`.
- **Search**: 12,673 records indexed in Azure AI Search (`src/build_search_index.py`) using keyword/full-text search — a better fit than vector search for this dataset's structured, factual queries.
- **Agent (primary)**: `foundry-agent-openapi/` — a native Azure AI Foundry Agent that decides which of two tools to call based on the user's question, via a custom Azure Function exposed as an OpenAPI tool. This is the primary implementation, running natively inside Foundry Agent Service.
- **Agent (secondary)**: `src/agent.py` — the same logic reimplemented as a custom Python agent using OpenAI-style function calling, wrapped by a Streamlit chat UI (`src/app.py`). This is the submitted clickable prototype — a secondary, independently-usable backup.

Both implementations are grounded in the same Azure AI Search index and produce identical, deterministic results.

## Running it locally

```
pip install -r requirements.txt
```

Create a `.env` file in the project root with:
```
AZURE_SEARCH_ENDPOINT=https://<your-search-resource>.search.windows.net
AZURE_SEARCH_KEY=<your-search-admin-key>
AZURE_OPENAI_ENDPOINT=https://<your-openai-resource>.openai.azure.com/
AZURE_OPENAI_KEY=<your-openai-key>
```

Build the index (one-time):
```
cd src
python build_search_index.py
```

Run the app:
```
streamlit run app.py
```
