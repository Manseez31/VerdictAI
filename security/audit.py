"""Immutable (tamper-evident) audit logging.

WHY THIS EXISTS
---------------
A legal platform must be able to answer, after the fact: what was asked, what
was retrieved, what the model said, which citations were verified, and whether
any security control fired. A plain log file is not enough — it can be edited.

DESIGN: hash chain
------------------
Each record stores the SHA-256 of (previous_hash + canonical_record). Altering
or deleting any past record breaks every subsequent hash, so tampering is
*detectable* (`verify()`), even though the file is still an ordinary JSONL.

This is tamper-EVIDENT, not tamper-PROOF. True immutability needs append-only
storage (WORM bucket / cloud audit sink). Documented, not overclaimed.

PII is redacted before anything is written.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from .pii import redact_pii

logger = logging.getLogger(__name__)

GENESIS = "0" * 64


def _canonical(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AuditLog:
    """Append-only, hash-chained JSONL audit log."""

    def __init__(self, path: str | Path = "data/audit/audit.jsonl", redact: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.redact = redact
        self._lock = threading.Lock()

    def _last_hash(self) -> str:
        if not self.path.exists():
            return GENESIS
        last = None
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if not last:
            return GENESIS
        try:
            return json.loads(last)["hash"]
        except Exception:
            logger.error("Audit log tail is corrupt; chaining from genesis")
            return GENESIS

    def record(self, event: str, **fields: Any) -> Dict[str, Any]:
        """Append one event. Never raises — an audit failure must not take down
        the request path, but it IS logged loudly."""
        try:
            payload = dict(fields)
            if self.redact:
                for k, v in list(payload.items()):
                    if isinstance(v, str):
                        payload[k], _ = redact_pii(v)

            with self._lock:
                prev = self._last_hash()
                body = {
                    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                    "event": event,
                    "data": payload,
                    "prev": prev,
                }
                body["hash"] = hashlib.sha256((prev + _canonical(body)).encode("utf-8")).hexdigest()
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(body, ensure_ascii=False) + "\n")
            return body
        except Exception:
            logger.exception("AUDIT WRITE FAILED for event=%s", event)
            return {}

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        if not self.path.exists():
            return iter(())
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    def verify(self) -> Dict[str, Any]:
        """Recompute the chain. Detects any edit/deletion/reordering."""
        prev = GENESIS
        count = 0
        for i, rec in enumerate(self):
            count += 1
            stored = rec.get("hash")
            body = {k: rec[k] for k in ("ts", "event", "data", "prev") if k in rec}
            expected = hashlib.sha256((prev + _canonical(body)).encode("utf-8")).hexdigest()
            if rec.get("prev") != prev or stored != expected:
                logger.error("AUDIT CHAIN BROKEN at record %d", i)
                return {"valid": False, "broken_at": i, "records": count}
            prev = stored
        return {"valid": True, "broken_at": None, "records": count}
