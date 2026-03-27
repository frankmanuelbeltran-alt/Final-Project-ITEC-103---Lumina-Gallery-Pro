import sqlite3
import os
import shutil
import threading
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

try:
    import imagehash
    HAS_IMAGEHASH = True
except ImportError:
    HAS_IMAGEHASH = False

from lumina.config import Config
from lumina.utils.logging_utils import logger


class DatabaseManager:
    SCHEMA_VERSION = 8

    def __init__(self, db_path=None):
        self.db_path = db_path or Config.DB_PATH
        self._local = threading.local()
        self._lock = threading.RLock()
        self.init_database()
        self.migrate_if_needed()
        self._init_wal_mode()

    def _get_connection(self):
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-64000")
            conn.execute("PRAGMA temp_store=MEMORY")
            self._local.conn = conn
        return self._local.conn

    def _init_wal_mode(self):
        try:
            with self.get_connection() as conn:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('PRAGMA synchronous=NORMAL')
                conn.execute('PRAGMA cache_size=-64000')
                conn.execute('PRAGMA temp_store=MEMORY')
        except sqlite3.Error as e:
            logger.warning(f"Could not enable WAL mode: {e}")

    @contextmanager
    def get_connection(self):
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def init_database(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT UNIQUE NOT NULL,
                    media_type TEXT CHECK(media_type IN ('image', 'video')),
                    size INTEGER,
                    mtime REAL,
                    sha256 TEXT,
                    width INTEGER,
                    height INTEGER,
                    duration INTEGER,
                    view_count INTEGER DEFAULT 0,
                    last_viewed TIMESTAMP,
                    favorite INTEGER DEFAULT 0,
                    rating INTEGER DEFAULT 0,
                    soft_delete INTEGER DEFAULT 0,
                    deleted_at TIMESTAMP,
                    original_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    phash TEXT
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS media_tags (
                    media_id INTEGER,
                    tag_id INTEGER,
                    PRIMARY KEY (media_id, tag_id),
                    FOREIGN KEY (media_id) REFERENCES media(id) ON DELETE CASCADE,
                    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS albums (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS album_media (
                    album_id INTEGER,
                    media_id INTEGER,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (album_id, media_id),
                    FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE,
                    FOREIGN KEY (media_id) REFERENCES media(id) ON DELETE CASCADE
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS duplicates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hash_value TEXT NOT NULL,
                    media_id INTEGER NOT NULL,
                    FOREIGN KEY (media_id) REFERENCES media(id) ON DELETE CASCADE
                )
            ''')
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS thumbnail_cache (
                    hash TEXT PRIMARY KEY,
                    path TEXT,
                    width INTEGER,
                    height INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    access_count INTEGER DEFAULT 0,
                    last_accessed TIMESTAMP
                )
            """)
            
            self._create_indexes(cursor)

    def _create_indexes(self, cursor):
        indexes = [
            ('idx_media_path', 'media(path)'),
            ('idx_media_sha256', 'media(sha256)'),
            ('idx_media_favorite', 'media(favorite)'),
            ('idx_media_mtime', 'media(mtime)'),
            ('idx_media_view_count', 'media(view_count)'),
            ('idx_media_size', 'media(size)'),
            ('idx_media_type', 'media(media_type)'),
            ('idx_media_rating', 'media(rating)'),
            ('idx_media_soft_delete', 'media(soft_delete)'),
            ('idx_media_deleted_at', 'media(deleted_at)'),
            ('idx_tags_name', 'tags(name)'),
            ('idx_media_tags_media_id', 'media_tags(media_id)'),
            ('idx_media_tags_tag_id', 'media_tags(tag_id)'),
            ('idx_album_media_album_id', 'album_media(album_id)'),
            ('idx_album_media_media_id', 'album_media(media_id)'),
            ('idx_duplicates_hash', 'duplicates(hash_value)'),
            ('idx_thumbnail_cache_hash', 'thumbnail_cache(hash)'),
            ('idx_thumbnail_cache_accessed', 'thumbnail_cache(last_accessed)'),
            ('idx_media_phash', 'media(phash)'),
        ]

        for name, columns in indexes:
            try:
                cursor.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {columns}')
            except sqlite3.Error as e:
                logger.warning(f"Could not create index {name}: {e}")

    def migrate_if_needed(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT version FROM schema_version')
            row = cursor.fetchone()
            current = row['version'] if row else 0

            migrations = [
                (1, self._migrate_v1),
                (2, self._migrate_v2),
                (3, self._migrate_v3),
                (4, self._migrate_v4),
                (5, self._migrate_v5),
                (6, self._migrate_v6),
                (7, self._migrate_v7),
                (8, self._migrate_v8),
            ]

            for version, migrate_func in migrations:
                if current < version:
                    try:
                        migrate_func(cursor)
                        cursor.execute('INSERT OR REPLACE INTO schema_version (version) VALUES (?)', (version,))
                        conn.commit()
                        logger.info(f"Migrated to schema version {version}")
                    except Exception as e:
                        logger.error(f"Migration to v{version} failed: {e}")

    def _migrate_v1(self, cursor):
        try:
            cursor.execute('ALTER TABLE media ADD COLUMN media_type TEXT DEFAULT "image"')
            cursor.execute('ALTER TABLE media ADD COLUMN duration INTEGER')
        except sqlite3.OperationalError:
            pass

    def _migrate_v2(self, cursor):
        try:
            cursor.execute('ALTER TABLE images RENAME TO media')
        except sqlite3.OperationalError:
            pass

    def _migrate_v3(self, cursor):
        self._create_indexes(cursor)

    def _migrate_v4(self, cursor):
        try:
            cursor.execute('ALTER TABLE media DROP COLUMN phash')
        except sqlite3.OperationalError:
            pass

    def _migrate_v5(self, cursor):
        try:
            cursor.execute('ALTER TABLE media ADD COLUMN soft_delete INTEGER DEFAULT 0')
            cursor.execute('ALTER TABLE media ADD COLUMN deleted_at TIMESTAMP')
            cursor.execute('ALTER TABLE media ADD COLUMN original_path TEXT')
            cursor.execute('ALTER TABLE media ADD COLUMN rating INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass

    def _migrate_v6(self, cursor):
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS duplicates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash_value TEXT NOT NULL,
                media_id INTEGER NOT NULL,
                FOREIGN KEY (media_id) REFERENCES media(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_duplicates_hash ON duplicates(hash_value)')
        
    def _migrate_v7(self, cursor):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS thumbnail_cache (
                hash TEXT PRIMARY KEY,
                path TEXT,
                width INTEGER,
                height INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 0,
                last_accessed TIMESTAMP
            )
        """)
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_thumbnail_cache_hash ON thumbnail_cache(hash)')
        try:
            cursor.execute('ALTER TABLE media ADD COLUMN phash TEXT')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_phash ON media(phash)')
        except sqlite3.OperationalError:
            pass

    def _migrate_v8(self, cursor):
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS albums (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS album_media (
                album_id INTEGER,
                media_id INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (album_id, media_id),
                FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE,
                FOREIGN KEY (media_id) REFERENCES media(id) ON DELETE CASCADE
            )
        ''')

    def get_or_create_media(self, path, media_type, size, mtime, sha256=None, 
                           width=None, height=None, duration=None, phash=None):
        path = os.path.abspath(path)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, size, mtime, sha256 FROM media WHERE path = ?', (path,))
            row = cursor.fetchone()

            if row:
                existing_id = row['id']
                if size == row['size'] and mtime == row['mtime']:
                    if width and height:
                        cursor.execute('UPDATE media SET width = ?, height = ? WHERE id = ?',
                                     (width, height, existing_id))
                    return existing_id, False

                cursor.execute('''
                    UPDATE media 
                    SET media_type = ?, size = ?, mtime = ?, sha256 = ?,
                        width = ?, height = ?, duration = ?
                    WHERE id = ?
                ''', (media_type, size, mtime, sha256, width, height, duration, existing_id))
                return existing_id, True
            else:
                cursor.execute('''
                    INSERT INTO media (path, media_type, size, mtime, sha256, 
                                     width, height, duration, phash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (path, media_type, size, mtime, sha256, width, height, duration, phash))
                return cursor.lastrowid, True

    def update_view_stats(self, media_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE media
                SET view_count = view_count + 1,
                    last_viewed = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (media_id,))

    def toggle_favorite(self, media_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT favorite FROM media WHERE id = ?', (media_id,))
            row = cursor.fetchone()
            if not row:
                return None
            current = row['favorite']
            new_state = 0 if current else 1
            cursor.execute('UPDATE media SET favorite = ? WHERE id = ?', (new_state, media_id))
            return new_state  

    def set_favorite_batch(self, media_ids, favorite=True):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            state = 1 if favorite else 0
            placeholders = ','.join('?' * len(media_ids))
            cursor.execute(f'UPDATE media SET favorite = ? WHERE id IN ({placeholders})', 
                         (state, *media_ids))
            return cursor.rowcount

    def set_rating(self, media_id, rating):
        rating = max(0, min(5, rating))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE media SET rating = ? WHERE id = ?', (rating, media_id))

    def soft_delete_media(self, media_id, trash_dir):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT path FROM media WHERE id = ?', (media_id,))
            row = cursor.fetchone()
            if not row:
                return False, "Media not found"

            original_path = row['path']
            trash_path = os.path.join(trash_dir, os.path.basename(original_path))

            counter = 1
            base, ext = os.path.splitext(trash_path)
            while os.path.exists(trash_path):
                trash_path = f"{base}_{counter}{ext}"
                counter += 1

            try:
                os.makedirs(trash_dir, exist_ok=True)
                shutil.move(original_path, trash_path)

                cursor.execute('''
                    UPDATE media 
                    SET soft_delete = 1, 
                        deleted_at = CURRENT_TIMESTAMP,
                        original_path = ?,
                        path = ?
                    WHERE id = ?
                ''', (original_path, trash_path, media_id))

                return True, trash_path
            except (IOError, OSError) as e:
                logger.error(f"Soft delete error: {e}")
                return False, str(e)

    def soft_delete_batch(self, media_ids, trash_dir):
        results = []
        for media_id in media_ids:
            success, result = self.soft_delete_media(media_id, trash_dir)
            results.append((media_id, success, result))
        return results

    def restore_media(self, media_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT path, original_path FROM media WHERE id = ?', (media_id,))
            row = cursor.fetchone()
            if not row:
                return False, "Media not found"

            trash_path = row['path']
            original_path = row['original_path']

            if os.path.exists(original_path):
                return False, "Original location already has a file with that name"

            os.makedirs(os.path.dirname(original_path), exist_ok=True)

            try:
                shutil.move(trash_path, original_path)

                cursor.execute('''
                    UPDATE media 
                    SET soft_delete = 0, 
                        deleted_at = NULL,
                        path = ?
                    WHERE id = ?
                ''', (original_path, media_id))

                return True, original_path
            except (IOError, OSError) as e:
                logger.error(f"Restore error: {e}")
                return False, str(e)

    def permanently_delete(self, media_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT path FROM media WHERE id = ?', (media_id,))
            row = cursor.fetchone()
            if not row:
                return False, "Media not found"

            path = row['path']

            try:
                if os.path.exists(path):
                    os.remove(path)
                cursor.execute('DELETE FROM media WHERE id = ?', (media_id,))
                return True, "Deleted"
            except (IOError, OSError) as e:
                logger.error(f"Permanent delete error: {e}")
                return False, str(e)

    def permanently_delete_batch(self, media_ids):
        results = []
        for media_id in media_ids:
            success, result = self.permanently_delete(media_id)
            results.append((media_id, success, result))
        return results

    def cleanup_old_trash(self, days=30):
        cutoff = datetime.now() - timedelta(days=days)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, path FROM media 
                WHERE soft_delete = 1 AND deleted_at < ?
            ''', (cutoff,))

            to_delete = cursor.fetchall()
            deleted_count = 0

            for row in to_delete:
                success, _ = self.permanently_delete(row['id'])
                if success:
                    deleted_count += 1

            return deleted_count

    def get_deleted_media(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM media 
                WHERE soft_delete = 1 
                ORDER BY deleted_at DESC
            ''')
            return [dict(row) for row in cursor.fetchall()]

    def get_duplicates(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT sha256, COUNT(*) as count 
                FROM media 
                WHERE sha256 IS NOT NULL AND soft_delete = 0
                GROUP BY sha256 
                HAVING count > 1
            ''')
            return [dict(row) for row in cursor.fetchall()]

    def get_all_media(self, include_deleted=False):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if include_deleted:
                cursor.execute('SELECT * FROM media ORDER BY mtime DESC')
            else:
                cursor.execute('SELECT * FROM media WHERE soft_delete = 0 ORDER BY mtime DESC')
            return [dict(row) for row in cursor.fetchall()]

    def get_stats(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            stats = {}

            cursor.execute('SELECT COUNT(*) as total FROM media WHERE soft_delete = 0')
            stats['total'] = cursor.fetchone()['total']

            cursor.execute('SELECT COUNT(*) as videos FROM media WHERE media_type = "video" AND soft_delete = 0')
            stats['videos'] = cursor.fetchone()['videos']

            cursor.execute('SELECT COUNT(*) as favorites FROM media WHERE favorite = 1 AND soft_delete = 0')
            stats['favorites'] = cursor.fetchone()['favorites']

            cursor.execute('SELECT COUNT(*) as deleted FROM media WHERE soft_delete = 1')
            stats['deleted'] = cursor.fetchone()['deleted']

            cursor.execute('SELECT SUM(size) as total_size FROM media WHERE soft_delete = 0')
            result = cursor.fetchone()
            stats['total_size'] = result['total_size'] or 0

            return stats
    
    def get_similar_by_phash(self, phash, threshold=10):
        if not phash or not HAS_IMAGEHASH:
            return []
    
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, path, phash FROM media 
                WHERE phash IS NOT NULL AND soft_delete = 0
            """)
        
            similar = []
            try:
                target_hash = imagehash.hex_to_hash(phash)
                for row in cursor.fetchall():
                    if row['phash']:
                        try:
                            other_hash = imagehash.hex_to_hash(row['phash'])
                            distance = target_hash - other_hash
                            if 0 < distance <= threshold:
                                similar.append((dict(row), distance))
                        except (ValueError, TypeError):
                            continue
            except (ValueError, TypeError):
                pass
            
            similar.sort(key=lambda x: x[1])
            return similar

    def get_cached_thumbnail(self, content_hash):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT path FROM thumbnail_cache 
                WHERE hash = ?
            """, (content_hash,))
            row = cursor.fetchone()
            
            if row and os.path.exists(row['path']):
                cursor.execute("""
                    UPDATE thumbnail_cache 
                    SET access_count = access_count + 1,
                        last_accessed = CURRENT_TIMESTAMP
                    WHERE hash = ?
                """, (content_hash,))
                return row['path']
            return None
    
    def save_thumbnail_cache(self, content_hash, path, width, height):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO thumbnail_cache 
                (hash, path, width, height, last_accessed)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (content_hash, path, width, height))

    def get_tags_for_media(self, media_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.name FROM tags t
                JOIN media_tags mt ON t.id = mt.tag_id
                WHERE mt.media_id = ?
            """, (media_id,))
            return [dict(row) for row in cursor.fetchall()]

    def add_tag_to_media(self, media_id, tag_name):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO tags (name) VALUES (?)', (tag_name,))
            cursor.execute('SELECT id FROM tags WHERE name = ?', (tag_name,))
            tag_id = cursor.fetchone()['id']
            cursor.execute('INSERT OR IGNORE INTO media_tags (media_id, tag_id) VALUES (?, ?)',
                         (media_id, tag_id))
            return tag_id

    def remove_tag_from_media(self, media_id, tag_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM media_tags WHERE media_id = ? AND tag_id = ?',
                         (media_id, tag_id))

    def get_all_tags(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM tags ORDER BY name')
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Database error in get_all_tags: {e}")
            return []

    def get_media_by_tag(self, tag_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT m.* FROM media m
                JOIN media_tags mt ON m.id = mt.media_id
                WHERE mt.tag_id = ? AND m.soft_delete = 0
            """, (tag_id,))
            return [dict(row) for row in cursor.fetchall()]

    def create_album(self, name, description=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO albums (name, description) VALUES (?, ?)',
                         (name, description))
            return cursor.lastrowid

    def get_all_albums(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM albums ORDER BY created_at DESC')
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Database error in get_all_albums: {e}")
            return []

    def add_media_to_album(self, album_id, media_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO album_media (album_id, media_id) VALUES (?, ?)',
                         (album_id, media_id))

    def remove_media_from_album(self, album_id, media_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM album_media WHERE album_id = ? AND media_id = ?',
                         (album_id, media_id))

    def get_media_in_album(self, album_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT m.* FROM media m
                JOIN album_media am ON m.id = am.media_id
                WHERE am.album_id = ? AND m.soft_delete = 0
            """, (album_id,))
            return [dict(row) for row in cursor.fetchall()]

    def delete_album(self, album_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM albums WHERE id = ?', (album_id,))

    def set_preference(self, key, value):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)',
                         (key, value))

    def get_preference(self, key, default=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM preferences WHERE key = ?', (key,))
            row = cursor.fetchone()
            return row['value'] if row else default