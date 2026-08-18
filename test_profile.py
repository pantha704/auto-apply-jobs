"""Legacy filename retained for compatibility.

The old version launched a private stock-Chrome profile. Public tests must never
open a user's profile, require a GUI, or use a browser channel. The real test
suite lives under tests/ and uses fixtures/mocks unless explicitly marked live.
"""

__test__ = False
