"""Operator identity for apply forms. Secrets live in profile_local.py (gitignored) or env."""
import os

NAME = os.environ.get("JOBHUNT_NAME", "")
EMAIL = os.environ.get("JOBHUNT_EMAIL", "")
PHONE = os.environ.get("JOBHUNT_PHONE", "")
ADDRESS = os.environ.get("JOBHUNT_ADDRESS", "")
CITY = os.environ.get("JOBHUNT_CITY", "")
STATE = os.environ.get("JOBHUNT_STATE", "")
PIN = os.environ.get("JOBHUNT_PIN", "")
COLLEGE = os.environ.get("JOBHUNT_COLLEGE", "")
DEGREE = os.environ.get("JOBHUNT_DEGREE", "")

try:
    import profile_local as _pl  # type: ignore
    NAME = getattr(_pl, "NAME", NAME)
    EMAIL = getattr(_pl, "EMAIL", EMAIL)
    PHONE = getattr(_pl, "PHONE", PHONE)
    ADDRESS = getattr(_pl, "ADDRESS", ADDRESS)
    CITY = getattr(_pl, "CITY", CITY)
    STATE = getattr(_pl, "STATE", STATE)
    PIN = getattr(_pl, "PIN", PIN)
    COLLEGE = getattr(_pl, "COLLEGE", COLLEGE)
    DEGREE = getattr(_pl, "DEGREE", DEGREE)
except ImportError:
    pass
