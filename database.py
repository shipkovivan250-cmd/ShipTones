import os
import sqlite3
from contextlib import contextmanager

from config import BASE_DIR, logger

# ============================================================
# SQLITE: ИСТОРИЯ (фишка 5) + ПЛЕЙЛИСТЫ (фишки 15/17)
# ============================================================
class Database:
    def __init__(self):
        self.db_path = BASE_DIR / "shiptones.db"
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
        try:
            yield conn
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            logger.error(f"DB Error: {e}")
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE, title TEXT, artist TEXT, source TEXT,
                    file_path TEXT, file_hash TEXT,
                    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT, url TEXT UNIQUE, source TEXT,
                    target_path TEXT, last_synced TIMESTAMP
                )
            """)

    # ---------- ИСТОРИЯ ----------
    def add_track(self, title, artist, source, url, file_path):
        from utils import hash_file_fast
        file_hash = hash_file_fast(file_path) or ""
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO history
                (title, artist, source, url, file_path, file_hash)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (title, artist, source, url, file_path, file_hash))

    def get_history(self, search=""):
        with self._conn() as conn:
            if search:
                like = f"%{search}%"
                return conn.execute("""
                    SELECT title, artist, source, downloaded_at FROM history
                    WHERE title LIKE ? OR artist LIKE ?
                    ORDER BY downloaded_at DESC LIMIT 200
                """, (like, like)).fetchall()
            return conn.execute("""
                SELECT title, artist, source, downloaded_at FROM history
                ORDER BY downloaded_at DESC LIMIT 200
            """).fetchall()

    def is_track_downloaded(self, url):
        """ФИКС: дубликат только если файл РЕАЛЬНО лежит на диске (как в v1.0)"""
        if not url:
            return None
        with self._conn() as conn:
            row = conn.execute("SELECT file_path FROM history WHERE url = ?", (url,)).fetchone()

        if row and row[0] and os.path.exists(row[0]):
            return row[0]
        return None

    # ---------- ПЛЕЙЛИСТЫ ----------
    def save_playlist(self, name, url, source, target_path):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO playlists (name, url, source, target_path)
                VALUES (?, ?, ?, ?)
            """, (name, url, source, target_path))

    def get_playlists(self):
        with self._conn() as conn:
            return conn.execute("""
                SELECT id, name, url, source, target_path, last_synced
                FROM playlists ORDER BY name
            """).fetchall()

    def delete_playlist(self, playlist_id):
        with self._conn() as conn:
            conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))

    def touch_playlist(self, url):
        with self._conn() as conn:
            conn.execute("UPDATE playlists SET last_synced = CURRENT_TIMESTAMP WHERE url = ?", (url,))

DB = Database()