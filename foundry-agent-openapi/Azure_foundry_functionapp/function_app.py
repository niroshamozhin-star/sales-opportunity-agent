"""function_app.py - HTTP API wrapping the same two search functions as
agent.py, hosted as an Azure Function so Foundry's OpenAPI tool can call
them server-side (no local script or interactive sign-in required)."""

import os
import json

import azure.functions as func
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
SEARCH_KEY = os.environ["AZURE_SEARCH_KEY"]
INDEX_NAME = "oos-sales-index"

search_client = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=INDEX_NAME, credential=AzureKeyCredential(SEARCH_KEY))


@app.route(route="search_recent_notices", methods=["GET"])
def search_recent_notices(req: func.HttpRequest) -> func.HttpResponse:
    state = req.params.get("state")
    limit = int(req.params.get("limit", 5))
    offset = int(req.params.get("offset", 0))
    date_from = req.params.get("date_from")
    date_to = req.params.get("date_to")

    filter_parts = []
    if state:
        filter_parts.append(f"phy_state eq '{state}'")
    if date_from:
        filter_parts.append(f"oos_date ge '{date_from}'")
    if date_to:
        filter_parts.append(f"oos_date le '{date_to}'")
    filter_str = " and ".join(filter_parts) if filter_parts else None

    results = search_client.search(
        search_text="*",
        filter=filter_str,
        order_by=["oos_date desc"],
        top=limit,
        skip=offset,
    )
    records = [
        {
            "legal_name": r["legal_name"],
            "phy_city": r["phy_city"],
            "phy_state": r["phy_state"],
            "oos_date": r["oos_date"],
            "oos_reason": r["oos_reason"],
        }
        for r in results
    ]
    return func.HttpResponse(json.dumps(records), mimetype="application/json")


@app.route(route="get_carrier_details", methods=["GET"])
def get_carrier_details(req: func.HttpRequest) -> func.HttpResponse:
    carrier_name = req.params.get("carrier_name")
    if not carrier_name:
        return func.HttpResponse(
            json.dumps({"error": "carrier_name is required"}), status_code=400, mimetype="application/json"
        )

    results = search_client.search(search_text=carrier_name, search_fields=["legal_name"], top=3)
    records = [
        {
            "legal_name": r["legal_name"],
            "phy_city": r["phy_city"],
            "phy_state": r["phy_state"],
            "oos_date": r["oos_date"],
            "oos_reason": r["oos_reason"],
        }
        for r in results
    ]
    return func.HttpResponse(json.dumps(records), mimetype="application/json")
