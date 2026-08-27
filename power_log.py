"""Discover the Power.log belonging to the newest Hearthstone session."""

from datetime import datetime
from pathlib import Path
from typing import Optional, Union


SESSION_PREFIX = "Hearthstone_"
SESSION_TIMESTAMP_FORMAT = "%Y_%m_%d_%H_%M_%S"


def _session_timestamp(path: Path) -> Optional[datetime]:
    if not path.is_dir() or not path.name.startswith(SESSION_PREFIX):
        return None

    timestamp_text = path.name[len(SESSION_PREFIX):]
    try:
        return datetime.strptime(timestamp_text, SESSION_TIMESTAMP_FORMAT)
    except ValueError:
        return None


def find_latest_session_dir(log_root: Union[str, Path]) -> Optional[Path]:
    """Return the newest valid timestamped session directory."""
    root = Path(log_root)
    if not root.is_dir():
        return None

    sessions = []
    for path in root.iterdir():
        timestamp = _session_timestamp(path)
        if timestamp is not None:
            sessions.append((timestamp, path))

    if not sessions:
        return None

    return max(sessions, key=lambda item: item[0])[1]


def find_latest_power_log(log_root: Union[str, Path]) -> Optional[Path]:
    """Return Power.log in the newest session, without falling back."""
    session_dir = find_latest_session_dir(log_root)
    if session_dir is None:
        return None

    power_log = session_dir / "Power.log"
    if not power_log.is_file():
        return None

    return power_log
