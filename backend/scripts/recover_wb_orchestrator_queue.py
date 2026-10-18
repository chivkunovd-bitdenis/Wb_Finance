"""Offline, selective Celery/Redis recovery. Dry-run unless --apply is supplied.

All WB Finance producers and consumers must remain stopped throughout recovery.
The private SQLite snapshot contains original bytes (including sensitive payloads).
No credentials, task bodies, task IDs or owner IDs are printed. Redis and DB URLs
are accepted only from REDIS_URL and DATABASE_URL environment variables.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import itertools
import json
import os
import sqlite3
import struct
import sys
import uuid
from pathlib import Path

TASK = "wb_orchestrator_tick"
SEPARATOR = b"\x06\x16"
BATCH = 250


class UnsafeRecovery(RuntimeError):
    """Safe, payload-free explanation of a rejected recovery."""


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def target_message(raw):
    """Return (owner, message without ETA), or None for a known other task.

    Unknown/malformed envelopes are never silently discarded, including ones
    where we cannot establish whether they are the target task.
    """
    try:
        message = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
        headers = message["headers"]
        task = headers["task"]
        if not isinstance(task, str) or not task:
            raise ValueError()
        if task != TASK:
            return None
        if not isinstance(headers.get("id"), str) or not headers["id"]:
            raise ValueError()
        if headers.get("group") or headers.get("group_index") is not None:
            raise ValueError()
        if message.get("content-type") != "application/json":
            raise ValueError()
        if message.get("content-encoding") not in ("utf-8", "utf8"):
            raise ValueError()
        if message["properties"].get("body_encoding") != "base64":
            raise ValueError()
        args, kwargs, canvas = json.loads(base64.b64decode(message["body"], validate=True))
        if not isinstance(args, list) or len(args) != 1 or not isinstance(args[0], str) or not args[0]:
            raise ValueError()
        if kwargs != {} or not isinstance(canvas, dict):
            raise ValueError()
        if set(canvas) - {"callbacks", "errbacks", "chain", "chord"}:
            raise ValueError()
        if any(canvas.values()):
            raise ValueError()
        # Decode-copy prevents mutation of an unacked envelope supplied by caller.
        message = json.loads(encode(message))
        message["headers"]["eta"] = None
        return args[0], encode(message)
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise UnsafeRecovery("Malformed or unsupported task envelope; nothing may be removed") from exc


def chunks(iterable):
    it = iter(iterable)
    while batch := list(itertools.islice(it, BATCH)):
        yield batch


def framed(*values):
    return b"".join(struct.pack(">Q", len(v)) + v for v in values)


def fingerprint(rows, ordered=False):
    count = 0
    if ordered:
        digest = hashlib.sha256()
        for row in rows:
            digest.update(framed(*row))
            count += 1
        return count, digest.hexdigest()
    # Commutative multiset digest permits bounded-memory HSCAN/ZSCAN iteration.
    total = 0
    for row in rows:
        total = (total + int.from_bytes(hashlib.sha256(framed(*row)).digest(), "big")) % (1 << 256)
        count += 1
    return count, f"{total:064x}"


def queue_rows(client, key):
    for start in range(0, client.llen(key), BATCH):
        yield from ((raw,) for raw in client.lrange(key, start, start + BATCH - 1))


def redis_fingerprint(client, key, kind):
    if kind == "list":
        return fingerprint(queue_rows(client, key), ordered=True)
    if kind == "hash":
        return fingerprint(client.hscan_iter(key, count=BATCH))
    return fingerprint((field, float(score).hex().encode()) for field, score in client.zscan_iter(key, count=BATCH))


class Recovery:
    def __init__(self, client, backup_dir, states, queues=(b"celery",), unacked=b"unacked", index=b"unacked_index"):
        self.client = client
        self.states = states  # Callable returning {owner: persisted intents}; never writes DB.
        self.queues = tuple(q + (SEPARATOR + str(p).encode() if p else b"") for q in queues for p in (0, 3, 6, 9))
        self.base_queues = tuple(queues)
        self.unacked = unacked
        self.index = index
        self.keys = [(q, "list") for q in self.queues] + [(unacked, "hash"), (index, "zset")]
        directory = Path(backup_dir)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.stat().st_mode & 0o077:
            raise UnsafeRecovery("Backup directory must be private (mode 0700)")
        self.path = directory / f"queue-{uuid.uuid4().hex}.sqlite3"
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(self.path)
        self.db.executescript("""
            PRAGMA journal_mode=DELETE;
            PRAGMA cache_size=-4096;
            CREATE TABLE ready(q BLOB, pos INTEGER, raw BLOB, owner TEXT, normalized BLOB, PRIMARY KEY(q,pos));
            CREATE TABLE unacked(field BLOB PRIMARY KEY, raw BLOB, owner TEXT, normalized BLOB, route BLOB);
            CREATE TABLE scores(field BLOB PRIMARY KEY, score REAL);
            CREATE TABLE canonical(owner TEXT PRIMARY KEY, kind TEXT, q BLOB, pos INTEGER, field BLOB, raw BLOB);
            CREATE TABLE metadata(name TEXT PRIMARY KEY, value TEXT);
        """)
        self.original = {}
        self.intents_digest = None

    def state_digest(self):
        state = self.states()
        return state, hashlib.sha256(encode(sorted(state.items()))).hexdigest()

    def capture(self):
        state, self.intents_digest = self.state_digest()
        for queue in self.base_queues:
            if any(key not in self.queues for key in self.client.scan_iter(match=queue + SEPARATOR + b"*")):
                raise UnsafeRecovery("Unexpected priority queue; extend inventory before recovery")
        for key, kind in self.keys:
            if self.client.type(key) not in (b"none", kind.encode()):
                raise UnsafeRecovery("Broker key has an unexpected Redis type")
            if self.client.pttl(key) not in (-1, -2):
                raise UnsafeRecovery("Expiring broker keys are not supported; nothing changed")
        for queue in self.queues:
            for pos, (raw,) in enumerate(queue_rows(self.client, queue)):
                target = target_message(raw)
                owner, normalized = target if target else (None, None)
                self.db.execute("INSERT INTO ready VALUES (?,?,?,?,?)", (queue, pos, raw, owner, normalized))
                if target:
                    self._canonical(state, owner, "ready", queue, pos, None, normalized)
            self.db.commit()
        for field, raw in self.client.hscan_iter(self.unacked, count=BATCH):
            try:
                message, exchange, routing_key = json.loads(raw)
                target = target_message(message)
                route = None
                if target:
                    if exchange not in ("", "celery") or not isinstance(routing_key, str):
                        raise ValueError()
                    priority = message["properties"].get("priority", 0)
                    if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 9:
                        raise ValueError()
                    priority = max(p for p in (0, 3, 6, 9) if p <= priority)
                    route = routing_key.encode() + (SEPARATOR + str(priority).encode() if priority else b"")
                    if route not in self.queues:
                        raise ValueError()
            except (TypeError, ValueError, KeyError) as exc:
                raise UnsafeRecovery("Unsupported unacked envelope or routing; nothing changed") from exc
            owner, normalized = target if target else (None, None)
            self.db.execute("INSERT OR REPLACE INTO unacked VALUES (?,?,?,?,?)", (field, raw, owner, normalized, route))
            if target:
                self._canonical(state, owner, "unacked", route, None, field, normalized)
        for field, score in self.client.zscan_iter(self.index, count=BATCH):
            self.db.execute("INSERT OR REPLACE INTO scores VALUES (?,?)", (field, score))
        if self.db.execute("SELECT COUNT(*) FROM unacked u LEFT JOIN scores s USING(field) WHERE u.owner IS NOT NULL AND s.field IS NULL").fetchone()[0]:
            raise UnsafeRecovery("Target unacked task has no index entry; nothing changed")
        self.db.commit()
        for key, kind in self.keys:
            expected = self.snapshot_fingerprint(key, kind)
            if redis_fingerprint(self.client, key, kind) != expected:
                raise UnsafeRecovery("Broker changed during snapshot; stop all producers and consumers")
            self.original[key] = expected
        if self.state_digest()[1] != self.intents_digest:
            raise UnsafeRecovery("Persisted intents changed during snapshot")
        self.db.execute("INSERT INTO metadata VALUES (?,?)", ("intents_sha256", self.intents_digest))
        self.db.commit()
        self._write_report("snapshot", self.report())
        return self.report()

    def _canonical(self, states, owner, kind, queue, pos, field, normalized):
        if owner not in states:
            raise UnsafeRecovery("Target owner lacks durable orchestrator state; nothing changed")
        self.db.execute("INSERT OR IGNORE INTO canonical VALUES (?,?,?,?,?,?)", (owner, kind, queue, pos, field, normalized))

    def snapshot_fingerprint(self, key, kind, retained=False):
        if kind == "list":
            if retained:
                return fingerprint(((v,) for v in self.retained_queue(key)), ordered=True)
            return fingerprint(self.db.execute("SELECT raw FROM ready WHERE q=? ORDER BY pos", (key,)), ordered=True)
        if kind == "hash":
            sql = "SELECT field,raw FROM unacked" + (" WHERE owner IS NULL" if retained else "")
            return fingerprint(self.db.execute(sql))
        sql = "SELECT field,score FROM scores"
        if retained:
            sql += " WHERE field NOT IN (SELECT field FROM unacked WHERE owner IS NOT NULL)"
        return fingerprint((f, float(s).hex().encode()) for f, s in self.db.execute(sql))

    def retained_queue(self, queue):
        # LPUSH/RPOP queues: restored messages go behind existing ready messages.
        yield from (r[0] for r in self.db.execute("SELECT raw FROM canonical WHERE kind='unacked' AND q=? ORDER BY owner", (queue,)))
        yield from (r[0] for r in self.db.execute("""
            SELECT CASE WHEN r.owner IS NULL THEN r.raw ELSE r.normalized END
            FROM ready r LEFT JOIN canonical c ON r.owner=c.owner
            WHERE r.q=? AND (r.owner IS NULL OR (c.kind='ready' AND c.q=r.q AND c.pos=r.pos)) ORDER BY r.pos
        """, (queue,)))

    def report(self):
        ready = self.db.execute("SELECT COUNT(*),COUNT(owner) FROM ready").fetchone()
        unacked = self.db.execute("SELECT COUNT(*),COUNT(owner) FROM unacked").fetchone()
        kept = self.db.execute("SELECT COUNT(*) FROM canonical").fetchone()[0]
        return {"ready_before": ready[0], "unacked_before": unacked[0], "target_before": ready[1] + unacked[1],
                "target_retained": kept, "duplicates_removed": ready[1] + unacked[1] - kept,
                "other_ready_preserved": ready[0] - ready[1], "other_unacked_preserved": unacked[0] - unacked[1],
                "intents_sha256": self.intents_digest}

    def _write_report(self, suffix, report):
        checksum = hashlib.sha256()
        with self.path.open("rb") as stream:
            for part in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(part)
        report = dict(report, snapshot_sha256=checksum.hexdigest())
        dest = self.path.with_suffix(f".{suffix}.json")
        with os.fdopen(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            json.dump(report, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())

    def apply(self):
        if not self.original:
            raise UnsafeRecovery("A verified snapshot is required before applying")
        temporary = {}
        expected = {}
        try:
            with self.client.pipeline() as transaction:
                transaction.watch(*(key for key, _ in self.keys))
                for key, kind in self.keys:
                    if redis_fingerprint(transaction, key, kind) != self.original[key]:
                        raise UnsafeRecovery("Broker changed after snapshot; nothing changed")
                    if transaction.pttl(key) not in (-1, -2):
                        raise UnsafeRecovery("Broker key expiry changed after snapshot")
                    expected[key] = self.snapshot_fingerprint(key, kind, retained=True)
                    if expected[key] == self.original[key]:
                        continue
                    temp = b"wb-recovery-private:" + uuid.uuid4().hex.encode()
                    temporary[key] = temp
                    if kind == "list":
                        for batch in chunks(self.retained_queue(key)):
                            self.client.rpush(temp, *batch)
                    elif kind == "hash":
                        for batch in chunks(self.db.execute("SELECT field,raw FROM unacked WHERE owner IS NULL")):
                            self.client.hset(temp, mapping=dict(batch))
                    else:
                        for batch in chunks(self.db.execute("SELECT field,score FROM scores WHERE field NOT IN (SELECT field FROM unacked WHERE owner IS NOT NULL)")):
                            self.client.zadd(temp, dict(batch))
                    if redis_fingerprint(self.client, temp, kind) != expected[key]:
                        raise UnsafeRecovery("Prepared replacement does not match preservation plan")
                if self.state_digest()[1] != self.intents_digest:
                    raise UnsafeRecovery("Persisted intents changed; nothing changed")
                transaction.multi()
                for key, temp in temporary.items():
                    if expected[key][0]:
                        transaction.rename(temp, key)
                    else:
                        # Only broker containers whose retained contents are empty.
                        transaction.delete(key)
                transaction.execute()
            for key, kind in self.keys:
                if redis_fingerprint(self.client, key, kind) != expected[key]:
                    raise UnsafeRecovery("Post-apply verification failed; keep services stopped and use private snapshot")
            after_digest = self.state_digest()[1]
            if after_digest != self.intents_digest:
                raise UnsafeRecovery("Post-apply intents verification failed; keep services stopped")
            report = dict(self.report(), applied=True, preserved_other_bytes_and_order=True,
                          intents_sha256_after=after_digest)
            self._write_report("applied", report)
            return report
        finally:
            # These keys are private UUID-named staging containers, never app keys.
            for temp in temporary.values():
                self.client.delete(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--producers-stopped", action="store_true", help="Attest that all API/beat/workers/external producers are stopped")
    parser.add_argument("--queue", action="append", help="Base queue; default celery (all standard priorities included)")
    args = parser.parse_args()
    if args.apply and not args.producers_stopped:
        parser.error("--apply requires --producers-stopped")
    if not os.getenv("REDIS_URL") or not os.getenv("DATABASE_URL"):
        parser.error("REDIS_URL and DATABASE_URL must be set privately in the environment")
    try:
        import redis
        from sqlalchemy import create_engine, text
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.database_url import normalize_database_url
        engine = create_engine(normalize_database_url(os.environ["DATABASE_URL"]))

        def states():
            with engine.connect() as connection:
                return {str(owner): intents for owner, intents in connection.execute(text("SELECT user_id, intents FROM wb_orchestrator_state"))}

        client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=False)
        recovery = Recovery(client, args.backup_dir, states, queues=tuple(q.encode() for q in (args.queue or ["celery"])))
        report = recovery.capture()
        if args.apply:
            report = recovery.apply()
        print(json.dumps(dict(report, dry_run=not args.apply), sort_keys=True))
    except UnsafeRecovery as exc:
        print(json.dumps({"error": str(exc), "recovery_halted": True}), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- redact driver errors, which may contain credentials.
        # Driver exceptions can embed passwords/URLs or raw payloads.
        print(json.dumps({"error_type": type(exc).__name__, "recovery_halted": True}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
