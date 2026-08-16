# Foundry Agent - Function Calling Exploration

Four approaches were tried for giving the native Foundry Agent access to real,
deterministic carrier search logic (beyond the working Python/Streamlit
solution in the main `AzureFoundry` repo). Here's what happened with each.

## 1. Knowledge base (Foundry IQ)

- Upload files directly to the agent; Foundry auto-builds and manages its own index.
- **Obstacle:** persistent 404 error on the embedding call during file upload, even
  after deploying the embedding model and adding an explicit Azure OpenAI connection.
- **Outcome:** abandoned early - never got past the upload step.

## 2. Built-in "Azure AI Search" tool

- Point the agent directly at the existing `oos-sales-index` - zero custom code.
- **Obstacle 1:** unreliable "most recent" sorting - confirmed it missed ~19 real,
  more-recent records that should have ranked first (verified against source data).
  Uses relevance-based search internally, not a guaranteed date sort.
- **Obstacle 2:** no true pagination - "give me the next batch" always re-ran the
  same fixed top-50 query, confirmed via repeated testing.
- **Obstacle 3:** fixed 50-document retrieval cap, not configurable.
- **Outcome:** works, but not reliable enough for a "most recent" use case where
  order genuinely matters. Kept as a working fallback demo option.

## 3. FunctionTool + local Python script

- Give the Foundry Agents SDK the real `search_recent_notices()`/`get_carrier_details()`
  Python functions directly (`InteractiveBrowserCredential` for auth).
- **Obstacle:** personal Microsoft account authentication failure - "You can't sign in
  here with a personal account." Persisted even after creating a custom Azure AD app
  registration explicitly configured to allow personal accounts, and after a tenant
  propagation wait. Root cause: personal-account + Default Directory tenant is a known
  awkward edge case for third-party app sign-in validation.
- **Outcome:** correct design, blocked by an environment-specific auth limitation, not
  a flaw in the approach itself. Requires a machine that can complete interactive
  browser sign-in reliably (not this trial-account environment).

## 4. OpenAPI tool + Azure Function (WORKED)

- Wrap the same search logic as a small HTTP API (Azure Function), give Foundry an
  OpenAPI spec describing it, call it via an OpenAPI tool - no local script, no
  interactive sign-in needed.
- **Obstacle 1:** Free Trial subscription blocked creating both Flex Consumption and
  App Service resources outright. Fixed by upgrading to Pay-As-You-Go (drawing from
  existing trial credit, not new spend).
- **Obstacle 2:** Flex Consumption's deployment succeeded per the logs, but the app
  reported "0 functions found" - traced to Flex Consumption's blob-based "run from
  package" deployment model not being correctly wired up by the portal's manual
  zip-push method. Confirmed by the total absence of Advanced Tools/Kudu on that
  hosting plan.
- **Obstacle 3:** switched to classic **App Service** hosting instead (proper Kudu
  support) - needed the `AzureWebJobsFeatureFlags=EnableWorkerIndexing` setting and
  Linux/Python 3.11-targeted vendored dependencies (pip install with
  `--platform manylinux2014_x86_64 --python-version 3.11`) to get functions detected.
- **Obstacle 4:** a Foundry Guardrail false-positive blocked legitimate in-scope
  requests once the OpenAPI tool was added alongside the built-in Azure AI Search
  tool - resolved by removing the redundant built-in tool.
- **Obstacle 5:** even with only the OpenAPI tool present, the model sometimes
  answered from general knowledge or reached for Web search instead of calling the
  tool - fixed with a strict instruction ("you MUST call carriersearchapi for every
  in-scope request").
- **Obstacle 6:** the tool call itself then failed with 401 Unauthorized - the
  connection/credential (API key) had never actually been linked to the tool at the
  agent level (only created at the project level). Fixed by setting Authentication
  method to "Connection" and selecting the existing saved connection.
- **Outcome: fully working end-to-end.** Real, deterministic, correctly-sorted data
  confirmed matching both the source Excel data and the Streamlit app's output.
  This is the approach used for the native Foundry Agent live demo.

## Summary

| Approach | Result |
|---|---|
| Knowledge base (Foundry IQ) | Blocked by a platform bug |
| Built-in Azure AI Search tool | Works, but unreliable sorting/pagination |
| FunctionTool + local script | Blocked by personal-account auth |
| **OpenAPI tool + Azure Function** | **Working, reliable, used for the demo** |
