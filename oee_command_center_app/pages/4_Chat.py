"""Agent Chat page (SH-32/SH-59/SH-52): persona-aware chat against whichever
Cortex Agent the active persona maps to, via the Cortex Agents `agent:run`
REST API. See docs/designs/SH-27-28-30-31-32-33-semantic-view-agent-chat.md
§7 for the original frozen design and docs/designs/SH-54-55-56-59-60-52-
persona-suite.md §6/§7 for the persona-awareness, live tool introspection,
and ticket-confirmation UI added here.

DEVIATION FROM THE DESIGN DOC'S SSE SKETCH (confirmed live, 2026-09-23):
this page uses a single non-streaming JSON response (`stream: false` +
`Accept: application/json`) instead of parsing the SSE event stream
line-by-line. `_snowflake.send_snow_api_request` already returns the whole
HTTP response body as one string either way (it isn't a streaming client
itself), and the non-streaming response body is documented to be exactly
the same JSON shape as the final streamed `response` event's `data` field --
so there is no functionality lost by skipping SSE parsing in this skeleton
pass, and it avoids a much more fragile hand-rolled SSE parser.
"""

import json

import streamlit as st

from streamlit_app import PERSONAS, get_connection, render_sidebar, require_persona

st.set_page_config(page_title="Chat", layout="wide")

AGENT_DATABASE = "snowcomotive"
AGENT_SCHEMA = "cons"
REQUEST_TIMEOUT_MS = 60000

# Static display-name lookup for rendering a human-readable capability list
# under the chat caption (SH-59 §6.2/§6.3). This part is unavoidably static
# -- DESCRIBE AGENT returns tool *names*, not marketing copy -- but which
# tools are actually present/absent (the real drift risk) is always live,
# via get_agent_tool_names() below.
TOOL_DISPLAY = {
    "Analyst": "Ask about machine health, anomalies, OEE, orders, inventory, and priority/RUL.",
    "explain_prediction": "Explain why a specific prediction was flagged (feature-level breakdown).",
    "create_jira_ticket": "File a maintenance ticket for a machine.",
    "request_jira_ticket": "Escalate/request maintenance attention on a machine.",
}

# Tool names whose tool_result content this page renders a distinct
# confirmation for (SH-52, §7) -- both point at the same underlying
# SP_CREATE_JIRA_TICKET procedure, just under different tool_spec names
# per persona (Module 7 §3's "same API, different framing").
TICKET_TOOL_NAMES = {"create_jira_ticket", "request_jira_ticket"}


@st.cache_data(ttl=300)
def get_agent_tool_names(agent_name: str) -> list[str]:
    """Live introspection, not a hardcoded persona->tool dict: parses
    DESCRIBE AGENT's `agent_spec` column (a JSON string matching the body
    passed to `FROM SPECIFICATION $$ ... $$`) so the capability list can
    never drift from what's actually wired on the live agent object.

    Live-verified shape (this session, against snow-co-cat-alyst-
    snowcomotive): `DESCRIBE AGENT <name>` returns a row with an
    `agent_spec` column holding a JSON (not YAML) string; its top-level
    `tools` list holds `{"tool_spec": {"name": ..., ...}}` entries.

    DEVIATION FROM THE DESIGN DOC'S SKETCH (confirmed live, this session):
    `conn.query(...)` (Streamlit's `SnowflakeConnection.query`) calls
    `cursor.fetch_pandas_all()` internally, which raises
    `NotSupportedError` for `DESCRIBE AGENT` -- it isn't a SELECT and
    doesn't support Arrow-format results, unlike every other `conn.query()`
    call elsewhere in this app. Using `conn.cursor()` directly (same
    pattern as `get_connection()`'s own `USE ...` statements and the Chat
    page's `_call_agent_local_dev`) avoids the pandas/Arrow path entirely
    while still being a live `DESCRIBE AGENT` call -- this is not the
    static-dict fallback §9 item 1 flags as the user-declined alternative;
    DESCRIBE AGENT's output is still parsed live, just fetched differently.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"DESCRIBE AGENT {AGENT_DATABASE}.{AGENT_SCHEMA}.{agent_name}")
        row = cur.fetchone()
        columns = [d[0] for d in cur.description]
    finally:
        cur.close()
    spec = json.loads(row[columns.index("agent_spec")])
    return [tool["tool_spec"]["name"] for tool in spec.get("tools", [])]


def _extract_text(content: list[dict]) -> str:
    """Concatenate every text content item in the agent's response.
    Tool-call content items (Analyst's generated SQL) are not deeply
    rendered here -- ticket tool results get their own confirmation UI via
    _extract_ticket_results() instead."""
    return "\n\n".join(item["text"] for item in content if item.get("type") == "text")


def _extract_ticket_results(content: list[dict]) -> list[dict]:
    """Scans the response's tool_result content items for create_jira_ticket/
    request_jira_ticket results and returns the decoded procedure response
    (`{"status": ..., "ticket_id": ..., "ticket_url": ...}`) for each.

    Live-verified shape (this session, against snow-co-cat-alyst-
    snowcomotive, via maintenance_supervisor_agent/create_jira_ticket and
    production_planner_agent/request_jira_ticket): a `tool_result`-typed
    content item has `tool_result.name` (the tool name) and
    `tool_result.content`, a list of `{"type": "json", "json": {...}}`
    items whose `json.result` field is itself a JSON-*encoded string* (not
    a nested object) holding the procedure's actual `{"status", "ticket_id",
    "ticket_url"}` response -- must be json.loads()'d again to reach it.
    This differs from the design doc's illustrative sketch (§9 item 2's
    flagged assumption), which is why this second json.loads() is needed.
    """
    results = []
    for item in content:
        if item.get("type") != "tool_result":
            continue
        tool_result = item.get("tool_result", {})
        if tool_result.get("name") not in TICKET_TOOL_NAMES:
            continue
        for result_item in tool_result.get("content", []):
            raw = result_item.get("json", {}).get("result")
            if not raw:
                continue
            try:
                results.append(json.loads(raw))
            except (TypeError, ValueError):
                continue
    return results


def _call_agent_in_snowflake(agent_run_path: str, body: dict) -> dict:
    import _snowflake  # only importable inside Streamlit-in-Snowflake

    resp = _snowflake.send_snow_api_request(
        "POST", agent_run_path, {}, {}, body, None, REQUEST_TIMEOUT_MS
    )
    if resp["status"] >= 400:
        raise RuntimeError(f"Agent API error (status {resp['status']}): {resp['content']}")
    return json.loads(resp["content"])


def _call_agent_local_dev(agent_run_path: str, body: dict) -> dict:
    """Local-dev-only fallback -- `_snowflake` isn't importable outside
    Streamlit-in-Snowflake, so call the same REST path directly with
    `requests`, authenticated with the connector session's own token."""
    import requests

    raw_conn = get_connection().raw_connection
    url = f"https://{raw_conn.host}{agent_run_path}"
    headers = {
        "Authorization": f'Snowflake Token="{raw_conn.rest.token}"',
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    resp = requests.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT_MS / 1000)
    if resp.status_code >= 400:
        raise RuntimeError(f"Agent API error (status {resp.status_code}): {resp.text}")
    return resp.json()


def call_agent(agent_run_path: str, messages: list[dict]) -> list[dict]:
    """Sends the full chat history to the agent and returns its raw response
    content list (text, tool_use, and tool_result items), or raises on any
    failure -- the caller surfaces this via st.error rather than fabricating
    a response (design doc invariant 5)."""
    body = {"messages": messages, "stream": False}
    try:
        import _snowflake  # noqa: F401

        data = _call_agent_in_snowflake(agent_run_path, body)
    except ImportError:
        data = _call_agent_local_dev(agent_run_path, body)
    return data.get("content", [])


def render_ticket_confirmation(ticket_result: dict) -> None:
    """Renders a distinct confirmation for a create_jira_ticket/
    request_jira_ticket result, independent of how the agent's own prose
    worded it (SH-52, §7). `ticket_url` is always null per SH-58's
    invariant -- never rendered as a link."""
    status = ticket_result.get("status")
    ticket_id = ticket_result.get("ticket_id")
    if status == "CREATED":
        st.success(f"Ticket {ticket_id} created.")
    elif status == "ALREADY_OPEN":
        st.info(f"Machine already has an open ticket: {ticket_id}.")
    elif status == "RECENTLY_CLOSED":
        st.info(
            f"A ticket for this machine was recently closed: {ticket_id}. "
            "Filing a new one may be worth reconsidering."
        )


render_sidebar()
persona = require_persona()
agent_name = PERSONAS[persona]["agent_name"]
agent_run_path = f"/api/v2/databases/{AGENT_DATABASE}/schemas/{AGENT_SCHEMA}/agents/{agent_name}:run"

st.title("Chat")
st.caption(f"Chatting as {PERSONAS[persona]['label']} -- {PERSONAS[persona]['blurb']}")
for tool_name in get_agent_tool_names(agent_name):
    st.caption(f"• {TOOL_DISPLAY.get(tool_name, tool_name)}")

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

for message in st.session_state.chat_history:
    with st.chat_message(message["role"]):
        st.markdown(message["text"])
        for ticket_result in message.get("ticket_results", []):
            render_ticket_confirmation(ticket_result)

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
                content = call_agent(agent_run_path, agent_messages)
        except Exception as exc:
            st.error(f"Agent request failed: {exc}")
        else:
            response_text = _extract_text(content)
            ticket_results = _extract_ticket_results(content)
            st.markdown(response_text)
            for ticket_result in ticket_results:
                render_ticket_confirmation(ticket_result)
            st.session_state.chat_history.append(
                {"role": "assistant", "text": response_text, "ticket_results": ticket_results}
            )
