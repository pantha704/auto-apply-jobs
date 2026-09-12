"""Shared non-pro CloakBrowser path. Never silently fall back to stock Chromium."""
import os
from pathlib import Path
_installed=sorted((Path.home()/'.cloakbrowser').glob('chromium-*/chrome'),key=lambda p:p.stat().st_mtime,reverse=True)
BROWSER_EXECUTABLE=os.getenv('JOBHUNT_BROWSER_EXECUTABLE') or str(_installed[0] if _installed else Path.home()/'.cloakbrowser'/'chrome')
