"""Agent Chat page (SH-32, S-APP-2): single-agent chat against
snowcomotive.cons.maintenance_supervisor_agent via the Cortex Agents
`agent:run` REST API. See docs/designs/SH-27-28-30-31-32-33-semantic-view-
agent-chat.md §7 for the frozen design and §8 item 4 for the call-mechanism
risk this page resolves.

DEVIATION FROM THE DESIGN DOC'S SSE SKETCH (confirmed live, 2026-09-23):
this page uses a single non-streaming JSON response (`stream: false` +
`Accept: application/json`) instead of parsing the SSE event stream
line-by-line. `_snowflake.send_snow_api_request` already returns the whole
HTTP response body as one string either way (it isn't a streaming client
itself), and the non-streaming response body is documented to be exactly
the same JSON shape as the final streamed `response` event's `data` field --
so there is no functionality lost by skipping SSE parsing in this skeleton
pass (no tool-call/citation rendering yet regardless, per the design doc's
own §11 deferral), and it avoids a much more fragile hand-rolled SSE parser.
"""

import json

import streamlit as st

from streamlit_app import get_connection, render_sidebar

st.set_page_config(page_title="Chat", layout="wide")

AGENT_DATABASE = "snowcomotive"
AGENT_SCHEMA = "cons"
AGENT_NAME = "maintenance_supervisor_agent"
AGENT_RUN_PATH = f"/api/v2/databases/{AGENT_DATABASE}/schemas/{AGENT_SCHEMA}/agents/{AGENT_NAME}:run"
REQUEST_TIMEOUT_MS = 60000


def _extract_text(content: list[dict]) -> str:
    """Concatenate every text content item in the agent's response.
    Tool-call content items (Analyst's generated SQL, if surfaced) are not
    deeply rendered in this skeleton pass -- deferred per the frozen design
    doc §7/§11 until a tool with a meaningful structured result exists."""
    return "\n\n".join(item["text"] for item in content if item.get("type") == "text")


def _call_agent_in_snowflake(body: dict) -> dict:
    import _snowflake  # only importable inside Streamlit-in-Snowflake

    resp = _snowflake.send_snow_api_request(
        "POST", AGENT_RUN_PATH, {}, {}, body, None, REQUEST_TIMEOUT_MS
    )
    if resp["status"] >= 400:
        raise RuntimeError(f"Agent API error (status {resp['status']}): {resp['content']}")
    return json.loads(resp["content"])


def _call_agent_local_dev(body: dict) -> dict:
    """Local-dev-only fallback -- `_snowflake` isn't importable outside
    Streamlit-in-Snowflake, so call the same REST path directly with
    `requests`, authenticated with the connector session's own token."""
    import requests

    raw_conn = get_connection().raw_connection
    url = f"https://{raw_conn.host}{AGENT_RUN_PATH}"
    headers = {
        "Authorization": f'Snowflake Token="{raw_conn.rest.token}"',
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    resp = requests.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT_MS / 1000)
    if resp.status_code >= 400:
        raise RuntimeError(f"Agent API error (status {resp.status_code}): {resp.text}")
    return resp.json()


def call_agent(messages: list[dict]) -> str:
    """Sends the full chat history to the agent and returns its final text
    response, or raises on any failure -- the caller surfaces this via
    st.error rather than fabricating a response (design doc invariant 5)."""
    body = {"messages": messages, "stream": False}
    try:
        import _snowflake  # noqa: F401

        data = _call_agent_in_snowflake(body)
    except ImportError:
        data = _call_agent_local_dev(body)
    return _extract_text(data.get("content", []))


render_sidebar()
st.title("Chat")
st.caption(
    "Ask the SnowComotive Maintenance Agent about machine health, anomalies, "
    "maintenance, OEE, orders, or inventory."
)

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

for message in st.session_state.chat_history:
    with st.chat_message(message["role"]):
        st.markdown(message["text"])

user_input = st.chat_input("Ask a question...")
if user_input:
    st.session_state.chat_history.append({"role": "user", "text": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    agent_messages = [
        {"role": m["role"], "content": [{"type": "text", "text": m["text"]}]}
        for m in st.session_state.chat_history
    ]

    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking..."):
                response_text = call_agent(agent_messages)
        except Exception as exc:
            st.error(f"Agent request failed: {exc}")
        else:
            st.markdown(response_text)
            st.session_state.chat_history.append({"role": "assistant", "text": response_text})
