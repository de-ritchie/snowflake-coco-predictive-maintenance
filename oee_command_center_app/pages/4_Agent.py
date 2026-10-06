"""Agent page (SH-74): persona-aware agent chat with draft/commit session model.

Sessions only appear in Chat History once the user has actually sent a message
and received a response (the "draft/commit" model). Before that, the active
view is an uncommitted draft that exists only for rendering.

Preserves the real Cortex Agent backend from SH-32/SH-59/SH-52/SH-62.
Chat history is per-persona (invariant #5).
See docs/designs/SH-70-streamlit-ux-revamp.md $4.5.
"""

import json
import time

import streamlit as st

from streamlit_app import PERSONAS, get_connection, render_sidebar, require_persona

st.set_page_config(page_title="SnowComotive", layout="wide")

AGENT_DATABASE = "snowcomotive"
AGENT_SCHEMA = "cons"
REQUEST_TIMEOUT_MS = 60000

PERSONA_GUARDRAILS = {
    "supervisor": "Can dispatch maintenance, create Jira tickets, and view sensor-level detail.",
    "planner": "Can view demand/capacity data and escalate maintenance requests — cannot dispatch directly.",
    "plant_manager": "Read-only OEE/health/inventory rollup — no ticketing or dispatch capability.",
}

PERSONA_WELCOME = {
    "supervisor": (
        "Hi, I'm here to help with hands-on machine health, root-cause investigation, "
        "and maintenance dispatch — I can check sensor readings, surface anomalies, "
        "review priority/RUL, and create real Jira tickets when needed. What would you "
        "like to look into?"
    ),
    "planner": (
        "Hi, I'm here to help with demand, inventory, and OEE risk analysis — I can "
        "pull capacity data, and flag scheduling conflicts. "
        "What do you need?"
    ),
    "plant_manager": (
        "Hi, I'm here to help with a read-only view of OEE, machine health, and "
        "inventory rollups across the plant. What would you like to review?"
    ),
}

DRAFT_SENTINEL = "__draft__"


def _make_welcome_message(persona_key: str) -> dict:
    return {"role": "assistant", "text": PERSONA_WELCOME[persona_key], "is_welcome": True}


def _new_draft(persona_key: str) -> dict:
    return {"id": DRAFT_SENTINEL, "title": "", "messages": [_make_welcome_message(persona_key)]}


def _extract_text(content: list[dict]) -> str:
    return "\n\n".join(item["text"] for item in content if item.get("type") == "text")


MCP_TICKET_TOOL_NAMES: set[str] = set()


def _extract_mcp_ticket_results(content: list[dict]) -> list[dict]:
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
    import _snowflake
    resp = _snowflake.send_snow_api_request(
        "POST", agent_run_path, {}, {}, body, None, REQUEST_TIMEOUT_MS
    )
    if resp["status"] >= 400:
        raise RuntimeError(f"Agent API error (status {resp['status']}): {resp['content']}")
    return json.loads(resp["content"])


def _call_agent_local_dev(agent_run_path: str, body: dict) -> dict:
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
    body = {"messages": messages, "stream": False}
    try:
        import _snowflake  # noqa: F401
        data = _call_agent_in_snowflake(agent_run_path, body)
    except ImportError:
        data = _call_agent_local_dev(agent_run_path, body)
    return data.get("content", [])


def render_mcp_ticket_confirmation(ticket_result: dict) -> None:
    ticket_key = ticket_result.get("ticket_id") or ticket_result.get("key")
    ticket_url = ticket_result.get("ticket_url")
    st.success(f"Real Jira ticket: {ticket_key}")
    if ticket_url:
        st.markdown(f"[View in Jira]({ticket_url})")


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------

def _get_cowork_url() -> str:
    conn = get_connection()
    row = conn.query(
        "SELECT CURRENT_ACCOUNT() AS account_locator, CURRENT_REGION() AS region",
        ttl=0,
    ).iloc[0]
    account_locator = row["ACCOUNT_LOCATOR"].lower()
    region_slug = "-".join(row["REGION"].split("_")[1:]).lower()
    return f"https://ai.snowflake.com/{region_slug}/{account_locator}#/ai"


render_sidebar()
persona = require_persona()

st.title(f"SnowComotive {PERSONAS[persona]['label']} Agent")
st.caption("To switch persona and use a different agent, go back to the home page.")

if persona == "supervisor":
    st.warning(
        "This Maintenance Supervisor Agent is temporarily unavailable in Streamlit due to a known "
        "Snowflake platform limitation: Streamlit-in-Snowflake can't "
        "authenticate to third-party MCP connectors (e.g. Jira) required for "
        "maintenance dispatch (the rest of the agents work). See "
        "[Snowflake's documentation](https://docs.snowflake.com/en/user-guide/"
        "snowflake-cortex/cortex-agents-mcp-connectors) for details.\n\n"
        "Please use Snowflake CoWork to continue this conversation."
    )
    try:
        st.markdown(f"[Open Snowflake CoWork]({_get_cowork_url()})")
    except Exception as exc:
        st.error(f"Could not determine CoWork URL: {exc}")
    st.stop()

agent_name = PERSONAS[persona]["agent_name"]
agent_run_path = f"/api/v2/databases/{AGENT_DATABASE}/schemas/{AGENT_SCHEMA}/agents/{agent_name}:run"

# Per-persona committed sessions list
if "chat_sessions" not in st.session_state:
    st.session_state.chat_sessions = {}
if persona not in st.session_state.chat_sessions:
    st.session_state.chat_sessions[persona] = []

# Per-persona draft (uncommitted session)
if "chat_draft" not in st.session_state:
    st.session_state.chat_draft = {}
if persona not in st.session_state.chat_draft:
    st.session_state.chat_draft[persona] = _new_draft(persona)

# Active view: DRAFT_SENTINEL means viewing the draft, otherwise a committed session id
if "active_session_id" not in st.session_state:
    st.session_state.active_session_id = {}
if persona not in st.session_state.active_session_id:
    st.session_state.active_session_id[persona] = DRAFT_SENTINEL

sessions = st.session_state.chat_sessions[persona]
active_id = st.session_state.active_session_id[persona]

# Resolve active view: draft or committed session
viewing_draft = active_id == DRAFT_SENTINEL
if viewing_draft:
    active_session = st.session_state.chat_draft[persona]
else:
    active_session = next((s for s in sessions if s["id"] == active_id), None)
    if active_session is None:
        # Stale id — fall back to draft
        st.session_state.active_session_id[persona] = DRAFT_SENTINEL
        active_session = st.session_state.chat_draft[persona]
        viewing_draft = True

chat_history = active_session["messages"]

# --- Layout: sidebar history | main chat ------------------------------------

chat_col, history_col = st.columns([4, 1])

with history_col:
    if st.button("New Chat", use_container_width=True):
        # If already on an empty draft, no-op
        draft_has_user_msg = any(
            m["role"] == "user" for m in st.session_state.chat_draft[persona]["messages"]
        )
        if not (viewing_draft and not draft_has_user_msg):
            st.session_state.chat_draft[persona] = _new_draft(persona)
            st.session_state.active_session_id[persona] = DRAFT_SENTINEL
            st.rerun()

    st.markdown("**Chat History**")
    for session in sessions:
        label = session["title"] or "Untitled"
        if not viewing_draft and session["id"] == active_id:
            label = f"▸ {label}"
        if st.button(label, key=f"hist_{session['id']}", use_container_width=True):
            st.session_state.active_session_id[persona] = session["id"]
            # Discard the current draft (replace with fresh one)
            st.session_state.chat_draft[persona] = _new_draft(persona)
            st.rerun()

with chat_col:
    for message in chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["text"])
            for mcp_ticket_result in message.get("mcp_ticket_results", []):
                render_mcp_ticket_confirmation(mcp_ticket_result)

# Chat input at top level so Streamlit pins it to the bottom of the viewport
user_input = st.chat_input("Ask a question...")
if user_input:
    chat_history.append({"role": "user", "text": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Exclude welcome messages from agent context
    agent_messages = [
        {"role": m["role"], "content": [{"type": "text", "text": m["text"]}]}
        for m in chat_history
        if not m.get("is_welcome")
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

            # Commit the draft on successful response
            if viewing_draft:
                first_user_msg = next(
                    (m["text"] for m in chat_history if m["role"] == "user"), ""
                )
                title = first_user_msg[:40] + ("..." if len(first_user_msg) > 40 else "")
                new_id = f"session_{int(time.time() * 1000)}"
                active_session["id"] = new_id
                active_session["title"] = title
                sessions.append(active_session)
                st.session_state.active_session_id[persona] = new_id
                # Replace draft with a fresh one for next time
                st.session_state.chat_draft[persona] = _new_draft(persona)
                # Rerun so the history column re-renders with the new entry
                st.rerun()
