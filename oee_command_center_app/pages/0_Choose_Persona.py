"""Persona picker / landing page (SH-59, S-PERSONA-3). Leading `0_` sorts
this first in Streamlit's native multipage nav, ahead of 1_Overview.py --
see docs/designs/SH-54-55-56-59-60-52-persona-suite.md §5 for the frozen
design, including §1a's deviation footnote (dedicated page + hard gate,
not a sidebar control, per the confirmed HLD deviation).
"""

import streamlit as st

from streamlit_app import PERSONAS, _set_query_param, render_sidebar

st.set_page_config(page_title="Choose Persona", layout="wide")

render_sidebar()

st.title("Choose your persona.")
st.write(
    "Pick the role you're viewing this command center as -- this determines "
    "which agent answers your questions in Chat and whether you can file "
    "maintenance tickets."
)

current = st.session_state.get("persona")
if current:
    st.caption(f"Currently viewing as: {PERSONAS[current]['label']}")

columns = st.columns(3)
for column, (key, persona) in zip(columns, PERSONAS.items()):
    with column:
        with st.container(border=True):
            st.subheader(persona["label"])
            st.write(persona["blurb"])
            if st.button(f"Continue as {persona['label']}", key=f"persona_{key}"):
                st.session_state["persona"] = key
                _set_query_param("persona", key)
                st.switch_page("pages/1_Overview.py")
