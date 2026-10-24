import json

from test_cli import invoke
from test_queue_store import spec

from m_dlp.queue_store import QueueStore


def test_priority_move_pin_commands_and_json_persist_with_config(tmp_path):
    config = tmp_path / "settings.toml"
    config.write_text('queue-db = "queue.db"\n')
    path = tmp_path / "queue.db"
    with QueueStore(path) as store:
        store.add([(spec(tmp_path, n), {}, None) for n in (1, 2, 3)])

    def call(*args):
        return invoke("queue", *args, "--config", str(config))

    assert call("priority", "2", "3", "--level", "high").returncode == 0
    assert call("move", "3", "--direction", "up").returncode == 0
    assert call("pin", "1").returncode == 0
    records = json.loads(call("list", "--json").stdout)
    assert [r["id"] for r in records] == [1, 3, 2]
    assert records[0]["pinned"] is True and records[1]["priority"] == 2
    assert "position" in records[0]
    assert call("pin", "1", "--off").returncode == 0
    assert [r["id"] for r in json.loads(call("list", "--json").stdout)] == [3, 2, 1]
    before = call("list", "--json").stdout
    assert call("priority", "1", "999", "--level", "low").returncode == 2
    assert call("list", "--json").stdout == before
    assert call("priority", "1", "--level", "urgent").returncode == 2
