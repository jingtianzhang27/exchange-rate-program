"""Snapshot backup helpers; these tests run without the CloudRun object storage."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.core import connect, init_db
from backend.storage import BackupManager, snapshot_bytes


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'rates.sqlite3'
        init_db(self.db)

    def _quotes(self):
        with connect(self.db) as db:
            return db.execute('SELECT COUNT(*) FROM quotes').fetchone()[0]

    def test_snapshot_bytes_is_a_readable_copy(self):
        with connect(self.db) as db:
            db.execute("INSERT INTO quotes VALUES('2026-01-01 00:00:00',7000000,'2026-01-01T00:00:00+08:00')")
        copy = Path(self.temp.name) / 'copy.sqlite3'
        copy.write_bytes(snapshot_bytes(self.db))
        with sqlite3.connect(copy) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM quotes').fetchone()[0], 1)

    def test_backup_disabled_without_bucket(self):
        with mock.patch.dict(os.environ, {'COS_BUCKET':'','BACKUP_ENABLED':'1'}):
            manager = BackupManager(self.db)
            self.assertFalse(manager.enabled)
            self.assertFalse(manager.backup_once())
            self.assertFalse(manager.restore_if_missing(attempts=1, delay=0))
            self.assertFalse(manager.status()['enabled'])

    def test_empty_database_is_never_uploaded(self):
        with mock.patch.dict(os.environ, {'COS_BUCKET':'bucket-for-test','BACKUP_ENABLED':'1'}):
            manager = BackupManager(self.db)
        manager._sdk = (object, object)  # pretend the optional SDK is installed
        self.assertTrue(manager.enabled)
        self.assertEqual(self._quotes(), 0)
        self.assertFalse(manager.backup_once())

    def test_unrestored_database_is_never_uploaded(self):
        with connect(self.db) as db:
            db.execute("INSERT INTO quotes VALUES('2026-01-01 00:00:00',7000000,'2026-01-01T00:00:00+08:00')")
        with mock.patch.dict(os.environ, {'COS_BUCKET':'bucket-for-test','BACKUP_ENABLED':'1'}):
            manager = BackupManager(self.db)
        manager._sdk = (object, object)
        manager.restore_failed = True
        self.assertEqual(self._quotes(), 1)
        self.assertFalse(manager.backup_once())


if __name__ == '__main__':
    unittest.main()
