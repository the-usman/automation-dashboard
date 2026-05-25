from langgraph.graph import END, StateGraph

from app.src.graphs.email_outreach.nodes import (
    aggregate_results,
    check_and_refresh_token,
    fetch_credentials,
    fetch_knowledge_base,
    generate_personalized_emails,
    log_activity,
    route_after_fetch,
    route_after_fetch_preview,
    route_after_validate,
    route_after_validate_preview,
    route_content_mode,
    route_recipient_loop,
    send_email_to_recipient,
    validate_input,
)
from app.src.graphs.email_outreach.state import EmailOutreachState


def build_outreach_graph() -> StateGraph:
    """Full send graph: validate → fetch creds → token check → [optional AI gen] → send loop → aggregate → log."""
    workflow = StateGraph(EmailOutreachState)

    workflow.add_node("validate_input", validate_input)
    workflow.add_node("fetch_credentials", fetch_credentials)
    workflow.add_node("check_token", check_and_refresh_token)
    workflow.add_node("fetch_kb", fetch_knowledge_base)
    workflow.add_node("generate_emails", generate_personalized_emails)
    workflow.add_node("send_email", send_email_to_recipient)
    workflow.add_node("aggregate", aggregate_results)
    workflow.add_node("log_activity", log_activity)

    workflow.set_entry_point("validate_input")

    workflow.add_conditional_edges("validate_input", route_after_validate)
    workflow.add_conditional_edges("fetch_credentials", route_after_fetch)
    # After token check: route to KB fetch (AI mode) or directly to send (template/pre-generated)
    workflow.add_conditional_edges("check_token", route_content_mode)
    # KB fetch always leads to generation
    workflow.add_edge("fetch_kb", "generate_emails")
    # After generation, start the send loop
    workflow.add_edge("generate_emails", "send_email")
    # Recipient loop: more left → send_email (cycle), done → aggregate
    workflow.add_conditional_edges("send_email", route_recipient_loop)
    workflow.add_edge("aggregate", "log_activity")
    workflow.add_edge("log_activity", END)

    return workflow


def build_preview_graph() -> StateGraph:
    """Preview graph: validate → fetch creds → fetch KB → generate emails → END (no send)."""
    workflow = StateGraph(EmailOutreachState)

    workflow.add_node("validate_input", validate_input)
    workflow.add_node("fetch_credentials", fetch_credentials)
    workflow.add_node("fetch_kb", fetch_knowledge_base)
    workflow.add_node("generate_emails", generate_personalized_emails)

    workflow.set_entry_point("validate_input")

    workflow.add_conditional_edges("validate_input", route_after_validate_preview)
    workflow.add_conditional_edges("fetch_credentials", route_after_fetch_preview)
    workflow.add_edge("fetch_kb", "generate_emails")
    workflow.add_edge("generate_emails", END)

    return workflow


# Compiled singletons — imported by the FastAPI routes
email_outreach_graph = build_outreach_graph().compile()
email_preview_graph = build_preview_graph().compile()
