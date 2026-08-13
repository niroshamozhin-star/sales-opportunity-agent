# Sales Opportunity Agent

A knowledge-only "Sales Opportunity GPT" built on Azure AI Foundry, that helps a trucking sales team spot recent FMCSA out-of-service (OOS) carriers and generate outreach for them.

## What it does

- **Summarize recent notices** — e.g. "Summarize the last 5 notices in Texas"
- **Generate an outreach script** for a selected carrier — e.g. "Write an outreach script for [carrier name]"

Both modes are grounded in real, indexed FMCSA data — the model never invents a carrier, date, or reason.

## How it's built

- **Data**: FMCSA Out-of-Service Orders (2026, active, deduped) enriched with city/state via the MCMIS Census API — see `src/fetch_and_enrich_oos_data.py`.
- **Search**: 13,140 records indexed in Azure AI Search (`src/build_search_index.py`) using keyword/full-text search — a better fit than vector search for this dataset's structured, factual queries.
- **Agent**: `src/agent.py` uses OpenAI-style function calling — the model (Azure OpenAI `gpt-5.4-mini`) decides which of two tools to call based on the user's question; the real search runs in Python, and results are handed back to the model to generate the final grounded answer.
- **Interface**: `src/app.py` is a Streamlit chat UI wrapping the agent.

See `IMPLEMENTATION_PLAN.md` for the full phase-by-phase build process and the decisions made along the way.

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
