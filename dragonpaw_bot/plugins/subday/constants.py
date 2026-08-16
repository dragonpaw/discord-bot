from pathlib import Path

TOTAL_WEEKS = 52
MILESTONE_WEEKS = (13, 26, 39, 52)
WEEKS_DIR = Path(__file__).parent / "weeks"


def next_milestone(week: int) -> int | None:
    """The first milestone at or after this week, or None past the last."""
    return next((m for m in MILESTONE_WEEKS if m >= week), None)


# Component interaction custom IDs
SUBDAY_SIGNUP_ID = "subday_signup"
SUBDAY_ABOUT_ID = "subday_about"
SUBDAY_CONFIG_PREFIX = "subday_cfg:"
SUBDAY_CFG_ROLE_PREFIX = "subday_cfg_role:"
SUBDAY_OWNER_REQUEST_PREFIX = "subday_owner_request:"
MAX_EMBEDS_PER_MESSAGE = 10
