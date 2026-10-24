import sqlite3

from test_queue_store import spec

from m_dlp.queue_store import QueueStore


def test_snapshot_reads_live_status_without_loading_torrents_or_options(tmp_path):
    path = tmp_path / "queue.db"
    with QueueStore(path) as store, QueueStore(path) as writer:
        store.add([(spec(tmp_path), {"cache_dir": "private"}, b"large torrent")])
        writer.progress(1, name="sample", completed=50, total=100, rate=12, peers=3)

        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and column in {"torrent", "options", "source"}:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        store.db.set_authorizer(authorize)
        row = store.snapshot()[0]
        assert (row.id, row.name, row.completed, row.total, row.rate, row.peers) == (
            1,
            "sample",
            50,
            100,
            12,
            3,
        )
        assert row.destination == str(spec(tmp_path).destination)
        writer.control([1], "pause")
        assert store.snapshot()[0].state == "paused"
