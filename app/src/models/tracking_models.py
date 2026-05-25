from pydantic import BaseModel


class TrackingRecord(BaseModel):
    tracking_id: str
    credential_id: str
    recipient_email: str
    recipient_name: str
    subject: str
    message_id: str | None = None
    thread_id: str | None = None
    sent_at: str
    opened: bool = False
    opened_at: str | None = None
    replied: bool = False
    replied_at: str | None = None
    reply_subject: str | None = None
    reply_preview: str | None = None
    follow_up_sent: bool = False
    follow_up_sent_at: str | None = None
    is_follow_up: bool = False
    calendar_credential_id: str | None = None
    meeting_link: str | None = None


class ReplyCheckRequest(BaseModel):
    credential_id: str
    days: int = 7


class ReplyInfo(BaseModel):
    tracking_id: str
    recipient_email: str
    reply_subject: str
    reply_preview: str
    replied_at: str
    meeting_link: str | None = None


class ReplyCheckResponse(BaseModel):
    credential_id: str
    new_replies: list[ReplyInfo]
    total_checked: int


class TrackingStatusResponse(BaseModel):
    credential_id: str
    records: list[TrackingRecord]
    total: int
    opened: int
    replied: int
