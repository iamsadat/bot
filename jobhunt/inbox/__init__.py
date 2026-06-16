"""jobhunt.inbox — inbox watcher public surface.

Re-exports everything callers need so ``from jobhunt.inbox import X`` works
without knowing the internal module layout.
"""

from jobhunt.inbox.sources import (
    InboxMessage,
    InboxSource,
    FakeInboxSource,
    IMAPInboxSource,
    _company_from_email,
)
from jobhunt.inbox.classify import (
    Classification,
    classify_message,
)
from jobhunt.inbox.calendar import (
    CalendarHint,
    extract_calendar,
)

__all__ = [
    "InboxMessage",
    "InboxSource",
    "FakeInboxSource",
    "IMAPInboxSource",
    "Classification",
    "classify_message",
    "CalendarHint",
    "extract_calendar",
    # exposed for testing
    "_company_from_email",
]
