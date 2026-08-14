"""agent.py - Sales Opportunity Agent. Connects the Azure AI Search index to
Azure OpenAI via function calling: the model picks a tool, we run the real
search, and the model answers using only what the tool returns."""

import os
import json

from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from openai import AzureOpenAI

load_dotenv()

SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
SEARCH_KEY = os.environ["AZURE_SEARCH_KEY"]
OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
OPENAI_KEY = os.environ["AZURE_OPENAI_KEY"]

INDEX_NAME = "oos-sales-index"
CHAT_DEPLOYMENT = "gpt-5.4-mini"

SYSTEM_PROMPT = """You are the Sales Opportunity Assistant for a trucking sales team.

You have access to FMCSA out-of-service (OOS) carrier records for 2026, via two tools:
- search_recent_notices: finds the most recent OOS notices, optionally filtered
  by state and/or a date range (date_from/date_to, format YYYY-MM-DD). Also
  supports pagination via the offset parameter - when the user asks for "the
  next N" records, call this again with offset set to how many matching
  records you've already shown them in this conversation, so they get a new
  batch instead of repeats.
- get_carrier_details: looks up one specific carrier by name.

IMPORTANT - whenever the user's request names a specific month (with or
without a year - all data is from 2026, so assume 2026 if no year is given),
you MUST compute date_from as that month's first day and date_to as its last
day, and pass BOTH into search_recent_notices. Do not call search_recent_notices
without those dates and then comment afterward that the results were from a
different month - always filter proactively. For example, "June carrier
details" means date_from="2026-06-01", date_to="2026-06-30".

You support exactly two kinds of requests:
1. Summarizing recent OOS notices (call search_recent_notices).
2. Generating a personalized outreach script for ONE selected carrier (call
   get_carrier_details, then write the script using this exact template,
   filled in with that carrier's real details):

   "Hi <Business Name>, I see your operations were recently impacted by an
   out-of-service notice dated <Out of Service Date>. Can we set up a call
   to discuss new sales opportunities?"

Always call a tool to get real data before answering a request for carrier
data - never invent a carrier, date, city, or reason. If a tool returns no
matching records, say so plainly instead of guessing.

If the user asks what you are, what you can do, or how you work (not asking
for carrier data itself), answer directly in plain language - do not call a
tool for this, since there's no carrier data to look up for a question like
that.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_recent_notices",
            "description": "Find the most recent out-of-service notices, optionally filtered by US state and/or a date range, with pagination support via offset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "description": "Two-letter US state code to filter by, e.g. 'OH'. Omit to search all states.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "How many recent notices to return.",
                        "default": 5,
                    },
                    "offset": {
                        "type": "integer",
                        "description": "How many matching records to skip before returning results. Use this to fetch the next page - e.g. if you already showed 5 records, pass offset=5 to get the next batch instead of repeating them.",
                        "default": 0,
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Optional start date (format YYYY-MM-DD), inclusive. Use to filter to a specific month or date range, e.g. '2026-06-01' for the start of June 2026.",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "Optional end date (format YYYY-MM-DD), inclusive. Use to filter to a specific month or date range, e.g. '2026-06-30' for the end of June 2026.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_carrier_details",
            "description": "Look up one specific carrier by business name, to generate an outreach script for them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "carrier_name": {
                        "type": "string",
                        "description": "The business name (or part of it) of the carrier to look up.",
                    },
                },
                "required": ["carrier_name"],
            },
        },
    },
]


def get_clients():
    """Creates the two clients this agent needs: one for search, one for chat."""
    search_client = SearchClient(
        endpoint=SEARCH_ENDPOINT, index_name=INDEX_NAME, credential=AzureKeyCredential(SEARCH_KEY)
    )
    openai_client = AzureOpenAI(
        azure_endpoint=OPENAI_ENDPOINT,api_key=OPENAI_KEY, api_version="2024-02-01",
    )
    return search_client, openai_client


def search_recent_notices(search_client, state=None, limit=5, offset=0, date_from=None, date_to=None):
    """Response mode 1: most recent OOS notices, newest first. Supports
    state/date-range filtering and offset-based pagination."""
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
    return [
        {
            "legal_name": r["legal_name"],
            "phy_city": r["phy_city"],
            "phy_state": r["phy_state"],
            "oos_date": r["oos_date"],
            "oos_reason": r["oos_reason"],
        }
        for r in results
    ]


def get_carrier_details(search_client, carrier_name):
    """Response mode 2 (step 1): find one specific carrier by name."""
    results = search_client.search(search_text=carrier_name, search_fields=["legal_name"], top=3)
    return [
        {
            "legal_name": r["legal_name"],
            "phy_city": r["phy_city"],
            "phy_state": r["phy_state"],
            "oos_date": r["oos_date"],
            "oos_reason": r["oos_reason"],
        }
        for r in results
    ]


def run_tool_call(search_client, tool_call):
    """Runs whichever function the model asked for, using the arguments it provided."""
    args = json.loads(tool_call.function.arguments)

    if tool_call.function.name == "search_recent_notices":
        result = search_recent_notices(search_client, **args)
    elif tool_call.function.name == "get_carrier_details":
        result = get_carrier_details(search_client, **args)
    else:
        result = {"error": f"unknown tool: {tool_call.function.name}"}

    return json.dumps(result)


def chat_with_agent(search_client, openai_client, messages):
    """
    Sends the conversation to the model. If the model wants to call a tool,
    we run it and send the model the result, then ask it for its final
    answer. Returns the assistant's reply text and the updated message list
    (so the calling code can keep the conversation history for next turn).
    """
    response = openai_client.chat.completions.create(
        model=CHAT_DEPLOYMENT,
        messages=messages,
        tools=TOOLS,
    )
    message = response.choices[0].message

    if message.tool_calls:
        # Convert SDK object to a plain dict - Streamlit and the next API call need message["role"].
        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ],
            }
        )
        for tool_call in message.tool_calls:
            # If the search itself fails (e.g. a transient network error), we still
            # must append a tool response for this tool_call_id - otherwise the
            # conversation history is left with an unanswered tool call, which
            # OpenAI permanently rejects on every future turn until the session
            # is reset. So on failure, feed the model an error string instead.
            try:
                tool_result = run_tool_call(search_client, tool_call)
            except Exception as e:
                tool_result = json.dumps({"error": f"Search failed: {e}"})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )

        follow_up = openai_client.chat.completions.create(
            model=CHAT_DEPLOYMENT,
            messages=messages,
        )
        final_message = follow_up.choices[0].message
        messages.append({"role": "assistant", "content": final_message.content})
        return final_message.content, messages

    messages.append({"role": "assistant", "content": message.content})
    return message.content, messages


if __name__ == "__main__":
    # Command-line test loop, no Streamlit needed.
    search_client, openai_client = get_clients()
    conversation = [{"role": "system", "content": SYSTEM_PROMPT}]

    print("Sales Opportunity Agent (command-line test) - type a question, or 'quit' to exit.")
    while True:
        user_input = input("\nYou: ")
        if user_input.strip().lower() == "quit":
            break
        conversation.append({"role": "user", "content": user_input})
        reply, conversation = chat_with_agent(search_client, openai_client, conversation)
        print(f"\nAgent: {reply}")
