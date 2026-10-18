"""Real Redis tests for selective recovery; each test owns an isolated process."""
import base64
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import redis

MODULE = Path(__file__).resolve().parents[1] / "backend/scripts/recover_wb_orchestrator_queue.py"
spec = importlib.util.spec_from_file_location("recover_queue", MODULE)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


@pytest.fixture
def broker(tmp_path):
    executable = shutil.which("redis-server")
    if not executable:
        pytest.skip("A local redis-server executable is required")
    # A fresh Unix socket avoids every existing Redis database and queue.
    socket_dir = tempfile.TemporaryDirectory(prefix="wbq-", dir="/tmp")
    socket = Path(socket_dir.name) / "r.sock"
    proc = subprocess.Popen([executable, "--port", "0", "--unixsocket", str(socket),
                             "--save", "", "--appendonly", "no"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = redis.Redis(unix_socket_path=str(socket))
    try:
        for _ in range(100):
            try:
                if client.ping():
                    break
            except redis.ConnectionError:
                time.sleep(0.02)
        else:
            raise RuntimeError("Isolated test Redis failed to start")
        yield client
    finally:
        client.close()
        proc.terminate()
        proc.wait(timeout=5)
        socket_dir.cleanup()


def message(owner="owner-a", name="wb_orchestrator_tick", task_id="task", priority=0, canvas=None):
    return json.dumps({"headers": {"task": name, "id": task_id, "eta": "2099-01-01T00:00:00Z"},
                       "body": base64.b64encode(json.dumps([[owner], {}, canvas or {"callbacks": None, "errbacks": None, "chain": None, "chord": None}]).encode()).decode(),
                       "content-type": "application/json", "content-encoding": "utf-8",
                       "properties": {"body_encoding": "base64", "priority": priority}}, indent=2).encode()


def reserve(client, field, raw, score, routing_key="celery"):
    encoded = json.dumps([json.loads(raw), "celery", routing_key], indent=3).encode()
    client.hset("unacked", field, encoded)
    client.zadd("unacked_index", {field: score})
    return encoded


def tool(broker, tmp_path, states=None):
    values = states if states is not None else {"owner-a": {"high": [{"kind": "finance"}]}, "owner-b": {"low": [{"kind": "funnel"}]}}
    return recovery.Recovery(broker, tmp_path / "private", lambda: values)


def test_mixed_ready_priority_and_unacked_preserve_every_other_byte_and_score(broker, tmp_path):
    other_one = message(name="wb_orchestrator_kick")
    other_two = message(name="ozon_sync", task_id="ozon")
    broker.rpush("celery", other_one, message(task_id="a1"), other_two, message(task_id="a2"))
    priority_queue = b"celery" + recovery.SEPARATOR + b"6"
    broker.rpush(priority_queue, message(task_id="a3", priority=6), other_two)
    reserved_other = reserve(broker, b"non-target", other_one, 100.25)
    reserve(broker, b"target-a", message(task_id="a4"), 101.5)
    reserve(broker, b"target-b", message("owner-b", task_id="b1", priority=7), 102.75)
    broker.zadd("unacked_index", {b"orphan-index-preserve": 3.125})
    broker.set("celery-task-meta-other", b"result bytes")
    broker.set("unrelated", b"private untouched")
    run = tool(broker, tmp_path)
    before = run.capture()
    assert before["target_before"] == 5 and before["target_retained"] == 2
    # Dry-run itself does not move or delete any message.
    assert broker.llen("celery") == 4 and broker.hlen("unacked") == 3
    report = run.apply()
    assert report["duplicates_removed"] == 3
    ready = broker.lrange("celery", 0, -1)
    assert ready[0] == other_one and ready[2] == other_two
    assert json.loads(ready[1])["headers"]["id"] == "a1"
    assert json.loads(ready[1])["headers"]["eta"] is None
    priority = broker.lrange(priority_queue, 0, -1)
    assert priority[1] == other_two
    assert json.loads(priority[0])["headers"]["id"] == "b1"
    assert json.loads(priority[0])["headers"]["eta"] is None
    assert broker.hgetall("unacked") == {b"non-target": reserved_other}
    assert dict(broker.zrange("unacked_index", 0, -1, withscores=True)) == {b"non-target": 100.25, b"orphan-index-preserve": 3.125}
    assert broker.get("celery-task-meta-other") == b"result bytes"
    assert broker.get("unrelated") == b"private untouched"
    assert not list(broker.scan_iter("wb-recovery-private:*"))
    assert report["intents_sha256"] == report["intents_sha256_after"]
    # Private snapshot retains original raw payloads, not merely counts.
    assert run.db.execute("SELECT raw FROM unacked WHERE field=?", (b"non-target",)).fetchone()[0] == reserved_other
    assert os.stat(run.path).st_mode & 0o077 == 0
    assert run.path.with_suffix(".applied.json").exists()


def test_target_only_unacked_moves_one_and_removes_empty_broker_containers(broker, tmp_path):
    reserve(broker, b"one", message(), 10)
    reserve(broker, b"two", message(task_id="duplicate"), 11)
    run = tool(broker, tmp_path)
    run.capture()
    run.apply()
    assert broker.llen("celery") == 1
    assert not broker.exists("unacked", "unacked_index")
    assert json.loads(broker.lindex("celery", 0))["headers"]["eta"] is None


@pytest.mark.parametrize("bad", [
    b"not json",
    b'{"headers": {}}',
    message(canvas={"chain": [{"task": "other"}]}),
    message(canvas={"chord": {"task": "other"}}),
    message(canvas={"callbacks": [{"task": "other"}]}),
])
def test_malformed_or_canvas_messages_abort_without_mutation(broker, tmp_path, bad):
    broker.rpush("celery", message(), bad, message(task_id="duplicate"))
    original = broker.lrange("celery", 0, -1)
    with pytest.raises(recovery.UnsafeRecovery):
        tool(broker, tmp_path).capture()
    assert broker.lrange("celery", 0, -1) == original


def test_unknown_owner_aborts_without_mutation(broker, tmp_path):
    raw = message("owner-missing")
    broker.rpush("celery", raw)
    with pytest.raises(recovery.UnsafeRecovery, match="durable"):
        tool(broker, tmp_path).capture()
    assert broker.lrange("celery", 0, -1) == [raw]


def test_changed_queue_after_snapshot_aborts_and_preserves_arrival(broker, tmp_path):
    broker.rpush("celery", message(), message(task_id="duplicate"))
    run = tool(broker, tmp_path)
    run.capture()
    broker.lpush("celery", message(name="other-arrival"))
    before = broker.lrange("celery", 0, -1)
    with pytest.raises(recovery.UnsafeRecovery, match="changed after snapshot"):
        run.apply()
    assert broker.lrange("celery", 0, -1) == before
    assert not list(broker.scan_iter("wb-recovery-private:*"))


def test_watch_rejects_change_while_replacements_are_prepared(broker, tmp_path, monkeypatch):
    broker.rpush("celery", message(), message(task_id="duplicate"))
    run = tool(broker, tmp_path)
    run.capture()
    original_rpush = broker.rpush
    def interleave(key, *values):
        result = original_rpush(key, *values)
        if key.startswith(b"wb-recovery-private:"):
            original_rpush("celery", message(name="other-arrival"))
        return result
    monkeypatch.setattr(broker, "rpush", interleave)
    with pytest.raises(redis.WatchError):
        run.apply()
    assert broker.llen("celery") == 3
    assert not list(broker.scan_iter("wb-recovery-private:*"))


def test_intents_changed_after_snapshot_abort_before_swap(broker, tmp_path):
    state = {"owner-a": {"high": ["old"]}}
    broker.rpush("celery", message(), message(task_id="duplicate"))
    original = broker.lrange("celery", 0, -1)
    run = tool(broker, tmp_path, state)
    run.capture()
    state["owner-a"]["high"].append("new")
    with pytest.raises(recovery.UnsafeRecovery, match="intents changed"):
        run.apply()
    assert broker.lrange("celery", 0, -1) == original
    assert not list(broker.scan_iter("wb-recovery-private:*"))


def test_no_targets_means_no_broker_mutations(broker, tmp_path):
    raw = message(name="other")
    broker.rpush("celery", raw)
    reserve(broker, b"other", raw, 12.34)
    before = {key: broker.dump(key) for key in broker.scan_iter()}
    run = tool(broker, tmp_path)
    assert run.capture()["target_before"] == 0
    assert run.apply()["duplicates_removed"] == 0
    assert {key: broker.dump(key) for key in broker.scan_iter()} == before


def test_missing_unacked_index_or_unknown_route_are_not_deleted(broker, tmp_path):
    reserve(broker, b"target", message(), 11)
    broker.zrem("unacked_index", b"target")
    before = broker.hgetall("unacked")
    with pytest.raises(recovery.UnsafeRecovery, match="index entry"):
        tool(broker, tmp_path).capture()
    assert broker.hgetall("unacked") == before


def test_expiring_broker_keys_abort_preserving_ttl(broker, tmp_path):
    broker.rpush("celery", message())
    broker.expire("celery", 60)
    with pytest.raises(recovery.UnsafeRecovery, match="Expiring"):
        tool(broker, tmp_path).capture()
    assert 0 < broker.ttl("celery") <= 60


def test_cli_does_not_apply_without_stopped_producers_attestation(tmp_path):
    result = subprocess.run([os.sys.executable, str(MODULE), "--apply", "--backup-dir", str(tmp_path)], text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert "requires --producers-stopped" in result.stderr
