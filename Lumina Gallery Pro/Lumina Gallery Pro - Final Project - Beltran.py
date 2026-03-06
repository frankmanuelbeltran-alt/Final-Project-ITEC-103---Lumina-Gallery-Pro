import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from PIL import Image, ImageTk, ImageOps
import os
from pathlib import Path
import time
import platform
import sqlite3
import hashlib
from datetime import datetime
from contextlib import contextmanager

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

class DatabaseManager:
    
    SCHEMA_VERSION = 2
    
    def __init__(self, db_path="gallery.db"):
        self.db_path = db_path
        self.init_database()
        self.migrate_if_needed()
        
    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
            
    def init_database(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT UNIQUE NOT NULL,
                    size INTEGER,
                    mtime REAL,
                    sha256 TEXT,
                    width INTEGER,
                    height INTEGER,
                    view_count INTEGER DEFAULT 0,
                    last_viewed TIMESTAMP,
                    favorite INTEGER DEFAULT 0,
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
                CREATE TABLE IF NOT EXISTS image_tags (
                    image_id INTEGER,
                    tag_id INTEGER,
                    PRIMARY KEY (image_id, tag_id),
                    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
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
                CREATE TABLE IF NOT EXISTS album_images (
                    album_id INTEGER,
                    image_id INTEGER,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (album_id, image_id),
                    FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE,
                    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            ''')
            
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_images_path ON images(path)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_images_sha256 ON images(sha256)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_images_favorite ON images(favorite)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_images_mtime ON images(mtime)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_images_view_count ON images(view_count)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_images_size ON images(size)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_images_width ON images(width)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_images_height ON images(height)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_image_tags_image_id ON image_tags(image_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_image_tags_tag_id ON image_tags(tag_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_album_images_album_id ON album_images(album_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_album_images_image_id ON album_images(image_id)')
            
    def migrate_if_needed(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT version FROM schema_version')
            row = cursor.fetchone()
            current_version = row['version'] if row else 0
            
            if current_version < 1:
                try:
                    cursor.execute('SELECT width FROM images LIMIT 1')
                except sqlite3.OperationalError:
                    cursor.execute('ALTER TABLE images ADD COLUMN width INTEGER')
                    cursor.execute('ALTER TABLE images ADD COLUMN height INTEGER')
                    cursor.execute('CREATE INDEX idx_images_width ON images(width)')
                    cursor.execute('CREATE INDEX idx_images_height ON images(height)')
                    
                cursor.execute('INSERT OR REPLACE INTO schema_version (version) VALUES (1)')
                
            if current_version < 2:
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_images_view_count ON images(view_count)')
                cursor.execute('INSERT OR REPLACE INTO schema_version (version) VALUES (2)')
                
    def get_or_create_image(self, path, size, mtime, sha256=None, width=None, height=None):
        path = os.path.abspath(path)
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT id, size, mtime, sha256, width, height FROM images WHERE path = ?', (path,))
            row = cursor.fetchone()
            
            if row:
                existing_id = row['id']
                existing_size = row['size']
                existing_mtime = row['mtime']
                existing_sha256 = row['sha256']
                
                if size == existing_size and mtime == existing_mtime:
                    if width and height and (row['width'] is None or row['height'] is None):
                        cursor.execute('UPDATE images SET width = ?, height = ? WHERE id = ?',
                                     (width, height, existing_id))
                    return existing_id, False
                
                new_sha256 = sha256 if sha256 else existing_sha256
                
                cursor.execute('''
                    UPDATE images 
                    SET size = ?, mtime = ?, sha256 = ?, width = ?, height = ?
                    WHERE id = ?
                ''', (size, mtime, new_sha256, width, height, existing_id))
                return existing_id, True
                
            else:
                cursor.execute('''
                    INSERT INTO images (path, size, mtime, sha256, width, height)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (path, size, mtime, sha256, width, height))
                return cursor.lastrowid, True
                
    def update_image_hash(self, image_id, sha256):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE images SET sha256 = ? WHERE id = ?', (sha256, image_id))
            
    def update_image_dimensions(self, image_id, width, height):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE images SET width = ?, height = ? WHERE id = ?',
                         (width, height, image_id))
            
    def update_view_stats(self, image_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE images 
                SET view_count = view_count + 1, 
                    last_viewed = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (image_id,))
            
    def toggle_favorite(self, image_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE images 
                SET favorite = CASE WHEN favorite = 1 THEN 0 ELSE 1 END
                WHERE id = ?
            ''', (image_id,))
            
            cursor.execute('SELECT favorite FROM images WHERE id = ?', (image_id,))
            row = cursor.fetchone()
            return row['favorite'] if row else 0
            
    def get_image_by_path(self, path):
        path = os.path.abspath(path)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM images WHERE path = ?', (path,))
            return cursor.fetchone()
            
    def get_image_by_id(self, image_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM images WHERE id = ?', (image_id,))
            return cursor.fetchone()
            
    def find_duplicates_by_hash(self, sha256):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, path FROM images WHERE sha256 = ? AND sha256 IS NOT NULL', (sha256,))
            return [(row['id'], row['path']) for row in cursor.fetchall()]
            
    def get_all_duplicate_groups(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT sha256, id, path 
                FROM images 
                WHERE sha256 IN (
                    SELECT sha256 FROM images 
                    WHERE sha256 IS NOT NULL 
                    GROUP BY sha256 HAVING COUNT(*) > 1
                )
                ORDER BY sha256
            ''')
            
            groups = {}
            for row in cursor.fetchall():
                h = row['sha256']
                if h not in groups:
                    groups[h] = []
                groups[h].append((row['id'], row['path']))
                
            return list(groups.items())
            
    def get_all_image_paths(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT path FROM images')
            return {row['path'] for row in cursor.fetchall()}
            
    def remove_image(self, path):
        path = os.path.abspath(path)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM images WHERE path = ?', (path,))
            
    def add_tag(self, image_id, tag_name):
        tag_name = tag_name.strip().lower()
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT id FROM tags WHERE name = ?', (tag_name,))
            row = cursor.fetchone()
            
            if row:
                tag_id = row['id']
            else:
                cursor.execute('INSERT INTO tags (name) VALUES (?)', (tag_name,))
                tag_id = cursor.lastrowid
                
            try:
                cursor.execute('INSERT INTO image_tags (image_id, tag_id) VALUES (?, ?)',
                             (image_id, tag_id))
            except sqlite3.IntegrityError:
                pass
                
    def remove_tag(self, image_id, tag_name):
        tag_name = tag_name.strip().lower()
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM image_tags 
                WHERE image_id = ? AND tag_id = (
                    SELECT id FROM tags WHERE name = ?
                )
            ''', (image_id, tag_name))
            
    def get_tags_for_image(self, image_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT t.name FROM tags t
                JOIN image_tags it ON t.id = it.tag_id
                WHERE it.image_id = ?
                ORDER BY t.name
            ''', (image_id,))
            return [row['name'] for row in cursor.fetchall()]
            
    def search_by_tag(self, tag_name):
        tag_name = tag_name.strip().lower()
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT i.path FROM images i
                JOIN image_tags it ON i.id = it.image_id
                JOIN tags t ON it.tag_id = t.id
                WHERE t.name = ?
            ''', (tag_name,))
            return [row['path'] for row in cursor.fetchall()]
            
    def get_all_tags(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT t.name, COUNT(it.image_id) as count 
                FROM tags t
                LEFT JOIN image_tags it ON t.id = it.tag_id
                GROUP BY t.id
                ORDER BY count DESC, t.name
            ''')
            return [(row['name'], row['count']) for row in cursor.fetchall()]
            
    def get_stats(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) as count FROM images')
            total = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(*) as count FROM images WHERE favorite = 1')
            favorites = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(DISTINCT sha256) as count FROM images WHERE sha256 IS NOT NULL')
            unique_hashes = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(*) as count FROM images WHERE sha256 IS NOT NULL')
            hashed = cursor.fetchone()['count']
            
            return {
                'total': total,
                'favorites': favorites,
                'duplicates': hashed - unique_hashes if hashed > 0 else 0,
                'hashed': hashed,
                'tags': cursor.execute('SELECT COUNT(*) FROM tags').fetchone()[0]
            }


class ProductionGalleryApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Lumina Gallery Pro - Database Edition")
        self.root.geometry("1400x900")
        self.root.minsize(1000, 700)
        
        self.is_windows = platform.system() == "Windows"
        self.db = DatabaseManager()
        
        self.all_images = []
        self.images = []
        self.image_metadata = {}
        self.thumbnail_cache = {}
        self.filter_query = ""
        
        self.current_index = 0
        self.view_mode = "grid"
        self.sort_mode = "name"
        self.slideshow_active = False
        self.slideshow_job = None
        self.current_rotation = 0
        self.zoomed = False
        self.current_image_original = None
        self.showing_favorites_only = False
        self.showing_duplicates_only = False
        
        self.thumbnail_size = (150, 150)
        self.columns = 3
        
        self.is_dark = True
        self.colors = self.get_dark_theme()
        self.theme_registry = []
        self._resize_job = None
        
        self.create_widgets()
        self.bind_events()
        self.root.after(100, self.load_initial_images)
        
    def get_dark_theme(self):
        return {
            'bg': '#0f0f0f', 'surface': '#1a1a1a', 'surface_hover': '#252525',
            'accent': '#6366f1', 'accent_hover': '#4f46e5',
            'text': '#f5f5f5', 'text_secondary': '#a3a3a3',
            'border': '#262626', 'danger': '#ef4444',
            'success': '#22c55e', 'favorite': '#f59e0b',
            'duplicate': '#ec4899'
        }
        
    def get_light_theme(self):
        return {
            'bg': '#fafafa', 'surface': '#ffffff', 'surface_hover': '#f0f0f0',
            'accent': '#6366f1', 'accent_hover': '#4f46e5',
            'text': '#171717', 'text_secondary': '#737373',
            'border': '#e5e5e5', 'danger': '#ef4444',
            'success': '#22c55e', 'favorite': '#f59e0b',
            'duplicate': '#ec4899'
        }
        
    def register_widget(self, widget, config_type, color_key=None, fg_attr=None):
        self.theme_registry.append((widget, config_type, color_key, fg_attr))
        
    def apply_theme(self):
        for widget, config_type, color_key, fg_attr in self.theme_registry:
            try:
                if config_type == 'bg':
                    widget.config(bg=self.colors[color_key])
                elif config_type == 'fg':
                    widget.config(fg=self.colors[fg_attr])
                elif config_type == 'both':
                    widget.config(bg=self.colors[color_key], fg=self.colors[fg_attr])
                elif config_type == 'entry':
                    widget.config(bg=self.colors[color_key], fg=self.colors[fg_attr],
                                 insertbackground=self.colors['text'])
                elif config_type == 'optionmenu':
                    widget.config(bg=self.colors[color_key], fg=self.colors[fg_attr], highlightthickness=0)
                    if hasattr(widget, 'menu'):
                        widget.menu.config(bg=self.colors[color_key], fg=self.colors[fg_attr])
                elif config_type == 'button':
                    widget.config(bg=self.colors['surface_hover'], fg=self.colors['text'])
                elif config_type == 'accent_button':
                    is_active = (self.view_mode == "grid" and widget == self.view_btn) or \
                               (self.view_mode == "single" and widget == self.back_btn)
                    widget.config(bg=self.colors['accent'] if is_active else self.colors['surface_hover'],
                                fg=self.colors['text'])
                elif config_type == 'danger_button':
                    widget.config(bg=self.colors['danger'], fg=self.colors['text'])
                elif config_type == 'favorite_button':
                    widget.config(bg=self.colors['favorite'], fg=self.colors['text'])
                elif config_type == 'duplicate_button':
                    widget.config(bg=self.colors['duplicate'], fg=self.colors['text'])
                elif config_type == 'canvas':
                    widget.config(bg=self.colors[color_key], highlightthickness=0)
            except tk.TclError:
                pass
        self.root.configure(bg=self.colors['bg'])
        
    def create_widgets(self):
        self.main_container = tk.Frame(self.root)
        self.main_container.pack(fill=tk.BOTH, expand=True)
        self.register_widget(self.main_container, 'bg', 'bg')
        
        self.create_header()
        self.create_content()
        self.create_status_bar()
        self.apply_theme()
        
    def create_header(self):
        self.header = tk.Frame(self.main_container, height=70)
        self.header.pack(fill=tk.X)
        self.header.pack_propagate(False)
        self.register_widget(self.header, 'bg', 'surface')
        
        title_frame = tk.Frame(self.header)
        title_frame.pack(side=tk.LEFT, padx=20, pady=15)
        self.register_widget(title_frame, 'bg', 'surface')
        
        logo = tk.Label(title_frame, text="✦", font=("Segoe UI", 20))
        logo.pack(side=tk.LEFT)
        self.register_widget(logo, 'both', 'surface', 'accent')
        
        title = tk.Label(title_frame, text="Lumina Pro", font=("Segoe UI", 18, "bold"))
        title.pack(side=tk.LEFT, padx=(5, 0))
        self.register_widget(title, 'both', 'surface', 'text')
        
        self.stats_label = tk.Label(title_frame, text="", font=("Segoe UI", 10))
        self.stats_label.pack(side=tk.LEFT, padx=(20, 0))
        self.register_widget(self.stats_label, 'both', 'surface', 'text_secondary')
        
        controls = tk.Frame(self.header)
        controls.pack(side=tk.RIGHT, padx=20, pady=10)
        self.register_widget(controls, 'bg', 'surface')
        
        self.dup_filter_btn = tk.Label(controls, text="⚡ Duplicates", font=("Segoe UI", 10),
                                      padx=15, pady=6, cursor="hand2")
        self.dup_filter_btn.bind('<Button-1>', lambda e: self.show_duplicates())
        self.register_widget(self.dup_filter_btn, 'duplicate_button')
        self.dup_filter_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.fav_filter_btn = tk.Label(controls, text="★ Favorites", font=("Segoe UI", 10),
                                      padx=15, pady=6, cursor="hand2")
        self.fav_filter_btn.bind('<Button-1>', lambda e: self.toggle_favorites_filter())
        self.register_widget(self.fav_filter_btn, 'button')
        self.fav_filter_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.sort_var = tk.StringVar(value="Sort: Name")
        self.sort_menu = tk.OptionMenu(controls, self.sort_var, 
                                      "Sort: Name", "Sort: Date", "Sort: Size", "Sort: Views", "Sort: Resolution",
                                      command=self.on_sort_change)
        self.sort_menu.config(font=("Segoe UI", 10), width=12)
        self.register_widget(self.sort_menu, 'optionmenu', 'surface_hover', 'text')
        self.sort_menu.pack(side=tk.LEFT, padx=(0, 10))
        
        self.theme_btn = tk.Label(controls, text="☀ Light", font=("Segoe UI", 10),
                                 padx=15, pady=6, cursor="hand2")
        self.theme_btn.bind('<Button-1>', lambda e: self.toggle_theme())
        self.register_widget(self.theme_btn, 'button')
        self.theme_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.slideshow_btn = tk.Label(controls, text="▶ Slideshow", font=("Segoe UI", 10),
                                     padx=15, pady=6, cursor="hand2")
        self.slideshow_btn.bind('<Button-1>', lambda e: self.toggle_slideshow())
        self.register_widget(self.slideshow_btn, 'button')
        self.slideshow_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.add_btn = tk.Label(controls, text="+ Add Folder", font=("Segoe UI", 10),
                               padx=15, pady=6, cursor="hand2")
        self.add_btn.bind('<Button-1>', lambda e: self.add_folder())
        self.register_widget(self.add_btn, 'button')
        self.add_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.view_btn = tk.Label(controls, text="⊞ Grid", font=("Segoe UI", 10),
                                padx=15, pady=6, cursor="hand2")
        self.view_btn.bind('<Button-1>', lambda e: self.toggle_view())
        self.register_widget(self.view_btn, 'accent_button')
        self.view_btn.pack(side=tk.LEFT)
        
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(self.header, textvariable=self.search_var,
                                    relief=tk.FLAT, font=("Segoe UI", 11), width=25)
        self.search_entry.pack(side=tk.RIGHT, padx=20, pady=15, ipady=8)
        self.search_entry.insert(0, "Search images...")
        self.register_widget(self.search_entry, 'entry', 'bg', 'text_secondary')
        
        self.search_entry.bind('<FocusIn>', self.on_search_focus_in)
        self.search_entry.bind('<FocusOut>', self.on_search_focus_out)
        self.search_entry.bind('<KeyRelease>', self.on_search)
        
    def create_content(self):
        self.content_frame = tk.Frame(self.main_container)
        self.content_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        self.register_widget(self.content_frame, 'bg', 'bg')
        
        self.create_grid_view()
        self.create_single_view()
        self.show_grid_view()
        
    def create_grid_view(self):
        self.grid_frame = tk.Frame(self.content_frame)
        self.register_widget(self.grid_frame, 'bg', 'bg')
        
        self.grid_canvas = tk.Canvas(self.grid_frame)
        self.grid_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.register_widget(self.grid_canvas, 'canvas', 'bg')
        
        self.scrollbar = ttk.Scrollbar(self.grid_frame, orient="vertical", command=self.on_scroll)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.grid_canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.thumbnails_frame = tk.Frame(self.grid_canvas)
        self.canvas_window = self.grid_canvas.create_window((0, 0), window=self.thumbnails_frame, anchor="nw")
        self.register_widget(self.thumbnails_frame, 'bg', 'bg')
        
        self.thumbnails_frame.bind("<Configure>", self.on_frame_configure)
        self.grid_canvas.bind("<Configure>", self.on_canvas_configure)
        
        if self.is_windows:
            self.grid_canvas.bind_all("<MouseWheel>", self.on_mousewheel)
        else:
            self.grid_canvas.bind_all("<Button-4>", lambda e: self.on_scroll_linux(-1))
            self.grid_canvas.bind_all("<Button-5>", lambda e: self.on_scroll_linux(1))
            
    def create_single_view(self):
        self.single_frame = tk.Frame(self.content_frame)
        self.register_widget(self.single_frame, 'bg', 'bg')
        
        self.toolbar = tk.Frame(self.single_frame, height=50)
        self.toolbar.pack(fill=tk.X, pady=(0, 10))
        self.toolbar.pack_propagate(False)
        self.register_widget(self.toolbar, 'bg', 'surface')
        
        nav = tk.Frame(self.toolbar)
        nav.pack(side=tk.LEFT, padx=10, pady=8)
        self.register_widget(nav, 'bg', 'surface')
        
        self.back_btn = tk.Label(nav, text="← Back", font=("Segoe UI", 10), padx=15, pady=6, cursor="hand2")
        self.back_btn.bind('<Button-1>', lambda e: self.show_grid_view())
        self.register_widget(self.back_btn, 'button')
        self.back_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.prev_btn = tk.Label(nav, text="◀ Prev", font=("Segoe UI", 10), padx=15, pady=6, cursor="hand2")
        self.prev_btn.bind('<Button-1>', lambda e: self.show_prev_image())
        self.register_widget(self.prev_btn, 'button')
        self.prev_btn.pack(side=tk.LEFT, padx=5)
        
        self.next_btn = tk.Label(nav, text="Next ▶", font=("Segoe UI", 10), padx=15, pady=6, cursor="hand2")
        self.next_btn.bind('<Button-1>', lambda e: self.show_next_image())
        self.register_widget(self.next_btn, 'button')
        self.next_btn.pack(side=tk.LEFT, padx=5)
        
        edit = tk.Frame(self.toolbar)
        edit.pack(side=tk.RIGHT, padx=10, pady=8)
        self.register_widget(edit, 'bg', 'surface')
        
        self.fav_btn = tk.Label(edit, text="☆ Favorite", font=("Segoe UI", 10), padx=15, pady=6, cursor="hand2")
        self.fav_btn.bind('<Button-1>', lambda e: self.toggle_current_favorite())
        self.register_widget(self.fav_btn, 'button')
        self.fav_btn.pack(side=tk.LEFT, padx=5)
        
        self.tags_btn = tk.Label(edit, text="🏷 Tags", font=("Segoe UI", 10), padx=15, pady=6, cursor="hand2")
        self.tags_btn.bind('<Button-1>', lambda e: self.edit_tags())
        self.register_widget(self.tags_btn, 'button')
        self.tags_btn.pack(side=tk.LEFT, padx=5)
        
        self.rotate_btn = tk.Label(edit, text="↻ Rotate", font=("Segoe UI", 10), padx=15, pady=6, cursor="hand2")
        self.rotate_btn.bind('<Button-1>', lambda e: self.rotate_image())
        self.register_widget(self.rotate_btn, 'button')
        self.rotate_btn.pack(side=tk.LEFT, padx=5)
        
        self.delete_btn = tk.Label(edit, text="🗑 Delete", font=("Segoe UI", 10), padx=15, pady=6, cursor="hand2")
        self.delete_btn.bind('<Button-1>', lambda e: self.delete_current_image())
        self.register_widget(self.delete_btn, 'danger_button')
        self.delete_btn.pack(side=tk.LEFT, padx=5)
        
        self.image_container = tk.Frame(self.single_frame)
        self.image_container.pack(fill=tk.BOTH, expand=True)
        self.register_widget(self.image_container, 'bg', 'surface')
        
        self.image_label = tk.Label(self.image_container, cursor="hand2")
        self.image_label.pack(expand=True)
        self.register_widget(self.image_label, 'bg', 'surface')
        self.image_label.bind('<Button-1>', lambda e: self.toggle_zoom())
        
        self.info_frame = tk.Frame(self.single_frame)
        self.info_frame.pack(fill=tk.X, pady=(10, 0))
        self.register_widget(self.info_frame, 'bg', 'bg')
        
        self.filename_label = tk.Label(self.info_frame, text="", font=("Segoe UI", 14, "bold"))
        self.filename_label.pack(anchor=tk.W)
        self.register_widget(self.filename_label, 'both', 'bg', 'text')
        
        self.details_label = tk.Label(self.info_frame, text="", font=("Segoe UI", 10))
        self.details_label.pack(anchor=tk.W, pady=(5, 0))
        self.register_widget(self.details_label, 'both', 'bg', 'text_secondary')
        
        self.tags_display = tk.Label(self.info_frame, text="", font=("Segoe UI", 10, "italic"))
        self.tags_display.pack(anchor=tk.W, pady=(5, 0))
        self.register_widget(self.tags_display, 'both', 'bg', 'accent')
        
    def create_status_bar(self):
        self.status_bar = tk.Frame(self.main_container, height=30)
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_bar.pack_propagate(False)
        self.register_widget(self.status_bar, 'bg', 'surface')
        
        self.status_label = tk.Label(self.status_bar, text="Ready", font=("Segoe UI", 9))
        self.status_label.pack(side=tk.LEFT, padx=20, pady=5)
        self.register_widget(self.status_label, 'both', 'surface', 'text_secondary')
        
        self.db_label = tk.Label(self.status_bar, text="DB: Ready", font=("Segoe UI", 9))
        self.db_label.pack(side=tk.LEFT, padx=20, pady=5)
        self.register_widget(self.db_label, 'both', 'surface', 'text_secondary')
        
        self.mem_label = tk.Label(self.status_bar, text="", font=("Segoe UI", 9))
        self.mem_label.pack(side=tk.RIGHT, padx=20, pady=5)
        self.register_widget(self.mem_label, 'both', 'surface', 'accent')
        
        self.count_label = tk.Label(self.status_bar, text="0 images", font=("Segoe UI", 9))
        self.count_label.pack(side=tk.RIGHT, padx=20, pady=5)
        self.register_widget(self.count_label, 'both', 'surface', 'text_secondary')
        
    def compute_sha256(self, filepath):
        try:
            sha256_hash = hashlib.sha256()
            with open(filepath, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except Exception:
            return None
            
    def load_initial_images(self):
        home = Path.home()
        picture_dirs = [home / "Pictures", home / "Downloads", home / "Desktop"]
        
        for dir_path in picture_dirs:
            if dir_path.exists():
                self.load_from_directory(dir_path, silent=True)
                break
                
        if not self.all_images:
            self.show_empty_state()
            
    def load_from_directory(self, directory, silent=False):
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
        
        new_images = []
        path_obj = Path(directory)
        
        for ext in image_extensions:
            new_images.extend(str(p) for p in path_obj.rglob(f"*{ext}"))
            new_images.extend(str(p) for p in path_obj.rglob(f"*{ext.upper()}"))
            
        new_images = list(dict.fromkeys(new_images))
        
        if new_images:
            added_count = 0
            updated_count = 0
            unchanged_count = 0
            
            for img_path in new_images:
                try:
                    stat = os.stat(img_path)
                    size = stat.st_size
                    mtime = stat.st_mtime
                    
                    image_id, was_modified = self.db.get_or_create_image(img_path, size, mtime)
                    
                    if was_modified:
                        pass
                        
                    self.image_metadata[img_path] = {
                        'id': image_id,
                        'size': size,
                        'mtime': mtime,
                        'path': img_path
                    }
                    
                    existing = self.db.get_image_by_id(image_id)
                    if existing and existing['created_at']:
                        if was_modified and existing['view_count'] == 0:
                            added_count += 1
                        elif was_modified:
                            updated_count += 1
                        else:
                            unchanged_count += 1
                    else:
                        added_count += 1
                        
                except Exception as e:
                    print(f"Error processing {img_path}: {e}")
                    
            self.all_images = list(dict.fromkeys(self.all_images + new_images))
            
            self.apply_filter_and_sort()
            self.update_db_status(f"DB: +{added_count} new, {updated_count} updated, {unchanged_count} unchanged")
            self.update_stats()
        elif not silent:
            messagebox.showinfo("No Images", "No image files found.")
            
    def apply_filter_and_sort(self):
        filtered = self.all_images.copy()
        
        if self.showing_favorites_only:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT path FROM images WHERE favorite = 1')
                fav_paths = {row['path'] for row in cursor.fetchall()}
                filtered = [p for p in filtered if p in fav_paths]
                
        if self.showing_duplicates_only:
            dup_groups = self.db.get_all_duplicate_groups()
            dup_paths = set()
            for hash_val, items in dup_groups:
                for img_id, path in items:
                    dup_paths.add(path)
            filtered = [p for p in filtered if p in dup_paths]
                
        if self.filter_query:
            filtered = [img for img in filtered 
                       if self.filter_query in os.path.basename(img).lower()]
                       
        if self.sort_mode == "name":
            filtered.sort(key=lambda x: os.path.basename(x).lower())
        elif self.sort_mode == "date":
            def get_mtime(path):
                meta = self.image_metadata.get(path)
                return meta.get('mtime', 0) if meta else 0
            filtered.sort(key=get_mtime, reverse=True)
        elif self.sort_mode == "size":
            def get_size(path):
                meta = self.image_metadata.get(path)
                return meta.get('size', 0) if meta else 0
            filtered.sort(key=get_size, reverse=True)
        elif self.sort_mode == "views":
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT path, view_count FROM images')
                view_counts = {row['path']: row['view_count'] for row in cursor.fetchall()}
                filtered.sort(key=lambda x: view_counts.get(x, 0), reverse=True)
        elif self.sort_mode == "resolution":
            def get_resolution(path):
                meta = self.image_metadata.get(path)
                if meta and 'width' in meta and meta['width']:
                    return meta['width'] * meta['height']
                return 0
            filtered.sort(key=get_resolution, reverse=True)
                
        self.images = filtered
        self.thumbnail_cache.clear()
        self.refresh_gallery()
        
    def on_sort_change(self, value):
        mode = value.replace("Sort: ", "").lower()
        if mode != self.sort_mode:
            self.sort_mode = mode
            self.apply_filter_and_sort()
            
    def toggle_favorites_filter(self):
        self.showing_favorites_only = not self.showing_favorites_only
        self.showing_duplicates_only = False
        
        if self.showing_favorites_only:
            self.fav_filter_btn.config(text="★ Favorites Only", bg=self.colors['favorite'])
            self.dup_filter_btn.config(text="⚡ Duplicates")
        else:
            self.fav_filter_btn.config(text="☆ All Images")
            self.apply_theme()
            
        self.apply_filter_and_sort()
        
    def show_duplicates(self):
        self.showing_duplicates_only = not self.showing_duplicates_only
        self.showing_favorites_only = False
        
        if self.showing_duplicates_only:
            self.dup_filter_btn.config(text="⚡ Duplicates Only", bg=self.colors['duplicate'])
            self.fav_filter_btn.config(text="★ Favorites")
            
            groups = self.db.get_all_duplicate_groups()
            total_dups = sum(len(items) for hash_val, items in groups)
            self.update_status(f"Found {len(groups)} duplicate groups ({total_dups} images)")
        else:
            self.dup_filter_btn.config(text="⚡ Duplicates")
            self.apply_theme()
            
        self.apply_filter_and_sort()
        
    def toggle_current_favorite(self):
        if not self.images or self.current_index >= len(self.images):
            return
            
        current_path = self.images[self.current_index]
        meta = self.image_metadata.get(current_path)
        
        if not meta or 'id' not in meta:
            try:
                stat = os.stat(current_path)
                img_id, _ = self.db.get_or_create_image(current_path, stat.st_size, stat.st_mtime)
                meta = {'id': img_id, 'path': current_path, 'size': stat.st_size, 'mtime': stat.st_mtime}
                self.image_metadata[current_path] = meta
            except Exception as e:
                messagebox.showerror("Error", f"Could not access database: {e}")
                return
                
        new_state = self.db.toggle_favorite(meta['id'])
        
        if new_state:
            self.fav_btn.config(text="★ Favorited", bg=self.colors['favorite'])
            self.update_status("Added to favorites")
        else:
            self.fav_btn.config(text="☆ Favorite")
            self.apply_theme()
            self.update_status("Removed from favorites")
            
        self.update_stats()
        
    def edit_tags(self):
        if not self.images or self.current_index >= len(self.images):
            return
            
        current_path = self.images[self.current_index]
        meta = self.image_metadata.get(current_path)
        
        if not meta or 'id' not in meta:
            return
            
        existing_tags = self.db.get_tags_for_image(meta['id'])
        tag_str = ", ".join(existing_tags)
        
        new_tags = simpledialog.askstring("Edit Tags", "Enter tags (comma separated):", 
                                          initialvalue=tag_str)
        if new_tags is not None:
            for tag in [t.strip() for t in new_tags.split(",") if t.strip()]:
                self.db.add_tag(meta['id'], tag)
            self.update_tags_display()
            
    def update_tags_display(self):
        if not self.images or self.current_index >= len(self.images):
            return
            
        current_path = self.images[self.current_index]
        meta = self.image_metadata.get(current_path)
        
        if meta and 'id' in meta:
            tags = self.db.get_tags_for_image(meta['id'])
            if tags:
                self.tags_display.config(text=f"Tags: {', '.join(tags)}")
            else:
                self.tags_display.config(text="")
                
    def update_stats(self):
        stats = self.db.get_stats()
        dup_text = f" | ⚡ {stats['duplicates']} dups" if stats['duplicates'] > 0 else ""
        self.stats_label.config(text=f"★ {stats['favorites']}  |  🏷 {stats['tags']}  |  👁 {stats['total']}{dup_text}")
        
    def update_db_status(self, text):
        self.db_label.config(text=text)
        self.root.after(5000, lambda: self.db_label.config(text="DB: Ready"))
        
    def on_search(self, event):
        query = self.search_var.get().lower()
        if query == "search images...":
            query = ""
            
        self.filter_query = query
        self.apply_filter_and_sort()
        
        if query:
            self.update_status(f"Found {len(self.images)} matches")
            
    def refresh_gallery(self):
        for widget in self.thumbnails_frame.winfo_children():
            widget.destroy()
            
        if not self.images:
            self.show_empty_state()
            return
            
        width = self.content_frame.winfo_width()
        if width < 300:
            self.root.after(100, self.refresh_gallery)
            return
            
        self.columns = max(3, width // 170)
        total_rows = (len(self.images) + self.columns - 1) // self.columns
        self.total_rows = total_rows
        
        self.thumbnail_widgets = []
        for idx in range(len(self.images)):
            row = idx // self.columns
            col = idx % self.columns
            
            placeholder = tk.Frame(self.thumbnails_frame, width=150, height=150)
            placeholder.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
            placeholder.grid_propagate(False)
            self.register_widget(placeholder, 'bg', 'surface')
            
            loading = tk.Label(placeholder, text="⏳", font=("Segoe UI", 24))
            loading.place(relx=0.5, rely=0.5, anchor="center")
            self.register_widget(loading, 'both', 'surface', 'text_secondary')
            
            self.thumbnail_widgets.append(placeholder)
            
        self.thumbnails_frame.update_idletasks()
        self.grid_canvas.configure(scrollregion=(0, 0, width, total_rows * 170))
        
        self.update_visible_range()
        
    def update_visible_range(self):
        if not self.images:
            return
            
        y1 = self.grid_canvas.canvasy(0)
        y2 = y1 + self.content_frame.winfo_height()
        
        row_start = max(0, int(y1 // 170) - 1)
        row_end = min(self.total_rows, int(y2 // 170) + 2)
        
        idx_start = row_start * self.columns
        idx_end = min(len(self.images), (row_end + 1) * self.columns)
        
        for idx in range(idx_start, idx_end):
            if idx not in self.thumbnail_cache and idx < len(self.thumbnail_widgets):
                self.load_thumbnail(idx)
                
        if len(self.thumbnail_cache) > 50:
            to_remove = [k for k in self.thumbnail_cache.keys() 
                        if k < idx_start - 10 or k > idx_end + 10]
            for ri in to_remove[:10]:
                del self.thumbnail_cache[ri]
                
        self.update_memory_display()
        
    def load_thumbnail(self, idx):
        if idx >= len(self.images) or idx in self.thumbnail_cache:
            return
            
        img_path = self.images[idx]
        widget = self.thumbnail_widgets[idx]
        
        try:
            for child in widget.winfo_children():
                child.destroy()
                
            img = Image.open(img_path)
            img = ImageOps.exif_transpose(img)
            img.thumbnail(self.thumbnail_size, Image.Resampling.LANCZOS)
            
            meta = self.image_metadata.get(img_path, {})
            if 'id' in meta and ('width' not in meta or not meta['width']):
                full_img = Image.open(img_path)
                full_img = ImageOps.exif_transpose(full_img)
                width, height = full_img.size
                self.db.update_image_dimensions(meta['id'], width, height)
                meta['width'] = width
                meta['height'] = height
                
            photo = ImageTk.PhotoImage(img)
            self.thumbnail_cache[idx] = photo
            
            img_label = tk.Label(widget, image=photo)
            img_label.place(relx=0.5, rely=0.4, anchor="center")
            self.register_widget(img_label, 'bg', 'surface')
            
            meta = self.image_metadata.get(img_path, {})
            is_fav = False
            is_dup = False
            
            if 'id' in meta:
                row = self.db.get_image_by_id(meta['id'])
                if row:
                    is_fav = row['favorite']
                    if row['sha256']:
                        dups = self.db.find_duplicates_by_hash(row['sha256'])
                        is_dup = len(dups) > 1
                    
            if is_fav:
                fav_indicator = tk.Label(widget, text="★", font=("Segoe UI", 12, "bold"))
                fav_indicator.place(relx=0.9, rely=0.1, anchor="center")
                self.register_widget(fav_indicator, 'both', 'surface', 'favorite')
                
            if is_dup:
                dup_indicator = tk.Label(widget, text="⚡", font=("Segoe UI", 10))
                dup_indicator.place(relx=0.1, rely=0.1, anchor="center")
                self.register_widget(dup_indicator, 'both', 'surface', 'duplicate')
            
            filename = os.path.basename(img_path)
            name = filename[:15] + "..." if len(filename) > 15 else filename
            text_label = tk.Label(widget, text=name, font=("Segoe UI", 8))
            text_label.place(relx=0.5, rely=0.85, anchor="center")
            self.register_widget(text_label, 'both', 'surface', 'text_secondary')
            
            widget.bind('<Button-1>', lambda e, i=idx: self.open_image(i))
            img_label.bind('<Button-1>', lambda e, i=idx: self.open_image(i))
            
            def on_enter(e, w=widget):
                w.config(bg=self.colors['accent'])
                for child in w.winfo_children():
                    child.config(bg=self.colors['accent'])
            def on_leave(e, w=widget):
                w.config(bg=self.colors['surface'])
                for child in w.winfo_children():
                    child.config(bg=self.colors['surface'])
                    
            widget.bind('<Enter>', on_enter)
            widget.bind('<Leave>', on_leave)
            
        except Exception as e:
            for child in widget.winfo_children():
                child.destroy()
            err = tk.Label(widget, text="✖", font=("Segoe UI", 20))
            err.place(relx=0.5, rely=0.5, anchor="center")
            self.register_widget(err, 'both', 'surface', 'danger')
            
    def open_image(self, index):
        self.current_index = index
        self.current_rotation = 0
        self.show_single_view()
        self.display_current_image()
        
        current_path = self.images[index]
        meta = self.image_metadata.get(current_path)
        if meta and 'id' in meta:
            self.db.update_view_stats(meta['id'])
            
    def display_current_image(self):
        if not self.images or self.current_index >= len(self.images):
            return
            
        img_path = self.images[self.current_index]
        
        try:
            self.current_image_original = Image.open(img_path)
            self.current_image_original = ImageOps.exif_transpose(self.current_image_original)
            
            self.fit_image_to_window()
            
            meta = self.image_metadata.get(img_path, {})
            view_count = 0
            is_favorite = False
            last_viewed = None
            is_duplicate = False
            
            if 'id' in meta:
                row = self.db.get_image_by_id(meta['id'])
                if row:
                    view_count = row['view_count']
                    is_favorite = row['favorite']
                    last_viewed = row['last_viewed']
                    if row['sha256']:
                        dups = self.db.find_duplicates_by_hash(row['sha256'])
                        is_duplicate = len(dups) > 1
                    
            if is_favorite:
                self.fav_btn.config(text="★ Favorited")
                self.register_widget(self.fav_btn, 'favorite_button')
            else:
                self.fav_btn.config(text="☆ Favorite")
                self.register_widget(self.fav_btn, 'button')
            self.apply_theme()
            
            self.filename_label.config(text=os.path.basename(img_path))
            size_mb = os.path.getsize(img_path) / (1024 * 1024)
            mod_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(img_path)))
            
            view_info = f" • 👁 {view_count}" if view_count > 0 else ""
            fav_info = " • ★" if is_favorite else ""
            dup_info = " • ⚡ DUPLICATE" if is_duplicate else ""
            
            self.details_label.config(
                text=f"{self.current_image_original.size[0]} × {self.current_image_original.size[1]} px • "
                     f"{size_mb:.2f} MB • {mod_time}{view_info}{fav_info}{dup_info}"
            )
            
            self.update_tags_display()
            self.zoomed = False
            
            if self.slideshow_active:
                self.schedule_slideshow()
                
        except Exception as e:
            messagebox.showerror("Error", f"Could not load image: {e}")
            
    def fit_image_to_window(self):
        if not self.current_image_original:
            return
            
        img = self.current_image_original.rotate(self.current_rotation, expand=True)
        
        container_width = max(1, self.image_container.winfo_width() - 40)
        container_height = max(1, self.image_container.winfo_height() - 40)
        
        img_width, img_height = img.size
        scale = min(container_width / img_width, container_height / img_height, 1.0)
        
        new_size = (int(img_width * scale), int(img_height * scale))
        resized = img.resize(new_size, Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(resized)
        
        self.image_label.config(image=photo)
        self.image_label.image = photo
        
    def toggle_zoom(self):
        if not self.current_image_original:
            return
            
        self.zoomed = not self.zoomed
        
        if self.zoomed:
            img = self.current_image_original.rotate(self.current_rotation, expand=True)
            max_size = 2000
            if img.width > max_size or img.height > max_size:
                img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.image_label.config(image=photo)
            self.image_label.image = photo
        else:
            self.fit_image_to_window()
            
    def rotate_image(self):
        if self.current_image_original:
            self.current_rotation = (self.current_rotation + 90) % 360
            self.fit_image_to_window()
            
    def delete_current_image(self):
        if not self.images or self.view_mode != "single":
            return
            
        img_path = self.images[self.current_index]
        filename = os.path.basename(img_path)
        
        if not messagebox.askyesno("Delete File", f"Delete '{filename}'?\n\nFile will be moved to trash.", icon='warning'):
            return
        if not messagebox.askyesno("Confirm", f"Are you sure?\n\n{img_path}", icon='warning'):
            return
            
        try:
            try:
                import send2trash
                send2trash.send2trash(img_path)
            except ImportError:
                os.remove(img_path)
                
            self.db.remove_image(img_path)
            
            self.all_images.remove(img_path)
            if img_path in self.image_metadata:
                del self.image_metadata[img_path]
                
            self.thumbnail_cache.clear()
            self.apply_filter_and_sort()
            
            if self.current_index >= len(self.images):
                self.current_index = max(0, len(self.images) - 1)
                
            if self.images:
                self.display_current_image()
            else:
                self.show_grid_view()
                self.show_empty_state()
                
            self.update_count()
            self.update_stats()
            self.update_status("File deleted")
            
        except Exception as e:
            messagebox.showerror("Error", f"Could not delete: {e}")
            
    def toggle_slideshow(self):
        self.slideshow_active = not self.slideshow_active
        
        if self.slideshow_active:
            if self.view_mode != "single" and self.images:
                self.open_image(0)
            self.slideshow_btn.config(text="⏹ Stop")
        else:
            self.slideshow_btn.config(text="▶ Slideshow")
            if self.slideshow_job:
                self.root.after_cancel(self.slideshow_job)
                
        self.apply_theme()
        
    def schedule_slideshow(self):
        if self.slideshow_active and self.view_mode == "single":
            self.slideshow_job = self.root.after(3000, self.slideshow_next)
            
    def slideshow_next(self):
        if not self.slideshow_active:
            return
            
        if self.current_index < len(self.images) - 1:
            self.current_index += 1
            self.display_current_image()
        else:
            self.current_index = 0
            self.display_current_image()
            
    def toggle_theme(self):
        self.is_dark = not self.is_dark
        self.colors = self.get_dark_theme() if self.is_dark else self.get_light_theme()
        
        self.theme_btn.config(text="🌙 Dark" if not self.is_dark else "☀ Light")
        self.apply_theme()
        
    def show_prev_image(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.current_rotation = 0
            self.display_current_image()
            
    def show_next_image(self):
        if self.current_index < len(self.images) - 1:
            self.current_index += 1
            self.current_rotation = 0
            self.display_current_image()
            
    def show_grid_view(self):
        self.view_mode = "grid"
        self.single_frame.pack_forget()
        self.grid_frame.pack(fill=tk.BOTH, expand=True)
        self.view_btn.config(text="⊞ Grid")
        
        if self.slideshow_active:
            self.toggle_slideshow()
            
        self.apply_theme()
        self.refresh_gallery()
        
    def show_single_view(self):
        self.view_mode = "single"
        self.grid_frame.pack_forget()
        self.single_frame.pack(fill=tk.BOTH, expand=True)
        self.view_btn.config(text="▣ Single")
        
        self.apply_theme()
        
    def toggle_view(self):
        if self.view_mode == "grid":
            if self.images:
                self.open_image(self.current_index)
        else:
            self.show_grid_view()
            
    def add_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.load_from_directory(folder)
            
    def on_search_focus_in(self, event):
        if self.search_entry.get() == "Search images...":
            self.search_entry.delete(0, tk.END)
            self.search_entry.config(fg=self.colors['text'])
            
    def on_search_focus_out(self, event):
        if not self.search_entry.get():
            self.search_entry.insert(0, "Search images...")
            self.search_entry.config(fg=self.colors['text_secondary'])
            
    def show_empty_state(self):
        for widget in self.thumbnails_frame.winfo_children():
            widget.destroy()
            
        empty = tk.Frame(self.thumbnails_frame)
        empty.pack(expand=True, pady=100)
        self.register_widget(empty, 'bg', 'bg')
        
        tk.Label(empty, text="📷", font=("Segoe UI", 64), 
                bg=self.colors['bg'], fg=self.colors['surface_hover']).pack()
        
        tk.Label(empty, text="No images found", font=("Segoe UI", 18, "bold"),
                bg=self.colors['bg'], fg=self.colors['text']).pack(pady=(20, 10))
        
    def update_status(self, text):
        self.status_label.config(text=text)
        self.root.after(3000, lambda: self.status_label.config(text="Ready"))
        
    def update_count(self):
        showing = len(self.images)
        total = len(self.all_images)
        if showing != total:
            self.count_label.config(text=f"{showing}/{total} images")
        else:
            self.count_label.config(text=f"{total} images")
            
    def update_memory_display(self):
        if HAS_PSUTIL:
            try:
                process = psutil.Process(os.getpid())
                mem_mb = process.memory_info().rss / 1024 / 1024
                self.mem_label.config(text=f"{mem_mb:.1f} MB")
            except:
                self.mem_label.config(text=f"Cache: {len(self.thumbnail_cache)}")
        else:
            self.mem_label.config(text=f"Cache: {len(self.thumbnail_cache)}")
            
    def on_scroll(self, *args):
        self.grid_canvas.yview(*args)
        self.update_visible_range()
        
    def on_mousewheel(self, event):
        self.grid_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.update_visible_range()
        
    def on_scroll_linux(self, direction):
        self.grid_canvas.yview_scroll(direction, "units")
        self.update_visible_range()
        
    def on_frame_configure(self, event=None):
        self.grid_canvas.configure(scrollregion=self.grid_canvas.bbox("all"))
        
    def on_canvas_configure(self, event):
        self.grid_canvas.itemconfig(self.canvas_window, width=event.width)
        
    def bind_events(self):
        self.root.bind('<Left>', lambda e: self.show_prev_image() if self.view_mode == "single" else None)
        self.root.bind('<Right>', lambda e: self.show_next_image() if self.view_mode == "single" else None)
        self.root.bind('<r>', lambda e: self.rotate_image() if self.view_mode == "single" else None)
        self.root.bind('<f>', lambda e: self.toggle_current_favorite() if self.view_mode == "single" else None)
        self.root.bind('<space>', lambda e: self.toggle_slideshow())
        self.root.bind('<Escape>', lambda e: self.handle_escape())
        self.root.bind('<Delete>', lambda e: self.delete_current_image() if self.view_mode == "single" else None)
        self.root.bind('<F11>', lambda e: self.toggle_fullscreen())
        self.root.bind('<Configure>', lambda e: self.on_resize())
        
    def handle_escape(self):
        if self.slideshow_active:
            self.toggle_slideshow()
        elif self.view_mode == "single":
            self.show_grid_view()
            
    def toggle_fullscreen(self):
        self.root.attributes('-fullscreen', not self.root.attributes('-fullscreen'))
        
    def on_resize(self):
        if self.view_mode == "single" and not self.zoomed:
            if self._resize_job is not None:
                try:
                    self.root.after_cancel(self._resize_job)
                except ValueError:
                    pass
                self._resize_job = None
            self._resize_job = self.root.after(200, self.fit_image_to_window)

def main():
    root = tk.Tk()
    app = ProductionGalleryApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
