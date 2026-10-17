from __future__ import annotations

import re
import shlex


def validate_profile_name(name: str) -> str:
    """Keep names portable and prevent paths or keyring account aliases."""
    if not re.fullmatch(r"[a-z](?:[a-z0-9-]{0,30}[a-z0-9])?", name):
        raise ValueError(
            "Profile names must be 1–32 lowercase letters, digits, or hyphens, "
            "start with a letter, and end with a letter or digit."
        )
    return name


def normalize_profile(name: str | None) -> str | None:
    if name is None:
        return None
    return None if validate_profile_name(name) == "default" else name


def connect_command(profile: str | None = None) -> str:
    """Build a copyable reconnect command for the selected connection."""
    profile = normalize_profile(profile)
    arguments = ["noteropdf", "connect"]
    if profile is not None:
        arguments.extend(("--profile", profile))
    return shlex.join(arguments)


def recovery_guidance(message: str, profile: str | None = None) -> str:
    """Scope the standard reconnect instruction at the presentation boundary."""
    command = connect_command(profile)
    return message.replace("`noteropdf connect`", f"`{command}`")
