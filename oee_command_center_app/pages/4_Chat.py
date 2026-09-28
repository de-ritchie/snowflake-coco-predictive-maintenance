"""Agent Chat page (SH-32/SH-59/SH-52): persona-aware chat against whichever
Cortex Agent the active persona maps to, via the Cortex Agents `agent:run`
REST API. See docs/designs/SH-27-28-30-31-32-33-semantic-view-agent-chat.md
§7 for the original frozen design and docs/designs/SH-54-55-56-59-60-52-
persona-suite.md §6/§7 for the persona-awareness, live tool introspection,
and ticket-confirmation UI added here. See
docs/designs/SH-62-mcp-swap-jira.md §8 for the MCP-server introspection and
MCP-real ticket-confirmation rendering added here (SH-62).

Chat history is keyed per-persona (`st.session_state.chat_histories[persona]`),
not one shared list -- switching personas must not leak one persona's
conversation/tool availability into another's. A "Clear chat" button lets the
user reset the *current* persona's history without switching personas
(post-merge manual-testing fix, 2026-09-27).

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
}

# Companion to TOOL_DISPLAY, for mcp_servers: attachments (SH-62, §8.1) --
# a separate top-level key in DESCRIBE AGENT's agent_spec JSON (mcp_servers,
# not tools), so it needs its own display-name lookup.
MCP_SERVER_DISPLAY = {
    "snowcomotive.cons.jira_mcp_server": "Create a real Jira ticket, or look up recent tickets for a machine, via the live Jira connector.",
}


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


@st.cache_data(ttl=300)
def get_agent_mcp_server_names(agent_name: str) -> list[str]:
    """Companion to get_agent_tool_names() (SH-62, §8.1) -- MCP servers are
    a separate top-level key in DESCRIBE AGENT's agent_spec JSON
    (mcp_servers, not tools), so they need their own extraction, or the
    capability caption silently omits any MCP-backed capability entirely.

    Live-verified shape (design doc §9's "what CAN and already WAS
    live-verified" list): the `mcp_servers` key is confirmed present and
    correctly shaped in `DESCRIBE AGENT`'s `agent_spec` JSON output, same
    `mcp_servers: - server_spec: name: "..."` shape as the YAML passed to
    `FROM SPECIFICATION`.
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
    return [server["server_spec"]["name"] for server in spec.get("mcp_servers", [])]


def _extract_text(content: list[dict]) -> str:
    """Concatenate every text content item in the agent's response.
    Tool-call content items (Analyst's generated SQL) are not deeply
    rendered here -- MCP-real ticket tool results get their own
    confirmation UI via _extract_mcp_ticket_results() instead."""
    return "\n\n".join(item["text"] for item in content if item.get("type") == "text")


# --- MCP-real ticket-confirmation path (SH-62, §8.2) --------------------
# Structurally its own path -- rendering here does not fabricate a
# ticket_url when the tool didn't actually return one.
#
# FLAGGED, NOT LIVE-CONFIRMED (design doc §9 item 2/item 5): the real
# Atlassian-hosted MCP server's actual tool name(s) for ticket creation/
# search, and the exact tool_result content-item shape for an
# mcp_servers:-attached server (as opposed to a `generic`-type custom
# tool), were not live-verified in the design session -- only DDL/agent-
# attachment plumbing was. This placeholder set is intentionally empty
# until a human completes `manage.py authorize-jira-mcp` (§4) and the
# real tool name(s) are observed live via a `DESCRIBE AGENT`/`agent:run`
# call or Snowsight's agent tool-testing UI -- see this story's report for
# the exact manual-verification step required before this set can be
# populated and this rendering path becomes reachable.
MCP_TICKET_TOOL_NAMES: set[str] = set()


def _extract_mcp_ticket_results(content: list[dict]) -> list[dict]:
    """Scans the response's tool_result content items for MCP-real ticket
    results (MCP_TICKET_TOOL_NAMES). Uses the same double-json.loads()
    defensive pattern as a starting point, but this is NOT guaranteed to
    match the real MCP tool-result shape (design doc §9 item 5) -- must be
    reconciled against a live response once OAuth consent (§4) is
    complete and MCP_TICKET_TOOL_NAMES above is populated with the real
    tool name(s)."""
    results = []
    for item in content:
        if item.get("type") != "tool_result":
            continue
        tool_result = item.get("tool_result", {})
        if tool_result.get("name") not in MCP_TICKET_TOOL_NAMES:
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


def render_mcp_ticket_confirmation(ticket_result: dict) -> None:
    """Renders a distinct confirmation for a real, MCP-backed Jira ticket
    result (SH-62, §8.2). Unlike the now-retired simulated store, a real
    MCP ticket's `ticket_url` is a real, valid Jira browse link and IS
    rendered as a clickable link when present."""
    ticket_key = ticket_result.get("ticket_id") or ticket_result.get("key")
    ticket_url = ticket_result.get("ticket_url")
    st.success(f"Real Jira ticket: {ticket_key}")
    if ticket_url:
        st.markdown(f"[View in Jira]({ticket_url})")


render_sidebar()
persona = require_persona()
agent_name = PERSONAS[persona]["agent_name"]
agent_run_path = f"/api/v2/databases/{AGENT_DATABASE}/schemas/{AGENT_SCHEMA}/agents/{agent_name}:run"

title_col, clear_col = st.columns([6, 1])
with title_col:
    st.title("Chat")
with clear_col:
    st.button("Clear chat", on_click=lambda: st.session_state.chat_histories.pop(persona, None))
st.caption(f"Chatting as {PERSONAS[persona]['label']} -- {PERSONAS[persona]['blurb']}")
for tool_name in get_agent_tool_names(agent_name):
    st.caption(f"• {TOOL_DISPLAY.get(tool_name, tool_name)}")
for server_name in get_agent_mcp_server_names(agent_name):
    st.caption(f"• {MCP_SERVER_DISPLAY.get(server_name, server_name)}")

# Chat history is keyed by persona, not a single flat list -- switching
# personas must not leak one persona's conversation into another's, since
# each persona maps to a different agent with different tools/framing.
# An explicit "Clear chat" button (above) additionally lets the user reset
# the *current* persona's history without switching personas at all.
if "chat_histories" not in st.session_state:
    st.session_state.chat_histories = {}
chat_history = st.session_state.chat_histories.setdefault(persona, [])

for message in chat_history:
    with st.chat_message(message["role"]):
        st.markdown(message["text"])
        for mcp_ticket_result in message.get("mcp_ticket_results", []):
            render_mcp_ticket_confirmation(mcp_ticket_result)

user_input = st.chat_input("Ask a question...")
if user_input:
    chat_history.append({"role": "user", "text": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    agent_messages = [
        {"role": m["role"], "content": [{"type": "text", "text": m["text"]}]}
        for m in chat_history
    ]

    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking..."):
                content = call_agent(agent_run_path, agent_messages)
        except Exception as exc:
            st.error(f"Agent request failed: {exc}")
        else:
            response_text = _extract_text(content)
            mcp_ticket_results = _extract_mcp_ticket_results(content)
            st.markdown(response_text)
            for mcp_ticket_result in mcp_ticket_results:
                render_mcp_ticket_confirmation(mcp_ticket_result)
            chat_history.append(
                {
                    "role": "assistant",
                    "text": response_text,
                    "mcp_ticket_results": mcp_ticket_results,
                }
            )
