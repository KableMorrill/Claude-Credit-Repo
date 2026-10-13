r"""Scrub file paths, user names and machine names from what leaves the .kra (D1).

The log inside the .kra keeps what capture saw. Everything derived from it that can leave the
document, the manifest definition first of all, goes through ``scrub`` (threat model: prompt,
path or name leakage). Scrubbing is deliberately eager: an over-scrubbed word costs a little
clarity, a leaked home directory names the artist.

What it does:

- A string that is a path, in Windows, UNC, POSIX or ``~`` form, becomes its last component, so
  ``C:\Users\Kable Morrill\Pictures\harbour.png`` becomes ``harbour.png``.
- A path embedded in prose is cut down the same way, up to the next space.
- The user's home directory, login name and machine name are replaced wherever they appear,
  case-insensitively, with ``[user]`` and ``[machine]``.
- URLs and relative names such as ``sd15/dreamshaper_8.safetensors`` are left alone.

The identity comes from the machine at export time (``Identity.current()``) and is injected in
tests.
"""

from __future__ import annotations

import getpass
import os
import platform
import re
import socket
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

USER_PLACEHOLDER = "[user]"
MACHINE_PLACEHOLDER = "[machine]"
MIN_NAME = 3  # replacing every "al" or "pc" would mangle more than it protects


@dataclass(frozen=True)
class Identity:
    """What identifies this user and machine. Names shorter than MIN_NAME are not scrubbed."""

    user_names: tuple[str, ...] = ()
    home: str | None = None
    host_names: tuple[str, ...] = ()

    @classmethod
    def current(cls) -> Identity:
        users: list[str] = []
        try:
            users.append(getpass.getuser())
        except (OSError, KeyError, ImportError):  # no account database, odd embedded setups
            pass
        users += [os.environ[v] for v in ("USER", "USERNAME", "LOGNAME") if os.environ.get(v)]
        home = os.path.expanduser("~")
        if home == "~":
            home = None
        elif home_name := file_name(home):
            users.append(home_name)  # the home folder is usually the login name
        hosts: list[str] = []
        for host in (socket.gethostname(), platform.node()):
            if host:
                hosts += [host, host.split(".")[0]]  # "studio-pc.local" and "studio-pc"
        return cls(_unique(users), home, _unique(hosts))


def scrub(value: Any, identity: Identity) -> Any:
    """Scrub every string inside nested dicts, lists and tuples. Keys and non-strings pass."""
    if isinstance(value, str):
        return scrub_text(value, identity)
    if isinstance(value, Mapping):
        return {key: scrub(item, identity) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(scrub(item, identity) for item in value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [scrub(item, identity) for item in value]
    return value


def scrub_text(text: str, identity: Identity) -> str:
    if identity.home:
        text = _replace_name(text, identity.home, "~")
        text = _replace_name(text, identity.home.replace("\\", "/"), "~")
    if _WHOLE_PATH.fullmatch(text):
        text = file_name(text)
    else:
        for pattern in _EMBEDDED_PATHS:
            text = pattern.sub(_embedded_file_name, text)
    # Longest first, so "Kable Morrill" goes before "kable" leaves "[user] Morrill" behind.
    for name in sorted(identity.user_names, key=len, reverse=True):
        text = _replace_word(text, name, USER_PLACEHOLDER)
    for name in sorted(identity.host_names, key=len, reverse=True):
        text = _replace_word(text, name, MACHINE_PLACEHOLDER)
    return text


def file_name(path: str) -> str:
    """The last component of a path in any style; the path itself if it has no component."""
    parts = [
        part for part in _SEPARATORS.split(path.strip()) if part and not _DRIVE.fullmatch(part)
    ]
    return parts[-1] if parts else path


_SEPARATORS = re.compile(r"[\\/]+")
_DRIVE = re.compile(r"[A-Za-z]:")
_WHOLE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/|~[\\/])[^\n\r]*")
_PATH_CHARS = r"""[^\s"'<>|*?]"""
_EMBEDDED_PATHS = (
    re.compile(rf"(?<![\w$])[A-Za-z]:[\\/]{_PATH_CHARS}*"),  # C:\Users\kable\x.png
    re.compile(rf"(?<![\w\\])\\\\{_PATH_CHARS}+"),  # \\studio-pc\share\x.png
    re.compile(rf"(?<!\w)~[\\/]{_PATH_CHARS}*"),  # ~/art/x.png
    re.compile(rf"(?<![\w/:.~\\])/(?:{_PATH_CHARS[:-1]}/]+/)+{_PATH_CHARS}*"),  # /home/kable/x.png
)
_TRAILING_PUNCTUATION = re.compile(r"[.,;:!?)\]]+$")


def _embedded_file_name(match: re.Match[str]) -> str:
    path = match.group(0)
    trailing = _TRAILING_PUNCTUATION.search(path)
    if trailing:
        path = path[: trailing.start()]
    name = file_name(path)
    return name + (trailing.group(0) if trailing else "")


def _replace_word(text: str, name: str, placeholder: str) -> str:
    if len(name) < MIN_NAME:
        return text
    return re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", placeholder, text, flags=re.IGNORECASE)


def _replace_name(text: str, name: str, replacement: str) -> str:
    return re.sub(re.escape(name), replacement, text, flags=re.IGNORECASE) if name else text


def _unique(names: Sequence[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for name in names:
        if name.strip():
            seen.setdefault(name.strip(), None)
    return tuple(seen)
