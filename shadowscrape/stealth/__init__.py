"""Stealth layer: consistent fingerprints, JS evasions, hardened launch, humanized input, Turnstile handling."""
from __future__ import annotations

from .profiles import Profile, build_profile, check_consistency, host_profile, resolve_profile
from .scripts import build_init_script, cdp_user_agent_override
from .turnstile import detect_challenge

__all__ = ["Profile", "build_profile", "host_profile", "resolve_profile", "check_consistency",
           "build_init_script", "cdp_user_agent_override", "detect_challenge"]
