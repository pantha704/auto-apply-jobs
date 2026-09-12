"""Unified locator/action contract — one interface for all routers.

F18 fix: Different routers interpret roles/regex/CSS differently. This module
provides a unified locator/action contract that all routers must use.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class LocatorSpec:
    """Unified locator specification."""
    role: str
    name: str
    text: str = ""
    css: str = ""
    testid: str = ""

    def __post_init__(self) -> None:
        if not self.role and not self.name and not self.text and not self.css:
            raise ValueError("locator must have at least one selector")


@dataclass(frozen=True)
class ActionSpec:
    """Unified action specification."""
    action_type: str  # click, fill, check, select
    intent: str
    locator: LocatorSpec
    value: str = ""

    def __post_init__(self) -> None:
        if self.action_type not in {"click", "fill", "check", "select"}:
            raise ValueError(f"unsupported action type: {self.action_type}")
        if self.action_type in {"fill", "select"} and not self.value:
            raise ValueError(f"{self.action_type} action requires value")


@runtime_checkable
class UnifiedLocator(Protocol):
    """Protocol for unified locator resolution."""
    
    def resolve(self, spec: LocatorSpec, timeout_ms: int = 2000) -> Any | None:
        """Resolve a locator spec to a page element."""
        ...
    
    def resolve_all(self, spec: LocatorSpec, limit: int = 10) -> list[Any]:
        """Resolve a locator spec to all matching elements."""
        ...


@runtime_checkable
class UnifiedAction(Protocol):
    """Protocol for unified action execution."""
    
    def execute(self, action: ActionSpec, timeout_ms: int = 3000) -> bool:
        """Execute an action spec."""
        ...


class PlaywrightUnifiedLocator:
    """Unified locator implementation for Playwright pages."""
    
    def __init__(self, page: Any) -> None:
        self.page = page
    
    def resolve(self, spec: LocatorSpec, timeout_ms: int = 2000) -> Any | None:
        """Resolve a locator spec to a page element."""
        for loc in self._locators(spec):
            try:
                el = loc.first
                if el.count() and el.is_visible():
                    return el
            except Exception:
                continue
        return None
    
    def resolve_all(self, spec: LocatorSpec, limit: int = 10) -> list[Any]:
        """Resolve a locator spec to all matching elements."""
        results = []
        for loc in self._locators(spec):
            try:
                count = min(loc.count(), limit - len(results))
                for i in range(count):
                    el = loc.nth(i)
                    if el.is_visible():
                        results.append(el)
                        if len(results) >= limit:
                            return results
            except Exception:
                continue
        return results
    
    def _locators(self, spec: LocatorSpec):
        """Yield Playwright locators from spec, most stable first."""
        role, name = spec.role, spec.name
        text, css = spec.text, spec.css
        
        if role and name:
            yield self.page.get_by_role(role, name=re.compile(name, re.I))
        if name and not role:
            yield self.page.get_by_role("button", name=re.compile(name, re.I))
            yield self.page.get_by_label(re.compile(name, re.I))
        if text:
            yield self.page.get_by_text(re.compile(re.escape(text), re.I))
            yield self.page.locator(f"button:has-text('{text}')")
            yield self.page.locator(f"a:has-text('{text}')")
        if css:
            yield self.page.locator(css)


class PlaywrightUnifiedAction:
    """Unified action implementation for Playwright pages."""
    
    def __init__(self, page: Any) -> None:
        self.page = page
        self.locator = PlaywrightUnifiedLocator(page)
    
    def execute(self, action: ActionSpec, timeout_ms: int = 3000) -> bool:
        """Execute an action spec."""
        el = self.locator.resolve(action.locator, timeout_ms)
        if el is None:
            return False
        
        try:
            if action.action_type == "click":
                el.click(timeout=timeout_ms)
                return True
            elif action.action_type == "fill":
                el.fill(action.value, timeout=timeout_ms)
                return True
            elif action.action_type == "check":
                el.check(timeout=timeout_ms)
                return True
            elif action.action_type == "select":
                el.select_option(action.value, timeout=timeout_ms)
                return True
        except Exception:
            return False
        
        return False


def create_unified_locator(page: Any) -> UnifiedLocator:
    """Create a unified locator for a Playwright page."""
    return PlaywrightUnifiedLocator(page)


def create_unified_action(page: Any) -> UnifiedAction:
    """Create a unified action executor for a Playwright page."""
    return PlaywrightUnifiedAction(page)
