# Sales Opportunity Agent — Phased Implementation Plan

## Context

This is the "AI Expert Assessment" take-home (`AI_Expert_Assessment B.xlsx`) for a job interview, due 2026-08-17. The scenario: FMCSA Out-of-Service (OOS) truck carrier data is a sales signal — when a carrier is put out of service, that's a lead for compliance/maintenance/replacement sales. The assessment scope (per the doc's own note) is a **knowledge-only GPT + architecture design doc**, not a fully automated end-to-end pipeline.

Data already gathered this session:
- `AI_Expert_Assessment B.xlsx` — the assessment brief (3 sections: architecture doc, custom GPT/RAG build, implementation & monitoring)
- `OUT_OF_SERVICE_ORDERS_20260812.csv` — 396K rows, columns `DOT_NUMBER, LEGAL_NAME, DBA_NAME, OOS_DATE, OOS_REASON, STATUS, RESCIND_DATE`. No location data.
- `OOS_Data_Dictionary_Rev03__2025-01-24.pdf` — field definitions for the OOS file
- `MCMIS_Company_Census_Data_Dictionary_Rev08_2026-01-23.pdf` — field definitions for the enrichment source
- Live API confirmed working: `https://data.transportation.gov/resource/az4n-8mr2.json` (MCMIS Company Census) — join key `dot_number`, gives `phy_city`/`phy_state`/`legal_name`/etc.
- Platform decision: **Azure AI Foundry** (fits AZ-204 background, existing AzureFoundry project)

This plan breaks the build into phases matching the assessment's own required deliverables, sequenced so the highest-risk/most-unknown work (the Foundry RAG setup) happens early, not last.

## Phase 1 — Data Preparation (local, no Azure needed yet) — DONE

Goal: turn the raw OOS data into a small, enriched, RAG-ready dataset.

**Final approach (Python, not manual Excel or PowerShell):** `src/fetch_and_enrich_oos_data.py` pulls data directly from the live FMCSA API (`p2mt-9ige`) instead of the downloaded CSV — this sidesteps the file-corruption and Excel-vs-script count mismatch issues hit earlier (manual Excel gave 10,939; naive script-based filtering gave 13,030; the rigorous, server-side, properly-typed live query — confirmed twice — gives **13,140**, which the script reproduces exactly).

**Project layout:**
```
AzureFoundry/
├── Project Requirement Documents/   (the given assessment brief + data dictionaries)
├── src/                              (all code, e.g. fetch_and_enrich_oos_data.py)
├── data/                             (generated outputs, e.g. OOS_Enriched_2026.csv)
├── requirements.txt                  (pandas, requests)
├── IMPLEMENTATION_PLAN.md            (this file)
└── Decision_Log_QA.docx              (interview-ready reasoning behind each decision)
```

1. Query the OOS API server-side with `$where=status='ACTIVE' AND rescind_date IS NULL AND oos_date>='2026-01-01' AND oos_date<'2027-01-01'`, paginating with `$limit`/`$offset`.
2. Load into a pandas DataFrame; convert `dot_number` to numeric and `oos_date` to a real date (this is the fix for the earlier text-comparison mismatch), then dedupe keeping each carrier's most recent record. Result: 13,140 unique carriers.
3. Batch-query the MCMIS Census API (`$where=dot_number in (...)`, batches of 100) to enrich each with city/state, with retry + exponential backoff on rate-limit errors.
4. Join and save to `data/OOS_Enriched_2026.csv` — confirmed 0 missing locations out of 13,140.

Deliverable: `data/OOS_Enriched_2026.csv` — 13,140 enriched records, ready to index in Phase 2.

## Phase 2 — Azure AI Foundry RAG Setup — DONE (via Python/SDK, not the portal UI)

Goal: get the enriched dataset searchable/retrievable by an LLM.

**What actually happened:** the Foundry portal's newer "Knowledge base" file-upload feature (Foundry IQ, marked Preview) had a persistent bug — every upload failed with a 404 on the embedding call, even after deploying the embedding model and adding an explicit Azure OpenAI connection. Pivoted to building the index directly with Python (`src/build_search_index.py`) using the `azure-search-documents` SDK, which bypassed the bug entirely.

That in turn hit a real Free-tier limit: vector embeddings for 13,140 records (even at a reduced 256 dimensions) exceeded the Search resource's 50MB storage cap. Fix: dropped vector search entirely and used plain keyword/full-text search instead — the two required response modes (summarize recent notices, generate an outreach script for a selected carrier) are structured factual lookups, not semantic queries, so keyword search + filterable fields (state, date) is a better fit anyway, not just a workaround.

**Result:** `oos-sales-index` in `sales-ai-search-nb2026`, 13,140 documents, verified with a sample query. Foundry resource `nb-sales-opportunity-agent-2026`, project `sales-opportunity-agent`, model `gpt-5.4-mini` all exist and are live.

Deliverable: a working, queryable Azure AI Search index. DONE.

## Phase 3 — Build the Two Required Response Modes — DONE (Python, not the Foundry portal Agent UI)

Goal: satisfy the assessment's explicit Section 2 requirement.

**What actually happened:** the Foundry portal's Agent "Knowledge" feature couldn't attach to an index built outside its own Foundry IQ flow (the Indexes tab showed nothing, even after connecting properly). Built the agent logic directly in Python instead: `src/agent.py` implements both response modes using OpenAI-style function calling — the model decides which of two tools to call (`search_recent_notices`, `get_carrier_details`) based on the user's question, our code runs the real search against `oos-sales-index`, and the model writes the final answer grounded in those real results. `src/app.py` wraps this in a Streamlit chat interface (`streamlit run app.py`, opens at localhost:8501) for the actual live demo.

1. **Mode 1 — Summary**: tested with "Summarize the last 3 notices in Ohio" — correctly filtered by state, returned 3 real records, summarized accurately.
2. **Mode 2 — Outreach script**: tested with "Write an outreach script for [carrier]" — correctly looked up the real record and generated the exact template text from the brief, filled in with real name/date.
3. Multi-turn tested too ("write an outreach script for the first one" after a summary) — conversation history/context works correctly.

Deliverable: a demo-able agent (Streamlit app at `localhost:8501`) supporting both modes, tested live. DONE.

## Phase 4 — Architecture Design Document (Section 1)

Goal: the written proposal the assessment explicitly requires, answering its 6 points:
1. Agent purpose + primary users (sales reps, by state territory)
2. Business problem + outcome (early visibility into OOS-triggered sales opportunities)
3. Data sources (FMCSA OOS feed + MCMIS Census API for enrichment)
4. Decision logic (where rules apply — e.g., ACTIVE + no rescind date filter — vs. where GenAI applies — summarization/script generation)
5. Outputs (salesperson notification + outreach script)
6. 2–4 week MVP scope (what you actually built: knowledge-only GPT) vs. future version (daily auto-polling, auto-notification to reps, CRM integration)

Include a simple box-and-arrow diagram: FMCSA feed → filter/enrich → index → Sales Opportunity Agent → salesperson.

Deliverable: written doc/slides, can be drafted in parallel with Phase 3 testing.

## Phase 5 — Implementation & Monitoring Write-up (Section 3)

Goal: the assessment's required closing section, pure writing (low risk, do once the build is stable):
1. Deployment steps for taking this from demo to production
2. Cost-control strategy as usage scales (e.g., pre-filtering before indexing, caching common queries, right-sizing the model tier)
3. Governance/security/ethics: hallucination mitigation (grounding on retrieved records only, citing source record IDs), data handling
4. 60-day success metrics (e.g., # of leads surfaced, rep response rate, conversion to booked calls)

Deliverable: written section, folded into the same doc/slides as Phase 4.

## Phase 6 — Demo Assembly & Rehearsal

**Submission requirement clarified (2026-08-13) — the recruiter expects 3 specific deliverables:**
1. **A clickable prototype** — something the recruiter can click into and use *themselves*, independent of a live screen-share. Our Streamlit app currently only runs on `localhost`, so this needs actual deployment, not just a local demo. Plan: deploy via **Streamlit Community Cloud** (free, deploys straight from a GitHub repo, gives a real public URL). Deferred for now — user wants to push source code to GitHub "later."
2. **A presentation** — slides covering Phases 4+5 (architecture + implementation/monitoring).
3. **Source code** — via the same GitHub repo used for deployment.

Goal: get ready for the live walkthrough the assessment requires.
1. Push code to GitHub (has an account already; repo visibility decision deferred).
2. Deploy the Streamlit app via Streamlit Community Cloud, using its secrets manager for the 4 Azure values (never commit `.env`).
3. Assemble Phases 4+5 into one proposal/slide deck.
4. Rehearse the live demo end-to-end at least once (agent setup walkthrough + live interaction with both modes), using the deployed prototype link.
5. Submit to recruiter: prototype link + presentation + source code link.

## Verification

- Phase 1: spot-check the enriched output file — pick 3-5 known DOT numbers and manually confirm city/state look correct.
- Phase 2: confirm the Foundry agent's retrieval returns actual indexed records (not hallucinated) when asked a factual question.
- Phase 3: run both response modes against real records and confirm business name/date match the source data exactly.
- Phase 4/5: no technical verification needed — these are review/proofread only.
- Phase 6: the rehearsal itself is the verification — if the live demo breaks, it surfaces here, not during the actual interview.
