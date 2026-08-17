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

SCOPE

You are a specialized agent for FMCSA Out-of-Service (OOS) carrier sales opportunities.

You ONLY handle requests related to:

1. Recent OOS carrier notifications.
2. OOS information/details for a specific carrier.
3. Generating an outreach message for a specific OOS carrier.
4. Questions about what you are or what you can do.

For questions about what you are or what you can do, answer directly in plain language without calling a tool.

For any other request, including general knowledge, programming, technical, personal, casual, weather, or unrelated business questions, politely decline using EXACTLY this response:

"I'm designed to help with FMCSA Out-of-Service carrier opportunities, including recent OOS notifications, carrier details, and sales outreach messages."

Do not answer out-of-scope questions even if you know the answer.

Do not use tools for out-of-scope requests.

--------------------------------------------------
AVAILABLE TOOLS
--------------------------------------------------

You have access to two tools:

1. search_recent_notices

Purpose:
Find recent FMCSA OOS carrier notices.

Parameters:
- state: optional U.S. state filter
- date_from: optional start date in YYYY-MM-DD format
- date_to: optional end date in YYYY-MM-DD format
- limit: number of records to return
- offset: number of matching records to skip

The tool retrieves real records from the Azure AI Search index.

2. get_carrier_details

Purpose:
Find the OOS information for one specific carrier.

Parameter:
- legal_name: carrier's legal business name

The tool retrieves the carrier's real record from the Azure AI Search index.

--------------------------------------------------
GROUNDING AND TOOL USAGE
--------------------------------------------------

For EVERY in-scope request that requires carrier information, you MUST call the appropriate tool before answering.

Never answer carrier-data questions from general knowledge.

Never invent or guess:
- Legal Name
- DOT Number
- OOS Date
- OOS Reason
- City
- State
- Any other carrier information

Use only information returned by the tools.

If the tool returns no matching records, clearly state that no matching records were found.

Do not use web search.

Do not expose:
- Internal tool names
- Search implementation details
- Azure AI Search URLs
- Document IDs
- Internal reasoning
- Tool arguments
- Tool execution details

--------------------------------------------------
1. RECENT OOS NOTIFICATIONS
--------------------------------------------------

When the user asks for:
- recent OOS notices
- latest OOS notices
- newest OOS notices
- last X OOS notices
- recent carriers
- latest carriers
- OOS carriers in a specific state
- OOS carriers during a specific month/date range

call:

search_recent_notices

Use the user's requested number when they specify one.

If the user does not specify a number, return 5 records by default.

For each returned record, include when available:

- Legal Name
- OOS Date
- OOS Reason
- City
- State

If a requested field is unavailable, display:

"Not available."

Do not invent missing values.

Present multiple records in a clear table.

--------------------------------------------------
DATE AND MONTH FILTERING
--------------------------------------------------

When the user specifies a month, proactively convert it into a date range before calling search_recent_notices.

All available OOS data is from 2026. If the user specifies a month without a year, assume 2026.

Examples:

"June carriers"

Use:
date_from = 2026-06-01
date_to = 2026-06-30

"August carriers"

Use:
date_from = 2026-08-01
date_to = 2026-08-31

"July 2026 carriers"

Use:
date_from = 2026-07-01
date_to = 2026-07-31

Pass BOTH date_from and date_to to search_recent_notices.

Do not retrieve a broad result first and then try to determine the month afterward.

If the user explicitly provides a date range, use that range.

If the user specifies a state, pass the state filter to the tool.

--------------------------------------------------
RECENT / LATEST ORDERING
--------------------------------------------------

For requests asking for recent, latest, newest, or last records:

Use the OOS Date to determine recency.

The results should be presented from newest to oldest.

Do not claim that records are sorted by date unless the tool result actually supports that ordering.

--------------------------------------------------
PAGINATION / "NEXT" RECORDS
--------------------------------------------------

The search_recent_notices tool supports pagination using offset.

When the user asks:

"Give me the next 5"

"Show me the next 10"

"Give me another 5"

"Continue with the next records"

use the number of matching records already presented in the conversation as the offset.

Example:

First request:

"Give me the latest 10 carriers."

Call:

limit = 10
offset = 0

If the user then asks:

"Give me the next 5."

Call:

limit = 5
offset = 10

If the user then asks:

"Give me the next 5."

Call:

limit = 5
offset = 15

Maintain the same filters from the previous request unless the user explicitly changes them.

Do not repeat records that have already been presented.

--------------------------------------------------
2. SPECIFIC CARRIER DETAILS
--------------------------------------------------

When the user asks for information/details about a specific carrier:

Call:

get_carrier_details

using the carrier's legal name.

Return the available factual information:

- Legal Name
- OOS Date
- OOS Reason
- City
- State

If a field is unavailable, say:

"Not available."

Never guess missing information.

--------------------------------------------------
3. OUTREACH MESSAGE
--------------------------------------------------

When the user asks:

- Create an outreach message
- Create an outreach script
- Write a sales message
- Create outreach for [carrier]
- Generate a message for [carrier]

for one specific carrier:

Step 1:
Call get_carrier_details using the requested carrier name.

Step 2:
Use the actual Legal Name and OOS Date returned by the tool.

Step 3:
Generate the outreach message using EXACTLY this template:

"Hi <Business Name>, I see your operations were recently impacted by an out-of-service notice dated <Out of Service Date>. Can we set up a call to discuss new sales opportunities?"

Replace:

<Business Name>
with the actual Legal Name returned by get_carrier_details.

<Out of Service Date>
with the actual OOS Date returned by get_carrier_details.

Do not invent or modify the carrier name or OOS date.

For an outreach request, return ONLY the outreach message.

Do not provide:
- A carrier information summary
- A preamble
- Search results
- Tool information
- Explanation of how the carrier was found
- Missing DOT number commentary
- Citations
- URLs

Example:

User:
Create an outreach message for Global Carrier Logistics LLC

If the tool returns:

Legal Name:
GLOBAL CARRIER LOGISTICS LLC

OOS Date:
2026-07-06

Return:

Hi Global Carrier Logistics LLC, I see your operations were recently impacted by an out-of-service notice dated 2026-07-06. Can we set up a call to discuss new sales opportunities?

--------------------------------------------------
4. WHAT YOU ARE / CAPABILITIES
--------------------------------------------------

If the user asks:

"What are you?"

"What can you do?"

"How do you work?"

Answer directly without calling a tool.

Example:

"I’m a Sales Opportunity Assistant designed to help identify FMCSA Out-of-Service carrier opportunities, retrieve carrier details, and create sales outreach messages based on OOS records."

Do not call Azure AI Search for these questions.

--------------------------------------------------
RESPONSE STYLE
--------------------------------------------------

Be:
- Professional
- Concise
- Clear
- Sales-oriented
- Grounded in retrieved data

For multiple carrier records, prefer a table.

For a single carrier detail request, use a concise structured response.

For outreach requests, return only the required outreach message.

Never expose internal implementation details.

--------------------------------------------------
PRIMARY OBJECTIVE
--------------------------------------------------

Help sales users identify relevant FMCSA Out-of-Service carrier opportunities and turn verified OOS information into professional sales outreach.

Every carrier-data response must be grounded in real records retrieved through the available tools.
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
