"""
Shared ocpplib.log helper for MEA OCPP 1.6 compliance tests.
"""
import re
import requests

_ANSI = re.compile(r'\x1b\[[0-9;]*m')

VSECC_LOG_BASE = "http://192.168.1.166/api"


class VseccLog:
    """Downloads ocpplib.log from vSECC and extracts OCPP frames since last mark."""

    def __init__(self, token: str, base: str = VSECC_LOG_BASE):
        self._url = f"{base}/logging/files/ocpplib.log"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._pos = 0
        try:
            r = requests.get(self._url, headers=self._headers, timeout=60)
            self._pos = len(r.text)
        except Exception:
            pass

    def mark(self):
        """Record current end-of-log position (call before each action)."""
        try:
            r = requests.get(self._url, headers=self._headers, timeout=60)
            self._pos = len(r.text)
        except Exception:
            pass

    def ocpp_frames(self) -> list[str]:
        """Return OCPP wire frames (>>> sent / <<< received) since last mark."""
        try:
            r = requests.get(self._url, headers=self._headers, timeout=60)
            new_text = r.text[self._pos:]
            self._pos = len(r.text)
        except Exception:
            return []
        frames = []
        for line in new_text.splitlines():
            if ">>> " in line or "<<< " in line:
                clean = _ANSI.sub("", line).strip()
                for marker in (">>> ", "<<< "):
                    idx = clean.find(marker)
                    if idx >= 0:
                        direction = ">>>" if marker == ">>> " else "<<<"
                        frames.append(f"  {direction} {clean[idx + len(marker):]}")
                        break
        return frames

    def find(self, frames: list[str], *keywords) -> str | None:
        """Return first frame containing all keywords, or None."""
        for f in frames:
            if all(k in f for k in keywords):
                return f
        return None


def print_ocpp(frames: list[str], label: str = ""):
    if not frames:
        return
    if label:
        print(f"       [ocpplib] {label}")
    for f in frames:
        print(f"       {f}")
