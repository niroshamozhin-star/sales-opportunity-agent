"""
agent.py

What this file does, in plain English:
  This is the "brain" of the Sales Opportunity Agent. It connects two things
  we already built - the Azure AI Search index (13,140 enriched carrier
  records) and the gpt-5.4-mini chat model - using a pattern called
  "function calling" (also called "tool use").

  How function calling works, if you're new to it:
    1. We tell the model about two Python functions it's "allowed to call":
       search_recent_notices() and get_carrier_details().
    2. When a user asks a question, the model reads it and decides for
       itself whether it needs to call one of those functions to answer -
       e.g. "summarize the last 5 notices" clearly needs
       search_recent_notices(), while "write an outreach script for ABC
       Trucking" needs get_carrier_details("ABC Trucking").
    3. We actually run whichever function it asked for (this is real
       Python code doing a real search against the index - the model never
       touches the index directly, it just asks us to look things up).
    4. We send the function's result back to the model, and it writes the
       final answer using that real data.

  This is what makes the agent's answers grounded in real records instead of
  the model just making something up - it can only "know" what the search
  functions actually return.

Python basics, if you're new to this:
  - `json.dumps(x)` turns a Python object into a JSON text string (and
    `json.loads(text)` does the reverse) - needed because the model
    communicates in JSON text, not raw Python objects.
  - `**kwargs` in a function call means "unpack this dictionary into
    keyword arguments" - e.g. if args = {"state": "OH"}, then
    search_recent_notices(**args) is the same as search_recent_notices(state="OH").
"""

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
- search_recent_notices: finds the most recent OOS notices, optionally filtered by state.
- get_carrier_details: looks up one specific carrier by name.

You support exactly two kinds of requests:
1. Summarizing recent OOS notices (call search_recent_notices).
2. Generating a personalized outreach script for ONE selected carrier (call
   get_carrier_details, then write the script using this exact template,
   filled in with that carrier's real details):

   "Hi <Business Name>, I see your operations were recently impacted by an
   out-of-service notice dated <Out of Service Date>. Can we set up a call
   to discuss new sales opportunities?"

Always call a tool to get real data before answering - never invent a carrier,
date, city, or reason. If a tool returns no matching records, say so plainly
instead of guessing.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_recent_notices",
            "description": "Find the most recent out-of-service notices, optionally filtered by US state.",
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


def search_recent_notices(search_client, state=None, limit=5):
    """Response mode 1: find the most recent OOS notices, newest first."""
    filter_str = f"phy_state eq '{state}'" if state else None
    results = search_client.search(
        search_text="*",
        filter=filter_str,
        order_by=["oos_date desc"],
        top=limit,
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
        # The model wants data before it can answer. `message` here is an SDK
        # object (ChatCompletionMessage), not a plain dict like the rest of
        # our conversation history - we have to convert it to a dict
        # ourselves, in the exact shape the API expects, or both Streamlit
        # (message["role"]) and the next API call will break on it.
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
            tool_result = run_tool_call(search_client, tool_call)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )

        # Ask again, now that the model has real data to work with.
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
    # Quick command-line test, without Streamlit - useful for checking the
    # agent logic works before wiring up the UI.
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
