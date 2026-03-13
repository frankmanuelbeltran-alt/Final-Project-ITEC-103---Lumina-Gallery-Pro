import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from PIL import Image, ImageTk, ImageOps, ExifTags
import os
from pathlib import Path
import time
import platform
import sqlite3
import hashlib
import json
import threading
import queue
from datetime import datetime, timedelta
from contextlib import contextmanager
import cv2
import math
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
import gc
import weakref
import shutil
import zipfile
import logging
from typing import Optional, List, Dict, Set, Callable, Any, Tuple
import enum


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('lumina_gallery_pro_max.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('LuminaGalleryProMax')


try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    logger.info("psutil not available - memory monitoring disabled")


try:
    import vlc
    HAS_VLC = True
except ImportError:
    HAS_VLC = False
    logger.info("vlc not available - video playback disabled")


class Config:
    THUMB_SIZE = int(os.getenv('LUMINA_THUMB_SIZE', '140'))
    THUMB_PADDING = int(os.getenv('LUMINA_THUMB_PADDING', '12'))
    THUMB_QUALITY = Image.Resampling.NEAREST
    MAX_RAM_CACHE = int(os.getenv('LUMINA_RAM_CACHE', '100'))
    MAX_VISIBLE_THUMBS = 50
    ZOOM_CACHE_SIZE = 10
    SCROLL_DEBOUNCE_MS = int(os.getenv('LUMINA_SCROLL_DEBOUNCE', '100'))
    RESIZE_DEBOUNCE_MS = int(os.getenv('LUMINA_RESIZE_DEBOUNCE', '200'))
    PREVIEW_DELAY_MS = 400
    SLIDESHOW_INTERVAL_MS = 5000
    THUMB_WORKERS = int(os.getenv('LUMINA_THUMB_WORKERS', '6'))
    MAX_CONCURRENT_LOADS = int(os.getenv('LUMINA_MAX_CONCURRENT', '6'))
    SCAN_BATCH_SIZE = 50
    TRASH_RETENTION_DAYS = int(os.getenv('LUMINA_TRASH_DAYS', '30'))
    DB_PATH = os.getenv('LUMINA_DB_PATH', 'gallery.db')
    CACHE_DIR = os.getenv('LUMINA_CACHE_DIR', '.cache/thumbnails')
    
    COLORS = {
        'bg': '#fff0f6',
        'surface': '#ffd6e7',
        'surface_hover': '#ffc2db',
        'surface_selected': '#ffb3d1',
        'accent': '#ff69b4',
        'accent_hover': '#ff4fa3',
        'text': '#4a2a3a',
        'text_secondary': '#8a5a6f',
        'border': '#ffb6d5',
        'danger': '#ff4d6d',
        'danger_hover': '#ff3355',
        'success': '#ff8fab',
        'favorite': '#ff85c1',
        'video': '#ff99cc',
        'duplicate': '#ff66b2',
        'selected': '#ff1493'
    }


class ViewMode(enum.Enum):
    GRID = "grid"
    SINGLE = "single"
    TRASH = "trash"
    SLIDESHOW = "slideshow"


class SortMode(enum.Enum):
    DATE = "date"
    NAME = "name"
    SIZE = "size"
    VIEWS = "views"
    RATING = "rating"
    RANDOM = "random"


from dataclasses import dataclass, field


@dataclass
class MediaItem:
    id: int
    path: str
    media_type: str
    size: int
    mtime: float
    sha256: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[int] = None
    view_count: int = 0
    last_viewed: Optional[datetime] = None
    favorite: bool = False
    rating: int = 0
    created_at: Optional[datetime] = None
    soft_delete: bool = False
    deleted_at: Optional[datetime] = None
    original_path: Optional[str] = None
    selected: bool = field(default=False, compare=False)
    exif_data: Dict[str, Any] = field(default_factory=dict, compare=False)
    
    @property
    def filename(self) -> str:
        return os.path.basename(self.path)
    
    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)
    
    @property
    def is_image(self) -> bool:
        return self.media_type == 'image'
    
    @property
    def is_video(self) -> bool:
        return self.media_type == 'video'
    
    def format_size(self) -> str:
        size = self.size
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"
    
    def format_duration(self) -> str:
        if self.duration is None:
            return "0:00"
        mins, secs = divmod(self.duration, 60)
        hours, mins = divmod(mins, 60)
        if hours > 0:
            return f"{hours}:{mins:02d}:{secs:02d}"
        return f"{mins}:{secs:02d}"


class ThreadSafeDict:
    def __init__(self):
        self._dict = {}
        self._lock = threading.RLock()
    
    def get(self, key, default=None):
        with self._lock:
            return self._dict.get(key, default)
    
    def __getitem__(self, key):
        with self._lock:
            return self._dict[key]
    
    def __setitem__(self, key, value):
        with self._lock:
            self._dict[key] = value
    
    def __delitem__(self, key):
        with self._lock:
            del self._dict[key]
    
    def pop(self, key, default=None):
        with self._lock:
            return self._dict.pop(key, default)
    
    def keys(self):
        with self._lock:
            return list(self._dict.keys())
    
    def values(self):
        with self._lock:
            return list(self._dict.values())
    
    def items(self):
        with self._lock:
            return list(self._dict.items())
    
    def __contains__(self, key):
        with self._lock:
            return key in self._dict
    
    def __len__(self):
        with self._lock:
            return len(self._dict)
    
    def clear(self):
        with self._lock:
            self._dict.clear()


class ThreadSafeList:
    def __init__(self):
        self._list = []
        self._lock = threading.RLock()
    
    def append(self, item):
        with self._lock:
            self._list.append(item)
    
    def extend(self, items):
        with self._lock:
            self._list.extend(items)
    
    def pop(self, index=-1):
        with self._lock:
            return self._list.pop(index)
    
    def __getitem__(self, index):
        with self._lock:
            return self._list[index]
    
    def __setitem__(self, index, value):
        with self._lock:
            self._list[index] = value
    
    def __len__(self):
        with self._lock:
            return len(self._list)
    
    def __iter__(self):
        with self._lock:
            return iter(self._list.copy())
    
    def copy(self):
        with self._lock:
            return self._list.copy()
    
    def index(self, item):
        with self._lock:
            return self._list.index(item)
    
    def clear(self):
        with self._lock:
            self._list.clear()


class TkQueue:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self._running = True
        self._check_queue()
        
    def _check_queue(self):
        if not self._running:
            return
        try:
            while True:
                func = self.queue.get_nowait()
                try:
                    self.root.after_idle(func)
                except Exception as e:
                    logger.error(f"Error executing queued function: {e}")
        except queue.Empty:
            pass
        self.root.after(50, self._check_queue)
        
    def put(self, func):
        self.queue.put(func)
    
    def shutdown(self):
        self._running = False


class ThumbnailLoader:
    def __init__(self, max_workers=6, max_concurrent=6):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.max_concurrent = max_concurrent
        self.pending_futures = {}
        self.load_queue = queue.PriorityQueue()
        self.active_count = 0
        self.lock = threading.RLock()
        self._shutdown = False
        self._start_processor()
        
    def _start_processor(self):
        self.processor_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.processor_thread.start()
        
    def _process_queue(self):
        while not self._shutdown:
            try:
                priority, task_id, func, callback = self.load_queue.get(timeout=0.1)
                
                with self.lock:
                    if task_id not in self.pending_futures:
                        continue
                
                while self.active_count >= self.max_concurrent and not self._shutdown:
                    time.sleep(0.01)
                    
                with self.lock:
                    if self._shutdown:
                        break
                    self.active_count += 1
                    
                future = self.executor.submit(func)
                
                with self.lock:
                    if task_id in self.pending_futures:
                        self.pending_futures[task_id] = future
                    else:
                        self.active_count -= 1
                        continue
                    
                def on_complete(fut, cb=callback, tid=task_id):
                    with self.lock:
                        self.active_count -= 1
                        should_callback = tid in self.pending_futures
                        if tid in self.pending_futures:
                            del self.pending_futures[tid]
                    
                    if not should_callback or self._shutdown:
                        return
                        
                    try:
                        result = fut.result()
                        if cb:
                            cb(result)
                    except Exception as e:
                        logger.error(f"Thumbnail load error: {e}")
                        
                future.add_done_callback(on_complete)
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Queue processor error: {e}")
                
    def submit(self, task_id: str, priority: int, func, callback=None):
        with self.lock:
            if self._shutdown:
                return
            if task_id in self.pending_futures:
                old_future = self.pending_futures[task_id]
                if old_future and not old_future.done():
                    old_future.cancel()
                del self.pending_futures[task_id]
            self.pending_futures[task_id] = None
                
        self.load_queue.put((priority, task_id, func, callback))
        
    def cancel(self, task_id: str):
        with self.lock:
            if task_id in self.pending_futures:
                future = self.pending_futures[task_id]
                if future and not future.done():
                    future.cancel()
                del self.pending_futures[task_id]
                
    def cancel_all(self):
        with self.lock:
            for task_id, future in list(self.pending_futures.items()):
                if future and not future.done():
                    future.cancel()
            self.pending_futures.clear()
            
            while not self.load_queue.empty():
                try:
                    self.load_queue.get_nowait()
                except queue.Empty:
                    break
            
    def shutdown(self, wait=True):
        self._shutdown = True
        self.cancel_all()
        self.executor.shutdown(wait=wait)


class BackgroundWorker:
    def __init__(self, tk_queue):
        self.tk_queue = tk_queue
        self.task_queue = queue.Queue()
        self.active_tasks = {}
        self.lock = threading.RLock()
        self._shutdown = False
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        
    def _worker_loop(self):
        while not self._shutdown:
            try:
                task = self.task_queue.get(timeout=0.1)
                if task is None:
                    break
                    
                task_id, func, callback = task
                
                with self.lock:
                    if task_id not in self.active_tasks:
                        continue
                
                try:
                    result = func()
                    with self.lock:
                        should_callback = task_id in self.active_tasks and callback and not self._shutdown
                    if should_callback:
                        self.tk_queue.put(lambda: callback(result))
                except Exception as e:
                    logger.error(f"Worker task {task_id} error: {e}")
                    
                with self.lock:
                    if task_id in self.active_tasks:
                        del self.active_tasks[task_id]
                        
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                
    def submit(self, task_id, func, callback=None):
        with self.lock:
            if self._shutdown:
                return
            self.active_tasks[task_id] = True
        self.task_queue.put((task_id, func, callback))
        
    def cancel(self, task_id):
        with self.lock:
            if task_id in self.active_tasks:
                del self.active_tasks[task_id]
    
    def shutdown(self):
        self._shutdown = True
        with self.lock:
            self.active_tasks.clear()
        self.task_queue.put(None)


class ThumbnailCache:
    def __init__(self, cache_dir=None):
        self.cache_dir = Path(cache_dir or Config.CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ram_cache = OrderedDict()
        self.lock = threading.RLock()
        self.access_count = 0
        self.hit_count = 0
        
    def _get_cache_path(self, content_hash):
        return self.cache_dir / f"{content_hash}.jpg"
        
    def get(self, content_hash):
        self.access_count += 1
        
        with self.lock:
            if content_hash in self.ram_cache:
                self.hit_count += 1
                self.ram_cache.move_to_end(content_hash)
                return self.ram_cache[content_hash].copy()
            
        cache_path = self._get_cache_path(content_hash)
        if cache_path.exists():
            try:
                img = Image.open(cache_path)
                img_copy = img.copy()
                img.close()
                self._add_to_ram(content_hash, img_copy)
                self.hit_count += 1
                return img_copy
            except Exception as e:
                logger.warning(f"Failed to load cached thumbnail: {e}")
                return None
        return None
        
    def put(self, content_hash, pil_image):
        cache_path = self._get_cache_path(content_hash)
        try:
            pil_image.save(cache_path, "JPEG", quality=85, optimize=True)
        except Exception as e:
            logger.warning(f"Cache save error: {e}")
        
        self._add_to_ram(content_hash, pil_image.copy())
        
    def _add_to_ram(self, content_hash, pil_image):
        with self.lock:
            if content_hash in self.ram_cache:
                self.ram_cache.move_to_end(content_hash)
                return
                
            while len(self.ram_cache) >= Config.MAX_RAM_CACHE:
                self.ram_cache.popitem(last=False)
                
            self.ram_cache[content_hash] = pil_image
        
    def compute_content_hash(self, file_path, file_stat):
        hasher = hashlib.sha256()
        hasher.update(file_path.encode())
        hasher.update(str(file_stat.st_mtime).encode())
        hasher.update(str(file_stat.st_size).encode())
        return hasher.hexdigest()[:32]
    
    def get_stats(self):
        hit_rate = (self.hit_count / self.access_count * 100) if self.access_count > 0 else 0
        return {
            'ram_items': len(self.ram_cache),
            'disk_items': len(list(self.cache_dir.glob('*.jpg'))),
            'access_count': self.access_count,
            'hit_count': self.hit_count,
            'hit_rate': hit_rate
        }
    
    def clear_ram(self):
        with self.lock:
            self.ram_cache.clear()
        gc.collect()


class DatabaseManager:
    SCHEMA_VERSION = 6
    
    def __init__(self, db_path=None):
        self.db_path = db_path or Config.DB_PATH
        self._local = threading.local()
        self._lock = threading.RLock()
        self.init_database()
        self.migrate_if_needed()
        self._init_wal_mode()
        
    def _get_connection(self):
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn
        
    def _init_wal_mode(self):
        try:
            with self.get_connection() as conn:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('PRAGMA synchronous=NORMAL')
                conn.execute('PRAGMA cache_size=-64000')
                conn.execute('PRAGMA temp_store=MEMORY')
        except Exception as e:
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        ]
        
        for name, columns in indexes:
            try:
                cursor.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {columns}')
            except Exception as e:
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
        except:
            pass
            
    def _migrate_v2(self, cursor):
        try:
            cursor.execute('ALTER TABLE images RENAME TO media')
        except:
            pass
            
    def _migrate_v3(self, cursor):
        self._create_indexes(cursor)
        
    def _migrate_v4(self, cursor):
        try:
            cursor.execute('ALTER TABLE media DROP COLUMN phash')
        except:
            pass
            
    def _migrate_v5(self, cursor):
        try:
            cursor.execute('ALTER TABLE media ADD COLUMN soft_delete INTEGER DEFAULT 0')
            cursor.execute('ALTER TABLE media ADD COLUMN deleted_at TIMESTAMP')
            cursor.execute('ALTER TABLE media ADD COLUMN original_path TEXT')
            cursor.execute('ALTER TABLE media ADD COLUMN rating INTEGER DEFAULT 0')
        except:
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
            
    def get_or_create_media(self, path, media_type, size, mtime, sha256=None, 
                           width=None, height=None, duration=None):
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
                                     width, height, duration)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (path, media_type, size, mtime, sha256, width, height, duration))
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
            except Exception as e:
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
            except Exception as e:
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
            except Exception as e:
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


class ToastManager:
    def __init__(self, root, colors):
        self.root = root
        self.colors = colors
        self.active_toasts = []
        self.toast_queue = queue.Queue()
        self._process_queue()
        
    def _process_queue(self):
        try:
            while True:
                message, duration, emoji = self.toast_queue.get_nowait()
                self._show_toast(message, duration, emoji)
        except queue.Empty:
            pass
        self.root.after(100, self._process_queue)
        
    def show(self, message, duration=2000, emoji="✨"):
        self.toast_queue.put((message, duration, emoji))
        
    def _show_toast(self, message, duration=2000, emoji="✨"):
        try:
            toast = tk.Toplevel(self.root)
            toast.overrideredirect(True)
            toast.attributes('-topmost', True)
            toast.attributes('-alpha', 0)
            
            frame = tk.Frame(toast, bg=self.colors['surface'], 
                            highlightbackground=self.colors['accent'],
                            highlightthickness=2, padx=15, pady=10)
            frame.pack()
            
            lbl = tk.Label(frame, text=f"{emoji} {message}", 
                          font=("Nunito", 11),
                          bg=self.colors['surface'], 
                          fg=self.colors['text'])
            lbl.pack()
            
            self.root.update_idletasks()
            x = self.root.winfo_x() + self.root.winfo_width()//2 - 100
            y = self.root.winfo_y() + self.root.winfo_height() - 80
            toast.geometry(f"+{x}+{y}")
            
            for alpha in range(0, 91, 15):
                toast.attributes('-alpha', alpha / 100)
                toast.update()
                time.sleep(0.02)
            
            def dismiss():
                try:
                    for alpha in range(90, -1, -15):
                        toast.attributes('-alpha', alpha / 100)
                        toast.update()
                        time.sleep(0.02)
                    toast.destroy()
                except:
                    pass
                    
            self.root.after(duration, dismiss)
            
        except Exception as e:
            logger.error(f"Toast error: {e}")


class ExifReader:
    @staticmethod
    def read_exif(image_path):
        try:
            with Image.open(image_path) as img:
                exif = img._getexif()
                if not exif:
                    return {}
                
                data = {}
                for tag_id, value in exif.items():
                    tag = ExifTags.TAGS.get(tag_id, tag_id)
                    data[tag] = value
                
                return ExifReader._format_exif(data)
        except Exception as e:
            logger.debug(f"Could not read EXIF from {image_path}: {e}")
            return {}
    
    @staticmethod
    def _format_exif(data):
        formatted = {}
        
        if 'Make' in data:
            formatted['Camera Make'] = str(data['Make'])
        if 'Model' in data:
            formatted['Camera Model'] = str(data['Model'])
        
        if 'DateTimeOriginal' in data:
            formatted['Date Taken'] = str(data['DateTimeOriginal'])
        
        if 'ExposureTime' in data:
            exp = data['ExposureTime']
            if isinstance(exp, tuple):
                formatted['Exposure'] = f"{exp[0]}/{exp[1]}s"
            else:
                formatted['Exposure'] = f"{exp}s"
        
        if 'FNumber' in data:
            fnum = data['FNumber']
            if isinstance(fnum, tuple):
                formatted['Aperture'] = f"f/{fnum[0]/fnum[1]:.1f}"
            else:
                formatted['Aperture'] = f"f/{fnum}"
        
        if 'ISOSpeedRatings' in data:
            formatted['ISO'] = str(data['ISOSpeedRatings'])
        
        if 'FocalLength' in data:
            focal = data['FocalLength']
            if isinstance(focal, tuple):
                formatted['Focal Length'] = f"{focal[0]/focal[1]:.0f}mm"
            else:
                formatted['Focal Length'] = f"{focal}mm"
        
        if 'GPSInfo' in data:
            gps = ExifReader._get_gps_coords(data['GPSInfo'])
            if gps:
                formatted['GPS'] = gps
        
        return formatted
    
    @staticmethod
    def _get_gps_coords(gps_info):
        try:
            from PIL import ExifTags
            
            gps_data = {}
            for key in gps_info.keys():
                decode = ExifTags.GPSTAGS.get(key, key)
                gps_data[decode] = gps_info[key]
            
            if 'GPSLatitude' in gps_data and 'GPSLongitude' in gps_data:
                lat = ExifReader._convert_dms(gps_data['GPSLatitude'])
                if gps_data.get('GPSLatitudeRef') == 'S':
                    lat = -lat
                
                lon = ExifReader._convert_dms(gps_data['GPSLongitude'])
                if gps_data.get('GPSLongitudeRef') == 'W':
                    lon = -lon
                
                return f"{lat:.6f}, {lon:.6f}"
        except:
            pass
        return None
    
    @staticmethod
    def _convert_dms(dms):
        degrees = dms[0][0] / dms[0][1]
        minutes = dms[1][0] / dms[1][1]
        seconds = dms[2][0] / dms[2][1]
        return degrees + minutes / 60 + seconds / 3600


class LuminaGalleryProMax:
    def __init__(self, root):
        self.root = root
        self.root.title("Lumina Gallery Pro Max 💗")
        self.root.geometry("1600x1000")
        self.root.minsize(1200, 800)
        
        self.is_windows = platform.system() == "Windows"
        self.is_linux = platform.system() == "Linux"
        self.is_mac = platform.system() == "Darwin"
        
        self.root.report_callback_exception = self._handle_error
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        
        self.db = DatabaseManager()
        self.thumb_cache = ThumbnailCache()
        self.tk_queue = TkQueue(root)
        self.worker = BackgroundWorker(self.tk_queue)
        self.thumb_loader = ThumbnailLoader(
            max_workers=Config.THUMB_WORKERS,
            max_concurrent=Config.MAX_CONCURRENT_LOADS
        )
        self.toast = ToastManager(root, Config.COLORS)
        self.exif_reader = ExifReader()
        
        self.trash_dir = Path(".lumina_trash")
        self.trash_dir.mkdir(exist_ok=True)
        
        try:
            deleted = self.db.cleanup_old_trash(Config.TRASH_RETENTION_DAYS)
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old trash items")
        except Exception as e:
            logger.error(f"Trash cleanup error: {e}")
        
        self.all_media = []
        self.media = []
        self.media_by_id = ThreadSafeDict()
        self.media_by_path = ThreadSafeDict()
        
        self.selected_items = set()
        self.last_selected_idx = None
        
        self.current_index = 0
        self.view_mode = ViewMode.GRID
        self.sort_mode = SortMode.DATE
        self.filter_query = ""
        self.showing_favorites = False
        self.showing_videos_only = False
        self.showing_deleted = False
        self.slideshow_active = False
        
        self.slideshow_after_id = None
        self._resize_after = None
        self._scroll_update_after = None
        self.video_timeline_after_id = None
        self.preview_after_id = None
        
        self.vlc_instance = None
        self.vlc_player = None
        self.vlc_attached = False
        
        if HAS_VLC:
            try:
                self.vlc_instance = vlc.Instance('--quiet', '--avcodec-hw=any')
                self.vlc_player = self.vlc_instance.media_player_new()
            except Exception as e:
                logger.error(f"VLC initialization error: {e}")
        
        self.original_image = None
        self.current_image_path = None
        self.zoom_level = 1.0
        self.pan_x = 0
        self.pan_y = 0
        self.is_panning = False
        self.pan_start_x = 0
        self.pan_start_y = 0
        self.zoom_cache = OrderedDict()
        self.canvas_image_id = None
        
        self.colors = Config.COLORS
        self.font_main = ("Nunito", 11)
        self.font_bold = ("Nunito", 12, "bold")
        self.font_title = ("Nunito", 20, "bold")
        self.font_emoji = ("Segoe UI Emoji", 22)
        self.font_small = ("Nunito", 9)
        
        self.visible_thumbs = {}
        self.thumb_size = Config.THUMB_SIZE
        self.thumb_padding = Config.THUMB_PADDING
        self.columns = 4
        self._render_lock = threading.RLock()
        self.canvas_window = None
        
        self._refreshing = False
        
        self.preview_window = None
        
        self.create_widgets()
        self.bind_events()
        
        self.root.after(100, self.load_initial_media)
        
        logger.info("LuminaGalleryProMax initialized")
        
    def _handle_error(self, exc, val, tb):
        import traceback
        logger.error("Unhandled exception:", exc_info=(exc, val, tb))
        try:
            messagebox.showerror("Error", f"An error occurred:\n{val}")
        except:
            pass
    
    def _on_close(self):
        logger.info("Shutting down Lumina Gallery Pro Max...")
        
        self.stop_slideshow()
        
        if HAS_VLC and self.vlc_player:
            try:
                self.vlc_player.stop()
            except:
                pass
        
        self.thumb_loader.cancel_all()
        self.thumb_loader.shutdown(wait=False)
        self.worker.shutdown()
        self.tk_queue.shutdown()
        
        self.thumb_cache.clear_ram()
        
        if self.original_image:
            try:
                self.original_image.close()
            except:
                pass
        
        logger.info("Shutdown complete")
        self.root.destroy()
        
    def create_widgets(self):
        self.gradient_canvas = tk.Canvas(self.root, highlightthickness=0)
        self.gradient_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._draw_gradient()
        
        self.main_container = tk.Frame(self.root, bg=self.colors['bg'])
        self.main_container.pack(fill=tk.BOTH, expand=True, padx=25, pady=20)
        
        self.setup_drag_drop()
        self.create_header()
        self.create_sidebar()
        self.create_content()
        self.create_status_bar()
        
    def _draw_gradient(self):
        try:
            width = self.root.winfo_screenwidth()
            height = self.root.winfo_screenheight()
            
            for i in range(height):
                ratio = i / height
                r = int(255 - (255 - 255) * ratio)
                g = int(240 - (240 - 182) * ratio)
                b = int(246 - (246 - 193) * ratio)
                color = f'#{r:02x}{g:02x}{b:02x}'
                self.gradient_canvas.create_line(0, i, width, i, fill=color, width=1)
        except Exception as e:
            logger.error(f"Gradient draw error: {e}")
            
    def setup_drag_drop(self):
        try:
            self.root.drop_target_register(tk.DND_FILES)
            self.root.dnd_bind('<<Drop>>', self.on_drop)
        except Exception as e:
            logger.info(f"Drag and drop not available: {e}")
            
    def on_drop(self, event):
        try:
            paths = event.data.split()
            for path in paths:
                path = path.strip('{}')
                if os.path.isdir(path):
                    self.scan_directory_background(path)
                elif os.path.isfile(path):
                    self.add_single_file(path)
        except Exception as e:
            logger.error(f"Drop handling error: {e}")
            self.toast.show("Error processing dropped files", emoji="⚠️")
                
    def create_header(self):
        self.header = tk.Frame(self.main_container, height=80, bg=self.colors['surface'])
        self.header.pack(fill=tk.X, pady=(0, 20))
        self.header.pack_propagate(False)
        
        title_frame = tk.Frame(self.header, bg=self.colors['surface'])
        title_frame.pack(side=tk.LEFT, padx=25, pady=20)
        
        tk.Label(title_frame, text="💗", font=self.font_emoji, 
                bg=self.colors['surface'], fg=self.colors['accent']).pack(side=tk.LEFT)
        
        tk.Label(title_frame, text="Lumina Pro Max", font=self.font_title,
                bg=self.colors['surface'], fg=self.colors['text']).pack(side=tk.LEFT, padx=(8, 0))
        
        self.stats_label = tk.Label(title_frame, text="", font=self.font_small,
                                   bg=self.colors['surface'], fg=self.colors['text_secondary'])
        self.stats_label.pack(side=tk.LEFT, padx=(20, 0))
        
        controls = tk.Frame(self.header, bg=self.colors['surface'])
        controls.pack(side=tk.RIGHT, padx=25, pady=15)
        
        self.slideshow_btn = self._create_button(
            controls, "Slideshow", self.toggle_slideshow, emoji="🎬"
        )
        self.slideshow_btn.pack(side=tk.LEFT, padx=8)
        
        self.video_filter_btn = self._create_button(
            controls, "Videos", self.toggle_video_filter, emoji="🎬"
        )
        self.video_filter_btn.pack(side=tk.LEFT, padx=8)
        
        self.fav_filter_btn = self._create_button(
            controls, "Favorites", self.toggle_favorites, emoji="💗"
        )
        self.fav_filter_btn.pack(side=tk.LEFT, padx=8)
        
        self.export_btn = self._create_button(
            controls, "Export", self.export_selected, emoji="📤"
        )
        self.export_btn.pack(side=tk.LEFT, padx=8)
        
        self.sort_var = tk.StringVar(value="Sort: Date 💕")
        sort_menu = tk.OptionMenu(controls, self.sort_var, 
                                 "Sort: Date 💕", "Sort: Name 🌸", "Sort: Size ✨", 
                                 "Sort: Views 🌟", "Sort: Rating ⭐", "Sort: Random 🎲",
                                 command=self.on_sort_change)
        sort_menu.config(font=self.font_main, bg=self.colors['surface'], 
                        fg=self.colors['text'], relief="flat", highlightthickness=0)
        sort_menu["menu"].config(font=self.font_main, bg=self.colors['surface'], 
                                fg=self.colors['text'])
        sort_menu.pack(side=tk.LEFT, padx=8)
        
        self.add_btn = self._create_button(
            controls, "Add Folder", self.add_folder_dialog, is_accent=True, emoji="📂"
        )
        self.add_btn.pack(side=tk.LEFT, padx=8)
        
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(self.header, textvariable=self.search_var,
                                    font=self.font_main, width=28,
                                    bg=self.colors['bg'], fg=self.colors['text_secondary'],
                                    relief="flat", highlightthickness=2,
                                    highlightcolor=self.colors['accent'],
                                    highlightbackground=self.colors['border'])
        self.search_entry.pack(side=tk.RIGHT, padx=25, pady=20, ipady=10)
        self.search_entry.insert(0, "Search your photos 💕")
        self.search_entry.bind('<FocusIn>', self.on_search_focus_in)
        self.search_entry.bind('<FocusOut>', self.on_search_focus_out)
        self.search_entry.bind('<KeyRelease>', self.on_search)
        
    def create_sidebar(self):
        self.sidebar = tk.Frame(self.main_container, width=200, bg=self.colors['surface'])
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        self.sidebar.pack_propagate(False)
        
        lib_frame = tk.Frame(self.sidebar, bg=self.colors['surface'])
        lib_frame.pack(fill=tk.X, pady=15, padx=15)
        
        tk.Label(lib_frame, text="Library", font=self.font_bold,
                bg=self.colors['surface'], fg=self.colors['text']).pack(anchor=tk.W)
        
        self.all_photos_btn = self._create_sidebar_button(
            lib_frame, "📷 All Photos", self.show_all_photos
        )
        self.all_photos_btn.pack(fill=tk.X, pady=5)
        
        self.sidebar_fav_btn = self._create_sidebar_button(
            lib_frame, "💗 Favorites", self.toggle_favorites
        )
        self.sidebar_fav_btn.pack(fill=tk.X, pady=5)
        
        self.sidebar_video_btn = self._create_sidebar_button(
            lib_frame, "🎬 Videos", self.toggle_video_filter
        )
        self.sidebar_video_btn.pack(fill=tk.X, pady=5)
        
        self.duplicates_btn = self._create_sidebar_button(
            lib_frame, "🔍 Duplicates", self.show_duplicates
        )
        self.duplicates_btn.pack(fill=tk.X, pady=5)
        
        self.trash_btn = self._create_sidebar_button(
            lib_frame, "🗑️ Recently Deleted", self.show_trash
        )
        self.trash_btn.pack(fill=tk.X, pady=5)
        
        batch_frame = tk.Frame(self.sidebar, bg=self.colors['surface'])
        batch_frame.pack(fill=tk.X, pady=15, padx=15)
        
        tk.Label(batch_frame, text="Batch Operations", font=self.font_bold,
                bg=self.colors['surface'], fg=self.colors['text']).pack(anchor=tk.W)
        
        self.batch_fav_btn = self._create_sidebar_button(
            batch_frame, "💗 Favorite Selected", self.batch_favorite
        )
        self.batch_fav_btn.pack(fill=tk.X, pady=5)
        
        self.batch_delete_btn = self._create_sidebar_button(
            batch_frame, "🗑️ Delete Selected", self.batch_delete
        )
        self.batch_delete_btn.pack(fill=tk.X, pady=5)
        
        self.clear_sel_btn = self._create_sidebar_button(
            batch_frame, "✓ Clear Selection", self.clear_selection
        )
        self.clear_sel_btn.pack(fill=tk.X, pady=5)
        
    def _create_sidebar_button(self, parent, text, command):
        btn = tk.Label(parent, text=text, font=self.font_main,
                      bg=self.colors['surface'], fg=self.colors['text'],
                      padx=10, pady=8, cursor="hand2")
        
        def on_enter(e, b=btn):
            b.config(bg=self.colors['surface_hover'])
        def on_leave(e, b=btn):
            b.config(bg=self.colors['surface'])
            
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        btn.bind('<Button-1>', lambda e: command())
        return btn
        
    def create_content(self):
        self.content_frame = tk.Frame(self.main_container, bg=self.colors['bg'])
        self.content_frame.pack(fill=tk.BOTH, expand=True)
        
        self.grid_frame = tk.Frame(self.content_frame, bg=self.colors['bg'])
        self.grid_canvas = tk.Canvas(self.grid_frame, highlightthickness=0, bg=self.colors['bg'])
        self.grid_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        
        scrollbar = ttk.Scrollbar(self.grid_frame, command=self.on_scroll)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.grid_canvas.configure(yscrollcommand=scrollbar.set)
        
        self.grid_inner_frame = tk.Frame(self.grid_canvas, bg=self.colors['bg'])
        self.canvas_window = self.grid_canvas.create_window(
            (0, 0), window=self.grid_inner_frame, anchor="nw", tags="inner"
        )
        
        self.single_frame = tk.Frame(self.content_frame, bg=self.colors['bg'])
        
        self.slideshow_frame = tk.Frame(self.content_frame, bg='black')
        self.slideshow_label = tk.Label(self.slideshow_frame, bg='black')
        self.slideshow_label.pack(fill=tk.BOTH, expand=True)
        
        self.create_single_view_toolbar()
        
    def create_single_view_toolbar(self):
        toolbar = tk.Frame(self.single_frame, height=60, bg=self.colors['surface'])
        toolbar.pack(fill=tk.X, pady=(0, 15))
        
        nav = tk.Frame(toolbar, bg=self.colors['surface'])
        nav.pack(side=tk.LEFT, padx=20, pady=15)
        
        self._create_button(nav, "Back", self.show_grid_view, emoji="←").pack(side=tk.LEFT, padx=5)
        self._create_button(nav, "Prev", self.prev_media, emoji="◀").pack(side=tk.LEFT, padx=5)
        self._create_button(nav, "Next", self.next_media, emoji="▶").pack(side=tk.LEFT, padx=5)
        
        actions = tk.Frame(toolbar, bg=self.colors['surface'])
        actions.pack(side=tk.RIGHT, padx=20)
        
        self.fav_btn = self._create_button(actions, "", self.toggle_favorite_current, emoji="💗")
        self.fav_btn.pack(side=tk.LEFT, padx=5)
        
        self._create_button(actions, "", self.open_current_folder, emoji="📁").pack(side=tk.LEFT, padx=5)
        self._create_button(actions, "", self.reset_zoom, emoji="🔍").pack(side=tk.LEFT, padx=5)
        self._create_button(actions, "", self.copy_current_path, emoji="📋").pack(side=tk.LEFT, padx=5)
        self._create_button(actions, "", self.show_exif_info, emoji="ℹ️").pack(side=tk.LEFT, padx=5)
        
        self.delete_btn = self._create_button(
            actions, "", self.delete_current, emoji="🗑️", 
            bg=self.colors['danger'], hover_bg=self.colors['danger_hover']
        )
        self.delete_btn.pack(side=tk.LEFT, padx=5)
        
        self.media_container = tk.Frame(self.single_frame, bg=self.colors['surface'],
                                       highlightbackground=self.colors['border'],
                                       highlightthickness=2)
        self.media_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        self.image_canvas = tk.Canvas(self.media_container, bg=self.colors['surface'], 
                                     highlightthickness=0, cursor="plus")
        self.image_canvas.pack(fill=tk.BOTH, expand=True)
        
        self.video_frame = tk.Frame(self.media_container, bg=self.colors['surface'])
        
        if HAS_VLC:
            self.video_controls = tk.Frame(self.single_frame, height=50, bg=self.colors['surface'])
            
            self.play_btn = self._create_button(self.video_controls, "", self.toggle_video_playback, emoji="▶")
            self.play_btn.pack(side=tk.LEFT, padx=10)
            
            self.timeline = ttk.Scale(self.video_controls, from_=0, to=100, orient=tk.HORIZONTAL)
            self.timeline.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=15)
            self.timeline.bind('<ButtonRelease-1>', self.seek_video)
            
            self.time_label = tk.Label(self.video_controls, text="0:00 / 0:00",
                                      font=self.font_main, bg=self.colors['surface'], fg=self.colors['text'])
            self.time_label.pack(side=tk.RIGHT, padx=15)
        
        self.info_frame = tk.Frame(self.single_frame, bg=self.colors['bg'])
        self.info_frame.pack(fill=tk.X, pady=15, padx=20)
        
        self.filename_label = tk.Label(self.info_frame, text="", font=self.font_title,
                                      bg=self.colors['bg'], fg=self.colors['text'])
        self.filename_label.pack(anchor=tk.W)
        
        self.details_label = tk.Label(self.info_frame, text="", font=self.font_main,
                                     bg=self.colors['bg'], fg=self.colors['text_secondary'])
        self.details_label.pack(anchor=tk.W, pady=(8, 0))
        
        self.show_grid_view()
        
    def _create_button(self, parent, text, command, is_accent=False, emoji="", 
                      bg=None, hover_bg=None):
        full_text = f"{emoji} {text}" if emoji else text
        
        btn_bg = bg or (self.colors['accent'] if is_accent else self.colors['surface'])
        btn_hover = hover_bg or (self.colors['accent_hover'] if is_accent else self.colors['surface_hover'])
        
        btn = tk.Label(parent, text=full_text, font=self.font_bold if is_accent else self.font_main,
                      bg=btn_bg, fg=self.colors['text'], padx=18, pady=8,
                      cursor="hand2", relief="flat", bd=0)
        btn.config(highlightbackground=self.colors['border'], highlightthickness=1)
        
        def on_enter(e, b=btn):
            b.config(bg=btn_hover)
        def on_leave(e, b=btn):
            b.config(bg=btn_bg)
            
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        btn.bind('<Button-1>', lambda e: command())
        
        return btn
        
    def create_status_bar(self):
        self.status_bar = tk.Frame(self.main_container, height=35, bg=self.colors['surface'])
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM, pady=(20, 0))
        
        self.status_label = tk.Label(self.status_bar, text="Ready 💕", font=self.font_small,
                                    bg=self.colors['surface'], fg=self.colors['text_secondary'])
        self.status_label.pack(side=tk.LEFT, padx=25, pady=8)
        
        self.progress_label = tk.Label(self.status_bar, text="", font=self.font_small,
                                      bg=self.colors['surface'], fg=self.colors['accent'])
        self.progress_label.pack(side=tk.LEFT, padx=20, pady=8)
        
        self.selection_label = tk.Label(self.status_bar, text="", font=self.font_small,
                                       bg=self.colors['surface'], fg=self.colors['accent'])
        self.selection_label.pack(side=tk.RIGHT, padx=25, pady=8)

        
    def bind_events(self):
        self.root.bind('<Left>', lambda e: self.prev_media())
        self.root.bind('<Right>', lambda e: self.next_media())
        self.root.bind('<Escape>', lambda e: self.handle_escape())
        self.root.bind('<f>', lambda e: self.toggle_favorite_current())
        self.root.bind('<Delete>', lambda e: self.delete_current())
        self.root.bind('<Shift-Delete>', lambda e: self.permanently_delete_current())
        self.root.bind('<space>', lambda e: self.toggle_video_playback())
        self.root.bind('<Control-c>', lambda e: self.copy_current_path())
        self.root.bind('<Control-a>', lambda e: self.select_all())
        self.root.bind('<Control-d>', lambda e: self.clear_selection())
        self.root.bind('<s>', lambda e: self.toggle_slideshow())
        
        for i in range(1, 6):
            self.root.bind(f'<Key-{i}>', lambda e, r=i: self.set_rating_current(r))
        
        self.image_canvas.bind("<MouseWheel>", self.zoom_image)
        self.image_canvas.bind("<Button-4>", self.zoom_image)
        self.image_canvas.bind("<Button-5>", self.zoom_image)
        self.image_canvas.bind("<ButtonPress-1>", self.start_pan)
        self.image_canvas.bind("<B1-Motion>", self.pan_image)
        self.image_canvas.bind("<ButtonRelease-1>", self.end_pan)
        self.image_canvas.bind("<Double-Button-1>", self.double_click_zoom)
        
        self.grid_canvas.bind("<MouseWheel>", self.smooth_scroll)
        self.grid_canvas.bind("<Button-4>", self.smooth_scroll)
        self.grid_canvas.bind("<Button-5>", self.smooth_scroll)
        
        self.root.bind("<Configure>", lambda e: self.on_resize())
        
    def handle_escape(self):
        if self.slideshow_active:
            self.stop_slideshow()
        elif self.view_mode == ViewMode.SINGLE:
            self.show_grid_view()
     
    def load_initial_media(self):
        self.load_media_from_db()
      
        if not self.all_media:
            home = Path.home()
            default_dirs = [home / "Pictures", home / "Videos", home / "Downloads"]
            
            for dir_path in default_dirs:
                if dir_path.exists():
                    self.scan_directory_background(str(dir_path))
                    break
                    
    def load_media_from_db(self):
        try:
            rows = self.db.get_all_media(include_deleted=self.showing_deleted)
            
            self.all_media = []
            self.media_by_id.clear()
            self.media_by_path.clear()
            
            for row in rows:
                item = MediaItem(
                    id=row['id'],
                    path=row['path'],
                    media_type=row['media_type'],
                    size=row['size'],
                    mtime=row['mtime'],
                    sha256=row['sha256'],
                    width=row['width'],
                    height=row['height'],
                    duration=row['duration'],
                    view_count=row['view_count'],
                    last_viewed=row['last_viewed'],
                    favorite=bool(row['favorite']),
                    created_at=row['created_at'],
                    soft_delete=bool(row.get('soft_delete', 0)),
                    deleted_at=row.get('deleted_at'),
                    original_path=row.get('original_path'),
                    rating=row.get('rating', 0)
                )
                self.all_media.append(item)
                self.media_by_id[item.id] = item
                self.media_by_path[item.path] = item
                
            self.apply_filters()
            self.update_stats()
            logger.info(f"Loaded {len(self.all_media)} media items from database")
        except Exception as e:
            logger.error(f"Error loading media from DB: {e}")
            self.toast.show("Error loading media library", emoji="⚠️")

    def scan_directory_background(self, directory):
        self.update_status(f"Scanning {directory}... 🌸")
        
        def scan_task():
            image_ext = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.heic'}
            video_ext = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.flv'}
            
            all_files = []
            path_obj = Path(directory)
            
            try:
                for ext in image_ext | video_ext:
                    all_files.extend(path_obj.rglob(f"*{ext}"))
                    all_files.extend(path_obj.rglob(f"*{ext.upper()}"))
            except Exception as e:
                logger.error(f"Error scanning directory: {e}")
                return
                
            all_files = list(dict.fromkeys(all_files))
            total = len(all_files)
            batch = []
            added_count = 0
            
            for idx, file_path in enumerate(all_files):
                file_path = str(file_path)
                ext = Path(file_path).suffix.lower()
                
                is_video = ext in video_ext
                media_type = 'video' if is_video else 'image'
                
                try:
                    stat = os.stat(file_path)
                    size = stat.st_size
                    mtime = stat.st_mtime
                    
                    width = height = duration = None
                    
                    if is_video:
                        cap = cv2.VideoCapture(file_path)
                        if cap.isOpened():
                            ret, frame = cap.read()
                            if ret:
                                height, width = frame.shape[:2]
                            fps = cap.get(cv2.CAP_PROP_FPS)
                            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                            if fps > 0:
                                duration = int(frame_count / fps)
                        cap.release()
                    else:
                        try:
                            with Image.open(file_path) as img:
                                img = ImageOps.exif_transpose(img)
                                width, height = img.size
                        except:
                            pass
                    
                    batch.append({
                        'path': file_path,
                        'media_type': media_type,
                        'size': size,
                        'mtime': mtime,
                        'width': width,
                        'height': height,
                        'duration': duration
                    })
                    
                    if len(batch) >= Config.SCAN_BATCH_SIZE:
                        added = self._insert_batch(batch)
                        added_count += added
                        batch = []
                    
                    if idx % 10 == 0:
                        self.tk_queue.put(lambda i=idx, t=total: 
                                        self.progress_label.config(text=f"Loading {i}/{t} ✨"))
                        
                except Exception as e:
                    logger.debug(f"Error scanning {file_path}: {e}")
            
            if batch:
                added = self._insert_batch(batch)
                added_count += added
                    
            self.tk_queue.put(lambda: self.finish_scan(added_count))
            
        self.worker.submit(f"scan_{directory}", scan_task)
        
    def _insert_batch(self, batch):
        added = 0
        for item in batch:
            try:
                _, is_new = self.db.get_or_create_media(**item)
                if is_new:
                    added += 1
            except Exception as e:
                logger.debug(f"Error inserting {item['path']}: {e}")
        return added
        
    def finish_scan(self, added_count):
        self.progress_label.config(text="")
        self.update_status("All done! 💕")
        self.toast.show(f"Scan complete! Added {added_count} new items")
        self.load_media_from_db()
        
    def add_single_file(self, path):
        if not os.path.isfile(path):
            return
            
        ext = Path(path).suffix.lower()
        image_ext = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.heic'}
        video_ext = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.flv'}
        
        if ext not in image_ext and ext not in video_ext:
            return
            
        is_video = ext in video_ext
        media_type = 'video' if is_video else 'image'
        
        try:
            stat = os.stat(path)
            size = stat.st_size
            mtime = stat.st_mtime
            
            width = height = duration = None
            
            if is_video:
                cap = cv2.VideoCapture(path)
                if cap.isOpened():
                    ret, frame = cap.read()
                    if ret:
                        height, width = frame.shape[:2]
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                    if fps > 0:
                        duration = int(frame_count / fps)
                cap.release()
            else:
                try:
                    with Image.open(path) as img:
                        img = ImageOps.exif_transpose(img)
                        width, height = img.size
                except:
                    pass
            
            self.db.get_or_create_media(
                path, media_type, size, mtime,
                width=width, height=height, duration=duration
            )
            
            self.load_media_from_db()
            self.toast.show(f"Added {os.path.basename(path)}")
            
        except Exception as e:
            logger.error(f"Error adding file {path}: {e}")
            
    def apply_filters(self):
        filtered = self.all_media.copy()
        
        if self.showing_favorites:
            filtered = [m for m in filtered if m.favorite]
            
        if self.showing_videos_only:
            filtered = [m for m in filtered if m.is_video]
            
        if self.showing_deleted:
            filtered = [m for m in filtered if m.soft_delete]
        else:
            filtered = [m for m in filtered if not m.soft_delete]
            
        if self.filter_query:
            q = self.filter_query.lower()
            filtered = [m for m in filtered if q in m.filename.lower()]
            
        if self.sort_mode == SortMode.DATE:
            filtered.sort(key=lambda m: m.mtime, reverse=True)
        elif self.sort_mode == SortMode.NAME:
            filtered.sort(key=lambda m: m.filename.lower())
        elif self.sort_mode == SortMode.SIZE:
            filtered.sort(key=lambda m: m.size, reverse=True)
        elif self.sort_mode == SortMode.VIEWS:
            filtered.sort(key=lambda m: m.view_count, reverse=True)
        elif self.sort_mode == SortMode.RATING:
            filtered.sort(key=lambda m: m.rating, reverse=True)
        elif self.sort_mode == SortMode.RANDOM:
            import random
            random.shuffle(filtered)
            
        self.media = filtered
        self.refresh_grid()
        
    def refresh_grid(self):
        """Anti-flicker grid refresh using widget recycling and virtual scrolling"""
        with self._render_lock:
            if self._refreshing:
                return
            self._refreshing = True
            
            try:
                if not hasattr(self, 'grid_canvas') or not self.grid_canvas.winfo_exists():
                    return
                
                try:
                    self.grid_canvas.update_idletasks()
                    canvas_width = self.grid_canvas.winfo_width() - 30
                    canvas_width = max(canvas_width, 400)
                except tk.TclError:
                    return
                
                total_item_width = self.thumb_size + self.thumb_padding
                if total_item_width <= 0:
                    return
                
                new_columns = max(3, canvas_width // total_item_width)
                
                if self.columns != new_columns or not self.visible_thumbs:
                    self.columns = new_columns
                    self._recycle_thumbnail_layout(canvas_width)
                else:
                    self._update_thumbnail_selections()
                
                self.update_scroll_region()
                
            finally:
                self._refreshing = False

    def _recycle_thumbnail_layout(self, canvas_width):
        """Recycle existing widgets, only create/destroy as needed"""
        if canvas_width <= 0:
            canvas_width = max(self.grid_canvas.winfo_width() - 30, 400)
        if canvas_width <= 0:
            return  
        
        try:
            self.grid_canvas.itemconfig(self.canvas_window, width=canvas_width)
        except tk.TclError:
            self.canvas_window = self.grid_canvas.create_window(
                (0, 0), window=self.grid_inner_frame, anchor="nw", tags="inner", width=canvas_width
            )
        
        self.grid_inner_frame.config(width=canvas_width)
        
        if not self.media:
            self._clear_all_thumbnails()
            self.show_empty_state()
            return
        
        if self.thumb_size <= 0 or self.thumb_padding < 0:
            return
        
        start, end = self.get_visible_range()
        visible_indices = set(range(start, end))
        all_needed = set(range(len(self.media)))
        
        current_indices = set(self.visible_thumbs.keys())
        to_remove = current_indices - all_needed
        for idx in to_remove:
            self._remove_thumbnail(idx)
        
        for idx in current_indices & all_needed:
            if idx not in visible_indices:
                frame = self.visible_thumbs[idx]
                frame.grid_remove()
        
        for idx in visible_indices:
            if idx < len(self.media):
                if idx in self.visible_thumbs:
                    self._reposition_thumbnail(idx)
                else:
                    self._create_thumbnail_widget_fast(idx)

    def _update_thumbnail_selections(self):
        """Update colors only, no repositioning"""
        for idx, frame in list(self.visible_thumbs.items()):
            if idx >= len(self.media):
                self._remove_thumbnail(idx)
                continue
            
            item = self.media[idx]
            is_selected = item.id in self.selected_items
            
            bg_color = self.colors['surface_selected'] if is_selected else self.colors['surface']
            border_color = self.colors['selected'] if is_selected else self.colors['border']
            
            frame.config(bg=bg_color, highlightbackground=border_color,
                        highlightthickness=3 if is_selected else 2)

    def _reposition_thumbnail(self, idx):
        """Move existing widget to new grid position"""
        if idx not in self.visible_thumbs:
            return
        
        if idx >= len(self.media):
            self._remove_thumbnail(idx)
            return
        
        frame = self.visible_thumbs[idx]
        item = self.media[idx]
        
        row = idx // self.columns
        col = idx % self.columns
        
        frame.grid(row=row, column=col, padx=self.thumb_padding//2, pady=self.thumb_padding//2)
        
        frame.media_path = item.path
        frame.media_id = item.id
        frame.media_idx = idx
        
        is_selected = item.id in self.selected_items
        bg_color = self.colors['surface_selected'] if is_selected else self.colors['surface']
        border_color = self.colors['selected'] if is_selected else self.colors['border']
        
        frame.config(bg=bg_color, highlightbackground=border_color,
                    highlightthickness=3 if is_selected else 2)

    def _create_thumbnail_widget_fast(self, idx):
        """Create thumbnail with minimal flicker"""
        if idx >= len(self.media):
            return
        
        if self.thumb_size <= 0:
            return
        
        item = self.media[idx]
        row = idx // self.columns
        col = idx % self.columns
        
        is_selected = item.id in self.selected_items
        bg_color = self.colors['surface_selected'] if is_selected else self.colors['surface']
        border_color = self.colors['selected'] if is_selected else self.colors['border']
        
        frame = tk.Frame(self.grid_inner_frame, width=self.thumb_size, height=self.thumb_size,
                        bg=bg_color, highlightbackground=border_color,
                        highlightthickness=3 if is_selected else 2)
        frame.grid(row=row, column=col, padx=self.thumb_padding//2, pady=self.thumb_padding//2)
        frame.grid_propagate(False)
        
        frame.media_path = item.path
        frame.media_id = item.id
        frame.media_idx = idx
        
        placeholder_size = min(100, self.thumb_size - 20)
        if placeholder_size > 0:
            placeholder = tk.Label(frame, text="⏳", font=("Segoe UI", 20),
                                  bg=bg_color, fg=self.colors['text_secondary'])
            placeholder.place(relx=0.5, rely=0.5, anchor="center")
        
        self.visible_thumbs[idx] = frame
        
        frame.bind("<Enter>", lambda e, f=frame, i=idx: self._on_thumb_enter(e, f, i))
        frame.bind("<Leave>", lambda e, f=frame: self._on_thumb_leave(e, f))
        frame.bind("<Button-1>", lambda e, f=frame: self._on_thumbnail_click(e, f))
        frame.config(cursor="hand2")
        
        start, end = self.get_visible_range()
        priority = 0 if start <= idx < end else 2
        
        task_id = f"thumb_{idx}_{item.path}"
        self.thumb_loader.submit(
            task_id, priority,
            lambda p=item.path: self._load_thumbnail_image(p),
            lambda result, f=frame, i=idx: self._apply_thumbnail_image(f, i, result)
        )
        
    def _refresh_thumbnail_by_item_id(self, item_id):
        """Refresh specific thumbnail by item ID"""
        for idx, media_item in enumerate(self.media):
            if media_item.id == item_id:
                if idx in self.visible_thumbs:
                    self._remove_thumbnail(idx)
                    self._create_thumbnail_widget_fast(idx)
                break 

    def _on_thumb_enter(self, event, frame, idx):
        """Hover enter with debounced preview"""
        item = self.media[idx]
        if item.id not in self.selected_items:
            frame.config(bg=self.colors['surface_hover'], highlightbackground=self.colors['accent'])
        frame.tkraise()
        
        if self.preview_after_id:
            self.root.after_cancel(self.preview_after_id)
        
        self.preview_after_id = self.root.after(Config.PREVIEW_DELAY_MS, 
                                                lambda: self.show_preview(item.path, event.x_root, event.y_root))

    def _on_thumb_leave(self, event, frame):
        """Hover exit with preview cancel"""
        idx = getattr(frame, 'media_idx', None)
        if idx is not None and idx < len(self.media):
            item = self.media[idx]
            if item.id not in self.selected_items:
                frame.config(bg=self.colors['surface'], highlightbackground=self.colors['border'])
        
        if self.preview_after_id:
            self.root.after_cancel(self.preview_after_id)
            self.preview_after_id = None
        self.hide_preview()

    def _load_thumbnail_image(self, path):
        """Load thumbnail image in background thread"""
        try:
            item = self.media_by_path.get(path)
            if not item:
                return None
                
            if item.is_video:
                stat = os.stat(path)
                cache_key = self.thumb_cache.compute_content_hash(path, stat)
                cached = self.thumb_cache.get(cache_key)
                
                if cached:
                    img = cached.copy()
                else:
                    cap = cv2.VideoCapture(path)
                    ret, frame = cap.read()
                    if ret:
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        img = Image.fromarray(frame)
                        self.thumb_cache.put(cache_key, img.copy())
                    else:
                        img = Image.new('RGB', (140, 140), self.colors['surface'])
                    cap.release()
            else:
                with Image.open(path) as img_full:
                    img = img_full.convert('RGB')
                    img = ImageOps.exif_transpose(img)
            
            img.thumbnail((140, 140), Config.THUMB_QUALITY)
            return (img, item.favorite, item.is_video, item.rating)
            
        except Exception as e:
            logger.debug(f"Thumbnail load error: {e}")
            return None

    def _apply_thumbnail_image(self, frame, idx, result):
        """Apply loaded image to thumbnail widget"""
        if not frame.winfo_exists():
            return
        
        current_idx = getattr(frame, 'media_idx', None)
        if current_idx != idx:
            return
        
        for widget in frame.winfo_children():
            widget.destroy()
        
        if result is None:
            tk.Label(frame, text="💔", font=("Segoe UI", 24),
                    bg=frame.cget('bg'), fg=self.colors['danger']).place(relx=0.5, rely=0.5, anchor="center")
            return
        
        img, is_fav, is_video, rating = result
        
        photo = ImageTk.PhotoImage(img)
        frame.photo = photo
        
        img.close()
        
        lbl = tk.Label(frame, image=photo, bg=frame.cget('bg'))
        lbl.place(relx=0.5, rely=0.45, anchor="center")
        
        if is_fav:
            tk.Label(frame, text="♥", font=("Segoe UI", 12),
                    fg=self.colors['favorite'], bg=frame.cget('bg')).place(x=5, y=5)
        
        if rating > 0:
            stars = "★" * rating
            tk.Label(frame, text=stars, font=("Segoe UI", 8),
                    fg=self.colors['accent'], bg=frame.cget('bg')).place(x=5, y=25)
        
        if is_video:
            tk.Label(frame, text="▶", font=("Segoe UI", 10),
                    fg=self.colors['video'], bg=frame.cget('bg')).place(relx=0.5, y=2, anchor="n")
        
        name = os.path.basename(frame.media_path)
        if len(name) > 15:
            name = name[:12] + "..."
        tk.Label(frame, text=name, font=self.font_small,
                bg=frame.cget('bg'), fg=self.colors['text_secondary']).place(relx=0.5, rely=0.88, anchor="center")

    def _remove_thumbnail(self, idx):
        """Remove thumbnail widget completely"""
        if idx in self.visible_thumbs:
            frame = self.visible_thumbs[idx]
            try:
                if frame.winfo_exists():
                    frame.destroy()
            except:
                pass
            del self.visible_thumbs[idx]
        
        for task_id in list(self.thumb_loader.pending_futures.keys()):
            if task_id.startswith(f"thumb_{idx}_"):
                self.thumb_loader.cancel(task_id)

    def _clear_all_thumbnails(self):
        """Clear all thumbnails"""
        self.thumb_loader.cancel_all()
        
        for idx in list(self.visible_thumbs.keys()):
            frame = self.visible_thumbs[idx]
            try:
                if frame.winfo_exists():
                    frame.destroy()
            except:
                pass
        
        self.visible_thumbs.clear()
        self.selected_items.clear()
        self.update_selection_label()

    def get_visible_range(self):
        """Calculate visible thumbnail indices for virtual scrolling"""
        if not self.media:
            return 0, 0
        
        canvas_height = self.grid_canvas.winfo_height()
        if canvas_height <= 0:
            canvas_height = 600  
        
        first_y = self.grid_canvas.canvasy(0)
        last_y = self.grid_canvas.canvasy(canvas_height)
        
        item_height = self.thumb_size + self.thumb_padding
        if item_height <= 0:
            return 0, min(len(self.media), 50) 
        
        start_row = max(0, int(first_y // item_height) - 1)
        end_row = int(last_y // item_height) + 2
        
        start = start_row * self.columns
        end = end_row * self.columns
        
        start = max(0, start)
        end = min(len(self.media), end + self.columns)
        
        return start, end

    def update_visible_thumbnails(self):
        """Update which thumbnails are visible based on scroll position"""
        if not self.media or self._refreshing:
            return
        
        start, end = self.get_visible_range()
        visible_range = range(start, end)
        
        for idx, frame in self.visible_thumbs.items():
            if idx in visible_range:
                if frame.winfo_ismapped():
                    continue
                self._reposition_thumbnail(idx)
            else:
                if frame.winfo_ismapped():
                    frame.grid_remove()
        
        for idx in visible_range:
            if idx < len(self.media) and idx not in self.visible_thumbs:
                self._create_thumbnail_widget_fast(idx)
        
        current_indices = set(self.visible_thumbs.keys())
        viewport_buffer = set(range(max(0, start - self.columns * 2), 
                                    min(len(self.media), end + self.columns * 2)))
        
        to_remove = current_indices - viewport_buffer
        for idx in to_remove:
            self._remove_thumbnail(idx)

    def _on_thumbnail_click(self, event, frame):
        """Handle thumbnail click with selection logic"""
        if not frame.winfo_exists():
            return
        
        path = getattr(frame, 'media_path', None)
        idx = getattr(frame, 'media_idx', None)
        
        if path is None or idx is None:
            return
        
        item = self.media_by_path.get(path)
        if not item:
            return
        
        if event.state & 0x4:
            self.toggle_selection(item)
            self.refresh_grid()
        elif event.state & 0x1:
            if self.last_selected_idx is not None:
                start = min(self.last_selected_idx, idx)
                end = max(self.last_selected_idx, idx)
                for i in range(start, end + 1):
                    if i < len(self.media):
                        self.selected_items.add(self.media[i].id)
                self.refresh_grid()
            else:
                self.toggle_selection(item)
                self.last_selected_idx = idx
        else:
            self.clear_selection()
            self.last_selected_idx = idx
            self.open_media(item)

    def toggle_selection(self, item):
        """Toggle item selection state"""
        if item.id in self.selected_items:
            self.selected_items.remove(item.id)
        else:
            self.selected_items.add(item.id)
        self.update_selection_label()

    def clear_selection(self):
        """Clear all selections"""
        self.selected_items.clear()
        self.last_selected_idx = None
        self.refresh_grid()
        self.update_selection_label()

    def select_all(self):
        """Select all visible items"""
        for item in self.media:
            self.selected_items.add(item.id)
        self.refresh_grid()
        self.update_selection_label()

    def update_selection_label(self):
        """Update selection count display"""
        count = len(self.selected_items)
        if count > 0:
            self.selection_label.config(text=f"{count} selected")
        else:
            self.selection_label.config(text="")

    def update_scroll_region(self):
        """Update canvas scroll region based on content"""
        if not self.media:
            return
        rows = math.ceil(len(self.media) / self.columns)
        height = rows * (self.thumb_size + self.thumb_padding)
        self.grid_canvas.config(scrollregion=(0, 0, 0, height))

    def on_scroll(self, *args):
        """Debounced scroll handler"""
        self.grid_canvas.yview(*args)
        
        if self._scroll_update_after is not None:
            try:
                self.root.after_cancel(self._scroll_update_after)
            except:
                pass
        
        self._scroll_update_after = self.root.after(50, self.update_visible_thumbnails)

    def smooth_scroll(self, event):
        """Smooth scroll with debounce"""
        if self.is_linux:
            delta = 3 if event.num == 4 else -3 if event.num == 5 else 0
        else:
            delta = event.delta // 40 if abs(event.delta) > 10 else event.delta // 4
        
        self.grid_canvas.yview_scroll(int(-delta), "units")
        
        if self._scroll_update_after is not None:
            try:
                self.root.after_cancel(self._scroll_update_after)
            except:
                pass
        
        self._scroll_update_after = self.root.after(50, self.update_visible_thumbnails)

    def on_resize(self, event=None):
        """Debounced resize handler"""
        if self.view_mode != ViewMode.GRID:
            return
        
        if self._resize_after is not None:
            try:
                self.root.after_cancel(self._resize_after)
            except:
                pass
        
        self._resize_after = self.root.after(Config.RESIZE_DEBOUNCE_MS, self.refresh_grid)

    def show_preview(self, path, x, y):
        """Show image preview popup"""
        if self.preview_window:
            self.preview_window.destroy()
        
        item = self.media_by_path.get(path)
        if not item or item.is_video:
            return
        
        try:
            self.preview_window = tk.Toplevel(self.root)
            self.preview_window.overrideredirect(True)
            self.preview_window.attributes('-topmost', True)
            
            preview_size = 300
            x = min(x + 20, self.root.winfo_screenwidth() - preview_size - 20)
            y = min(y + 20, self.root.winfo_screenheight() - preview_size - 20)
            
            self.preview_window.geometry(f"{preview_size}x{preview_size}+{x}+{y}")
            
            with Image.open(path) as img:
                img = img.convert('RGB')
                img = ImageOps.exif_transpose(img)
                img.thumbnail((preview_size, preview_size), Config.THUMB_QUALITY)
                photo = ImageTk.PhotoImage(img)
            
            self.preview_window.photo = photo
            
            lbl = tk.Label(self.preview_window, image=photo, bg=self.colors['surface'],
                          highlightbackground=self.colors['accent'], highlightthickness=2)
            lbl.pack(fill=tk.BOTH, expand=True)
            
        except Exception as e:
            logger.debug(f"Preview error: {e}")
            self.hide_preview()

    def hide_preview(self):
        """Hide preview popup"""
        if self.preview_window:
            try:
                self.preview_window.destroy()
            except:
                pass
            self.preview_window = None

    def show_empty_state(self):
        """Display empty gallery message"""
        for widget in self.grid_inner_frame.winfo_children():
            try:
                if widget not in [self.visible_thumbs.get(i) for i in self.visible_thumbs]:
                    widget.destroy()
            except:
                pass
        
        for widget in self.grid_inner_frame.winfo_children():
            if isinstance(widget, tk.Frame) and not hasattr(widget, 'media_path'):
                return
        
        empty = tk.Frame(self.grid_inner_frame, bg=self.colors['bg'])
        empty.pack(expand=True, fill=tk.BOTH, pady=100)
        
        tk.Label(empty, text="🎀", font=("Segoe UI", 72), 
                bg=self.colors['bg'], fg=self.colors['accent']).pack()
        
        if self.showing_deleted:
            msg = "Your trash is empty"
        elif self.showing_favorites:
            msg = "No favorites yet\nMark items as favorites to see them here 💗"
        elif self.filter_query:
            msg = f"No results for '{self.filter_query}'\nTry a different search term 🔍"
        else:
            msg = "Your gallery is empty\nAdd photos to get started 💗"
        
        tk.Label(empty, text=msg, font=self.font_title,
                bg=self.colors['bg'], fg=self.colors['text_secondary'], justify="center").pack(pady=20)
        
        if not self.showing_deleted and not self.showing_favorites and not self.filter_query:
            tk.Label(empty, text="Drag a folder here or click 📂 Add Folder",
                    font=self.font_main, bg=self.colors['bg'], 
                    fg=self.colors['text_secondary']).pack()

    def open_media(self, item):
        """Open media item in single view"""
        if isinstance(item, str):
            item = self.media_by_path.get(item)
        if not item:
            return
        
        self.current_image_path = item.path
        
        try:
            self.current_index = self.media.index(item)
        except ValueError:
            self.current_index = 0
        
        self.show_single_view()
        
        self.db.update_view_stats(item.id)
        item.view_count += 1
        
        if item.is_video and HAS_VLC:
            self.play_video(item.path)
        else:
            self.show_image(item.path)
        
        if self.current_index + 1 < len(self.media):
            next_item = self.media[self.current_index + 1]
            if next_item.is_image:
                self.worker.submit("preload", lambda p=next_item.path: self._preload_image(p))

    def _preload_image(self, path):
        """Preload image for smoother navigation"""
        try:
            with Image.open(path) as img:
                img.convert('RGB')
        except:
            pass

    def show_image(self, path):
        """Display image in single view"""
        self.image_canvas.delete("all")
        
        if self.original_image:
            try:
                self.original_image.close()
            except:
                pass
        
        self.canvas_image_id = None
        self.original_image = None
        self.zoom_cache = OrderedDict()
        self.zoom_level = 1.0
        self.pan_x = 0
        self.pan_y = 0
        
        self.video_frame.pack_forget()
        if HAS_VLC:
            self.video_controls.pack_forget()
        self.image_canvas.pack(fill=tk.BOTH, expand=True)
        
        try:
            self.original_image = Image.open(path).convert('RGB')
            self.original_image = ImageOps.exif_transpose(self.original_image)
        except Exception as e:
            logger.error(f"Error loading image {path}: {e}")
            self.image_canvas.create_text(
                self.image_canvas.winfo_width()//2, 
                self.image_canvas.winfo_height()//2,
                text="💔 Failed to load image", font=self.font_title,
                fill=self.colors['danger']
            )
            return
        
        self.reset_zoom()
        
        item = self.media_by_path.get(path)
        if item:
            self.filename_label.config(text=item.filename)
            size_str = f"{item.width or '?'}x{item.height or '?'}"
            self.details_label.config(text=f"{size_str} • {item.format_size()}")
            self.fav_btn.config(fg=self.colors['favorite'] if item.favorite else self.colors['text'])

    def play_video(self, path):
        """Play video in single view"""
        self.image_canvas.pack_forget()
        self.video_frame.pack(fill=tk.BOTH, expand=True)
        
        if not self.vlc_attached:
            self._attach_vlc_window()
        
        if HAS_VLC and self.vlc_player:
            media = self.vlc_instance.media_new(path)
            self.vlc_player.set_media(media)
            self.vlc_player.play()
            
            item = self.media_by_path.get(path)
            if item and item.duration:
                self.time_label.config(text=f"0:00 / {item.format_duration()}")
            
            self.video_controls.pack(fill=tk.X, pady=10, padx=20)
            self.update_video_timeline()

    def _attach_vlc_window(self):
        """Attach VLC player to window"""
        if HAS_VLC and not self.vlc_attached:
            try:
                if self.is_windows:
                    self.vlc_player.set_hwnd(self.video_frame.winfo_id())
                else:
                    self.vlc_player.set_xwindow(self.video_frame.winfo_id())
                self.vlc_attached = True
            except Exception as e:
                logger.error(f"VLC attach error: {e}")

    def render_zoomed_image(self):
        """Render zoomed image with caching"""
        if not self.original_image:
            return
        
        if not self.image_canvas.winfo_exists():
            return
        
        scale_key = round(self.zoom_level, 2)
        
        if scale_key in self.zoom_cache:
            resized = self.zoom_cache[scale_key]
        else:
            new_w = int(self.original_image.width * self.zoom_level)
            new_h = int(self.original_image.height * self.zoom_level)
            resized = self.original_image.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
            if len(self.zoom_cache) >= Config.ZOOM_CACHE_SIZE:
                self.zoom_cache.popitem(last=False)
            
            self.zoom_cache[scale_key] = resized
        
        self.current_photo = ImageTk.PhotoImage(resized)
        
        if self.canvas_image_id:
            try:
                self.image_canvas.itemconfig(self.canvas_image_id, image=self.current_photo)
                return
            except tk.TclError:
                self.canvas_image_id = None
        
        container_w = self.media_container.winfo_width() - 40
        container_h = self.media_container.winfo_height() - 40
        x = container_w//2 + self.pan_x
        y = container_h//2 + self.pan_y
        
        self.canvas_image_id = self.image_canvas.create_image(
            x, y, image=self.current_photo, anchor="center"
        )

    def reset_zoom(self, event=None):
        """Reset zoom to fit image"""
        if not self.original_image:
            return
        
        container_w = self.media_container.winfo_width() - 40
        container_h = self.media_container.winfo_height() - 40
        
        scale_w = container_w / self.original_image.width
        scale_h = container_h / self.original_image.height
        
        self.zoom_level = min(scale_w, scale_h)
        self.pan_x = 0
        self.pan_y = 0
        self.zoom_cache = OrderedDict()
        self.canvas_image_id = None
        
        self.render_zoomed_image()

    def zoom_image(self, event):
        """Zoom image with mouse wheel"""
        if not self.original_image:
            return
        
        if hasattr(event, 'delta'):
            if event.delta > 0:
                self.zoom_level *= 1.1
            else:
                self.zoom_level *= 0.9
        else:
            if event.num == 4:
                self.zoom_level *= 1.1
            elif event.num == 5:
                self.zoom_level *= 0.9
        
        self.zoom_level = max(0.1, min(5.0, self.zoom_level))
        self.render_zoomed_image()

    def start_pan(self, event):
        """Start image panning"""
        self.is_panning = True
        self.pan_start_x = event.x
        self.pan_start_y = event.y
        self.image_canvas.config(cursor="fleur")

    def pan_image(self, event):
        """Pan image with mouse drag"""
        if not self.is_panning or self.canvas_image_id is None:
            return
        
        dx = event.x - self.pan_start_x
        dy = event.y - self.pan_start_y
        
        self.image_canvas.move(self.canvas_image_id, dx, dy)
        
        self.pan_start_x = event.x
        self.pan_start_y = event.y

    def end_pan(self, event):
        """End image panning"""
        self.is_panning = False
        self.image_canvas.config(cursor="plus")

    def double_click_zoom(self, event):
        """Toggle zoom on double click"""
        if self.zoom_level > 1.5:
            self.reset_zoom()
        else:
            self.zoom_level = 2.5
            self.render_zoomed_image()

    def show_grid_view(self):
        """Switch to grid view"""
        self.view_mode = ViewMode.GRID
        self.single_frame.pack_forget()
        self.slideshow_frame.pack_forget()
        self.grid_frame.pack(fill=tk.BOTH, expand=True)
        
        self.hide_preview()
        self.stop_slideshow()
        
        if HAS_VLC and self.vlc_player:
            try:
                self.vlc_player.stop()
            except:
                pass
        
        self.update_visible_thumbnails()

    def show_single_view(self):
        """Switch to single view"""
        self.view_mode = ViewMode.SINGLE
        self.grid_frame.pack_forget()
        self.slideshow_frame.pack_forget()
        self.single_frame.pack(fill=tk.BOTH, expand=True)

    def show_all_photos(self):
        """Show all photos filter"""
        self.showing_favorites = False
        self.showing_videos_only = False
        self.showing_deleted = False
        self.clear_selection()
        self.apply_filters()

    def show_trash(self):
        """Show trash filter"""
        self.showing_favorites = False
        self.showing_videos_only = False
        self.showing_deleted = True
        self.clear_selection()
        self.apply_filters()

    def show_duplicates(self):
        """Show duplicate images"""
        duplicates = self.db.get_duplicates()
        if not duplicates:
            self.toast.show("No duplicates found!", emoji="✨")
            return
        
        duplicate_hashes = {d['sha256'] for d in duplicates}
        self.all_media = [m for m in self.all_media if m.sha256 in duplicate_hashes]
        self.apply_filters()
        self.toast.show(f"Found {len(duplicates)} duplicate groups", emoji="🔍")

    def toggle_slideshow(self):
        """Toggle slideshow mode"""
        if self.slideshow_active:
            self.stop_slideshow()
        else:
            self.start_slideshow()

    def start_slideshow(self):
        """Start slideshow"""
        if not self.media:
            self.toast.show("No media to display", emoji="⚠️")
            return
        
        self.slideshow_active = True
        self.slideshow_btn.config(bg=self.colors['accent'])
        
        self.grid_frame.pack_forget()
        self.single_frame.pack_forget()
        self.slideshow_frame.pack(fill=tk.BOTH, expand=True)
        
        self.show_slideshow_image()

    def stop_slideshow(self):
        """Stop slideshow"""
        if not self.slideshow_active:
            return
        
        self.slideshow_active = False
        self.slideshow_btn.config(bg=self.colors['surface'])
        
        if self.slideshow_after_id is not None:
            try:
                self.root.after_cancel(self.slideshow_after_id)
            except Exception:
                pass
            self.slideshow_after_id = None
        
        self.show_grid_view()

    def show_slideshow_image(self):
        """Show next slideshow image"""
        if not self.slideshow_active or not self.media:
            return
        
        import random
        image_items = [m for m in self.media if m.is_image]
        if not image_items:
            self.toast.show("No images for slideshow", emoji="⚠️")
            self.stop_slideshow()
            return
        
        item = random.choice(image_items)
        
        try:
            with Image.open(item.path) as img:
                img = img.convert('RGB')
                img = ImageOps.exif_transpose(img)
                
                screen_w = self.slideshow_frame.winfo_width()
                screen_h = self.slideshow_frame.winfo_height()
                img.thumbnail((screen_w, screen_h), Image.Resampling.LANCZOS)
                
                photo = ImageTk.PhotoImage(img)
                self.slideshow_label.config(image=photo)
                self.slideshow_label.image = photo
                
        except Exception as e:
            logger.error(f"Slideshow image error: {e}")
        
        self.slideshow_after_id = self.root.after(Config.SLIDESHOW_INTERVAL_MS, self.show_slideshow_image)

    def batch_favorite(self):
        """Batch favorite selected items"""
        if not self.selected_items:
            self.toast.show("No items selected", emoji="⚠️")
            return
        
        count = self.db.set_favorite_batch(list(self.selected_items), favorite=True)
        
        for item in self.all_media:
            if item.id in self.selected_items:
                item.favorite = True
        
        self.clear_selection()
        self.refresh_grid()
        self.update_stats()
        self.toast.show(f"Favorited {count} items", emoji="💗")

    def batch_delete(self):
        """Batch delete selected items"""
        if not self.selected_items:
            self.toast.show("No items selected", emoji="⚠️")
            return
        
        if not messagebox.askyesno("Confirm Delete", 
                                   f"Move {len(self.selected_items)} items to trash?"):
            return
        
        results = self.db.soft_delete_batch(list(self.selected_items), str(self.trash_dir))
        success_count = sum(1 for _, success, _ in results if success)
        
        for item in self.all_media:
            if item.id in self.selected_items:
                for media_id, success, result in results:
                    if media_id == item.id and success:
                        item.soft_delete = True
                        item.deleted_at = datetime.now()
                        item.path = result
                        item.original_path = item.path
                        self.media_by_path[item.path] = item
                        break
        
        self.clear_selection()
        self.apply_filters()
        self.update_stats()
        self.toast.show(f"Moved {success_count} items to trash", emoji="🗑️")

    def export_selected(self):
        """Export selected items"""
        if not self.selected_items:
            self.toast.show("No items selected", emoji="⚠️")
            return
        
        export_type = messagebox.askyesnocancel(
            "Export", 
            "Export to:\n\nYes = Folder\nNo = ZIP file\nCancel = Abort"
        )
        
        if export_type is None:
            return
        
        if export_type:
            dest = filedialog.askdirectory(title="Select export folder")
            if not dest:
                return
            
            exported = 0
            for item_id in self.selected_items:
                item = self.media_by_id.get(item_id)
                if item and os.path.exists(item.path):
                    try:
                        shutil.copy2(item.path, dest)
                        exported += 1
                    except Exception as e:
                        logger.error(f"Export error: {e}")
            
            self.toast.show(f"Exported {exported} items", emoji="📤")
        
        else:
            dest = filedialog.asksaveasfilename(
                defaultextension=".zip",
                filetypes=[("ZIP files", "*.zip")]
            )
            if not dest:
                return
            
            try:
                with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for item_id in self.selected_items:
                        item = self.media_by_id.get(item_id)
                        if item and os.path.exists(item.path):
                            zf.write(item.path, item.filename)
                
                self.toast.show(f"Created {os.path.basename(dest)}", emoji="📦")
            except Exception as e:
                logger.error(f"ZIP export error: {e}")
                messagebox.showerror("Error", f"Export failed: {e}")

    def delete_current(self):
        """Delete current media item"""
        if not self.media or self.current_index >= len(self.media):
            return
        
        item = self.media[self.current_index]
        
        if not messagebox.askyesno("Confirm Delete", 
                                   f"Move '{item.filename}' to Recently Deleted?\n\n"
                                   f"Items are permanently removed after {Config.TRASH_RETENTION_DAYS} days."):
            return
        
        success, result = self.db.soft_delete_media(item.id, str(self.trash_dir))
        
        if success:
            self.toast.show(f"Moved to trash", emoji="🗑️")
            item.soft_delete = True
            item.deleted_at = datetime.now()
            item.path = result
            self.media_by_path[item.path] = item
            
            if self.current_index < len(self.media):
                self.media.pop(self.current_index)
            
            if self.current_index < len(self.media):
                self.open_media(self.media[self.current_index])
            elif self.media:
                self.current_index = len(self.media) - 1
                self.open_media(self.media[self.current_index])
            else:
                self.show_grid_view()
                self.refresh_grid()
        else:
            messagebox.showerror("Error", f"Failed to delete: {result}")

    def permanently_delete_current(self):
        """Permanently delete current item"""
        if not self.media or self.current_index >= len(self.media):
            return
        
        item = self.media[self.current_index]
        
        if not messagebox.askyesno("Confirm Permanent Delete", 
                                   f"Permanently delete '{item.filename}'?\n\n"
                                   f"This action cannot be undone!"):
            return
        
        success, result = self.db.permanently_delete(item.id)
        
        if success:
            self.toast.show(f"Permanently deleted", emoji="💀")
            
            if item.id in self.media_by_id:
                del self.media_by_id[item.id]
            if item.path in self.media_by_path:
                del self.media_by_path[item.path]
            
            if self.current_index < len(self.media):
                self.media.pop(self.current_index)
            
            if self.current_index < len(self.media):
                self.open_media(self.media[self.current_index])
            elif self.media:
                self.current_index = len(self.media) - 1
                self.open_media(self.media[self.current_index])
            else:
                self.show_grid_view()
                self.refresh_grid()
        else:
            messagebox.showerror("Error", f"Failed to delete: {result}")

    def restore_current(self):
        """Restore current item from trash"""
        if not self.media or self.current_index >= len(self.media):
            return
        
        item = self.media[self.current_index]
        
        if not item.soft_delete:
            return
        
        success, result = self.db.restore_media(item.id)
        
        if success:
            self.toast.show(f"Restored to {os.path.dirname(result)}", emoji="↩️")
            item.soft_delete = False
            item.deleted_at = None
            item.path = result
            self.media_by_path[item.path] = item
            
            if self.current_index < len(self.media):
                self.media.pop(self.current_index)
            
            if self.current_index < len(self.media):
                self.open_media(self.media[self.current_index])
            elif self.media:
                self.current_index = len(self.media) - 1
                self.open_media(self.media[self.current_index])
            else:
                self.show_grid_view()
                self.refresh_grid()
        else:
            messagebox.showerror("Error", f"Failed to restore: {result}")

    def toggle_favorite_current(self):
        """Toggle favorite for current item"""
        if not self.media or self.current_index >= len(self.media):
            return
        
        item = self.media[self.current_index]
        new_state = self.db.toggle_favorite(item.id)
        if new_state is None:
            return
        
        item.favorite = bool(new_state)
        
        self.fav_btn.config(fg=self.colors['favorite'] if new_state else self.colors['text'])
        self.toast.show("Added to favorites 💗" if new_state else "Removed from favorites")
        
        self._refresh_thumbnail_by_item_id(item.id)
    def set_rating_current(self, rating):
        """Set rating for current item"""
        if not self.media or self.current_index >= len(self.media):
            return
        
        item = self.media[self.current_index]
        self.db.set_rating(item.id, rating)
        item.rating = rating
        
        stars = "★" * rating + "☆" * (5 - rating)
        self.toast.show(f"Rated {stars}")

    def copy_current_path(self):
        """Copy current item path to clipboard"""
        if not self.media or self.current_index >= len(self.media):
            return
        
        item = self.media[self.current_index]
        self.root.clipboard_clear()
        self.root.clipboard_append(item.path)
        self.toast.show("Path copied to clipboard", emoji="📋")

    def open_current_folder(self):
        """Open folder containing current item"""
        if not self.media or self.current_index >= len(self.media):
            return
        
        item = self.media[self.current_index]
        folder = item.folder
        
        if os.path.exists(folder):
            if self.is_windows:
                os.startfile(folder)
            else:
                import subprocess
                subprocess.call(['xdg-open', folder])

    def show_exif_info(self):
        """Show EXIF info for current image"""
        if not self.media or self.current_index >= len(self.media):
            return
        
        item = self.media[self.current_index]
        if item.is_video:
            return
        
        exif_data = self.exif_reader.read_exif(item.path)
        
        if not exif_data:
            messagebox.showinfo("EXIF Data", "No EXIF data available for this image.")
            return
        
        info_text = "📷 Image Information\n" + "=" * 30 + "\n\n"
        for key, value in exif_data.items():
            info_text += f"{key}: {value}\n"
        
        messagebox.showinfo("EXIF Data", info_text)

    def prev_media(self):
        """Navigate to previous media"""
        if self.current_index > 0:
            self.current_index -= 1
            self.open_media(self.media[self.current_index])

    def next_media(self):
        """Navigate to next media"""
        if self.current_index < len(self.media) - 1:
            self.current_index += 1
            self.open_media(self.media[self.current_index])

    def toggle_video_playback(self, event=None):
        """Toggle video play/pause"""
        if self.vlc_player:
            if self.vlc_player.is_playing():
                self.vlc_player.pause()
                self.play_btn.config(text="▶")
            else:
                self.vlc_player.play()
                self.play_btn.config(text="⏸")
                self.update_video_timeline()

    def update_video_timeline(self):
        """Update video timeline progress"""
        if self.video_timeline_after_id is not None:
            try:
                self.root.after_cancel(self.video_timeline_after_id)
            except Exception:
                pass
            self.video_timeline_after_id = None
        
        if self.vlc_player and self.vlc_player.is_playing():
            try:
                pos = self.vlc_player.get_position() * 100
                self.timeline.set(pos)
                
                length = self.vlc_player.get_length() / 1000
                current = self.vlc_player.get_time() / 1000
                if length > 0:
                    self.time_label.config(text=f"{int(current//60)}:{int(current%60):02d} / {int(length//60)}:{int(length%60):02d}")
            except:
                pass
            
            self.video_timeline_after_id = self.root.after(500, self.update_video_timeline)

    def seek_video(self, event):
        """Seek video to position"""
        if self.vlc_player:
            pos = self.timeline.get()
            self.vlc_player.set_position(pos / 100.0)

    def toggle_favorites(self):
        """Toggle favorites filter"""
        self.showing_favorites = not self.showing_favorites
        self.showing_deleted = False
        
        if self.showing_favorites:
            self.fav_filter_btn.config(bg=self.colors['favorite'])
        else:
            self.fav_filter_btn.config(bg=self.colors['surface'])
        
        self.clear_selection()
        self.apply_filters()

    def toggle_video_filter(self):
        """Toggle video filter"""
        self.showing_videos_only = not self.showing_videos_only
        self.showing_deleted = False
        
        if self.showing_videos_only:
            self.video_filter_btn.config(bg=self.colors['video'])
        else:
            self.video_filter_btn.config(bg=self.colors['surface'])
        
        self.clear_selection()
        self.apply_filters()

    def on_sort_change(self, value):
        """Handle sort change"""
        value = value.lower()
        if 'date' in value:
            self.sort_mode = SortMode.DATE
        elif 'name' in value:
            self.sort_mode = SortMode.NAME
        elif 'size' in value:
            self.sort_mode = SortMode.SIZE
        elif 'views' in value:
            self.sort_mode = SortMode.VIEWS
        elif 'rating' in value:
            self.sort_mode = SortMode.RATING
        elif 'random' in value:
            self.sort_mode = SortMode.RANDOM
        
        self.apply_filters()

    def on_search(self, event):
        """Handle search input"""
        self.filter_query = self.search_var.get().lower()
        if self.filter_query == "search your photos 💕":
            self.filter_query = ""
        self.apply_filters()

    def on_search_focus_in(self, event):
        """Search entry focus in"""
        if self.search_entry.get() == "Search your photos 💕":
            self.search_entry.delete(0, tk.END)
            self.search_entry.config(fg=self.colors['text'])

    def on_search_focus_out(self, event):
        """Search entry focus out"""
        if not self.search_entry.get():
            self.search_entry.insert(0, "Search your photos 💕")
            self.search_entry.config(fg=self.colors['text_secondary'])

    def update_status(self, text):
        """Update status bar text"""
        self.status_label.config(text=text)

    def update_stats(self):
        """Update statistics display"""
        try:
            stats = self.db.get_stats()
            stats_text = f"{stats['total']} photos ✨ {stats['videos']} videos 🎬 {stats['favorites']} favorites 💗"
            if stats['deleted'] > 0:
                stats_text += f" {stats['deleted']} in trash 🗑️"
            self.stats_label.config(text=stats_text)
        except Exception as e:
            logger.error(f"Stats update error: {e}")

    def add_folder_dialog(self):
        """Open folder dialog to add media"""
        folder = filedialog.askdirectory()
        if folder:
            self.scan_directory_background(folder)


def main():
    root = tk.Tk()
    app = LuminaGalleryProMax(root)
    root.mainloop()


if __name__ == "__main__":
    main()