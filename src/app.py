"""app.py - Streamlit chat UI wrapping agent.py. Run with: streamlit run app.py"""

import streamlit as st

from agent import get_clients, chat_with_agent, SYSTEM_PROMPT

st.set_page_config(page_title="Sales Opportunity Agent", page_icon="🚚")
st.title("🚚 Sales Opportunity Agent")
st.caption("Ask about recent out-of-service carriers, or ask for an outreach script for one of them.")

# Only create the Azure clients once per browser session, not on every re-run.
if "clients" not in st.session_state:
    st.session_state.clients = get_clients()

# Only start a fresh conversation once per browser session.
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

search_client, openai_client = st.session_state.clients

# Re-draw the whole conversation so far (skip the hidden system prompt).
for message in st.session_state.messages:
    if message["role"] in ("user", "assistant"):
        with st.chat_message(message["role"]):
            st.write(message["content"])

# The chat input box at the bottom of the page.
user_input = st.chat_input("e.g. Summarize the last 5 notices in Texas")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Searching..."):
            reply, st.session_state.messages = chat_with_agent(
                search_client, openai_client, st.session_state.messages
            )
        st.write(reply)
