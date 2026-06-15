from pydantic import BaseModel, model_validator


class RecipientWithDescription(BaseModel):
    email: str
    name: str = ""
    description: str = ""  # recipient background for AI personalization


class GeneratedEmail(BaseModel):
    recipient_email: str
    recipient_name: str
    subject: str
    body_html: str
    body_text: str = ""


class EmailPreviewRequest(BaseModel):
    credential_id: str
    campaign_description: str
    knowledge_base_id: str = ""
    recipients: list[RecipientWithDescription]

    @model_validator(mode="after")
    def _check(self):
        if not self.recipients:
            raise ValueError("recipients must not be empty")
        if not self.campaign_description.strip():
            raise ValueError("campaign_description is required")
        return self


class EmailPreviewResponse(BaseModel):
    emails: list[GeneratedEmail]


class EmailOutreachRequest(BaseModel):
    credential_id: str
    recipients: list[RecipientWithDescription]

    # ── Content modes (at least one must be provided) ─────────────────────────
    # Mode 1 – template: one subject+body applied to all recipients ({{name}} substitution)
    subject: str = ""
    body_html: str = ""
    body_text: str = ""

    # Mode 2 – AI generation: campaign + KB → LLM generates per-recipient emails
    campaign_description: str = ""
    knowledge_base_id: str = ""

    # Mode 3 – pre-generated: pass emails from /outreach/preview directly
    generated_emails: list[GeneratedEmail] = []

    # ── Shared send options ────────────────────────────────────────────────────
    from_name: str = ""
    reply_to: str = ""
    cc: list[str] = []
    bcc: list[str] = []
    # If set, a calendar meeting is auto-created when the lead replies
    calendar_credential_id: str = ""

    @model_validator(mode="after")
    def _check_content_mode(self):
        if not self.recipients:
            raise ValueError("recipients must not be empty")
        has_template = bool(self.subject and self.body_html)
        has_campaign = bool(self.campaign_description.strip())
        has_generated = bool(self.generated_emails)
        if not has_template and not has_campaign and not has_generated:
            raise ValueError(
                "Provide either subject+body_html (template), "
                "campaign_description (AI mode), or generated_emails (pre-generated)"
            )
        return self


class SendResult(BaseModel):
    recipient_email: str
    recipient_name: str
    provider: str
    status: str        # "sent" | "failed"
    message_id: str | None = None
    subject: str = ""
    error: str | None = None
    tracking_id: str | None = None
    sent_at: str


class OutreachSummary(BaseModel):
    total: int
    sent: int
    failed: int


class EmailOutreachResponse(BaseModel):
    success: bool
    summary: OutreachSummary
    results: list[SendResult]


class GenerateAndSendRequest(BaseModel):
    credential_id: str
    campaign_description: str
    recipients: list[RecipientWithDescription]
    knowledge_base_id: str = ""
    from_name: str = ""
    reply_to: str = ""
    cc: list[str] = []
    bcc: list[str] = []
    calendar_credential_id: str = ""

    @model_validator(mode="after")
    def _check(self):
        if not self.recipients:
            raise ValueError("recipients must not be empty")
        if not self.campaign_description.strip():
            raise ValueError("campaign_description is required")
        return self


class CampaignCreateRequest(BaseModel):
    credential_id: str
    campaign_description: str
    recipients: list[RecipientWithDescription]
    knowledge_base_id: str = ""
    from_name: str = ""
    reply_to: str = ""
    cc: list[str] = []
    bcc: list[str] = []
    calendar_credential_id: str = ""

    @model_validator(mode="after")
    def _check(self):
        if not self.recipients:
            raise ValueError("recipients must not be empty")
        if not self.campaign_description.strip():
            raise ValueError("campaign_description is required")
        return self


class CampaignCreateResponse(BaseModel):
    campaign_id: str
    status: str   # always "running"
    total: int


class CampaignStatusResponse(BaseModel):
    campaign_id: str
    status: str   # "running" | "completed" | "stopped" | "failed"
    total: int
    sent: int
    failed: int
    current_index: int
    results: list[SendResult]
    started_at: str
    completed_at: str | None = None
