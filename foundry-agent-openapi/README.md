# Foundry Agent - Native Function Calling

Gives the native Azure AI Foundry Agent access to the same deterministic carrier search logic used in the main solution, via a custom Azure Function exposed through an OpenAPI tool.

## How it works

- `Azure_foundry_functionapp/function_app.py` - an Azure Function wrapping the same search logic as `src/agent.py`'s `search_recent_notices`/`get_carrier_details`, hosted as a small HTTP API.
- `openapi_spec.json` - the OpenAPI 3.0 spec describing this API, given to the Foundry Agent as a custom tool.
- The Foundry Agent calls this Function server-side whenever it needs real carrier data - no local script or interactive sign-in required.

This demonstrates the same agent logic running natively inside Azure AI Foundry's Agent Service, as an alternative to the Streamlit/Python implementation in the main repo - both are grounded in the same Azure AI Search index and produce identical, deterministic results.
