"""IDX disclosure watcher package.

V1 is isolated from trading decisions. It collects and normalizes official IDX
announcements and may notify the existing Telegram news topic only when delivery
is explicitly enabled.
"""

from .client import IDXAnnouncementClient, IDXClientError
from .models import DisclosureAttachment, IDXDisclosure
from .repository import SQLiteDisclosureRepository
from .watcher import IDXDisclosureWatcher, PollResult
from .groq_json_mode import install_groq_json_mode_patch

# Keep the existing public reader API while hardening Groq GPT-OSS output to
# valid JSON. This patch stays entirely inside the isolated IDX package.
install_groq_json_mode_patch()

__all__ = [
    "DisclosureAttachment",
    "IDXAnnouncementClient",
    "IDXClientError",
    "IDXDisclosure",
    "IDXDisclosureWatcher",
    "PollResult",
    "SQLiteDisclosureRepository",
]
