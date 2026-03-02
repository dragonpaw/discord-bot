# -*- coding: utf-8 -*-
from pathlib import Path

TOTAL_WEEKS = 52
MILESTONE_WEEKS = (13, 26, 39, 52)
WEEKS_DIR = Path(__file__).parent / "weeks"

# Component interaction custom IDs
SUBDAY_SIGNUP_ID = "subday_signup"
SUBDAY_CONFIG_PREFIX = "subday_cfg:"
SUBDAY_CFG_ROLE_PREFIX = "subday_cfg_role:"
SUBDAY_OWNER_REQUEST_PREFIX = "subday_owner_request:"
MAX_EMBEDS_PER_MESSAGE = 10
