import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from PIL import Image, ImageTk, ImageOps
import os
from pathlib import Path
import time
import platform
import sqlite3
import hashlib
import json
import threading
import queue
from datetime import datetime
from contextlib import contextmanager
import cv2
import imagehash

# Optional imports with graceful fallbacks
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import vlc
    HAS_VLC = True
except ImportError:
    HAS_VLC = False

# Thread-safe Tkinter update queue
class TkQueue:
    """Thread-safe queue for UI updates from worker threads"""
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.check_queue()
        
    def check_queue(self):
        try:
            while True:
                func = self.queue.get_nowait()
                func()
        except queue.Empty:
            pass
        self.root.after(50, self.check_queue)
        
    def put(self, func):
        self.queue.put(func)

class BackgroundWorker:
    """Manages background tasks without blocking UI"""
    def __init__(self, tk_queue):
        self.tk_queue = tk_queue
        self.task_queue = queue.Queue()
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        self.active_tasks = {}
        
    def _worker_loop(self):
        while True:
            task_id, func, callback = self.task_queue.get()
            if task_id is None:
                break
                
            self.active_tasks[task_id] = True
            
            try:
                result = func()
                if callback and task_id in self.active_tasks:
                    self.tk_queue.put(lambda: callback(result))
            except Exception as e:
                print(f"Worker error: {e}")
                
            if task_id in self.active_tasks:
                del self.active_tasks[task_id]
                
    def submit(self, task_id, func, callback=None):
        """Submit task to background worker"""
        self.task_queue.put((task_id, func, callback))
        
    def cancel(self, task_id):
        """Cancel pending task"""
        if task_id in self.active_tasks:
            del self.active_tasks[task_id]

class ThumbnailCache:
    """Persistent disk cache for thumbnails"""
    def __init__(self, cache_dir=".cache/thumbnails"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ram_cache = {}
        self.max_ram = 100
        
    def _get_cache_path(self, content_hash):
        return self.cache_dir / f"{content_hash}.jpg"
        
    def get(self, content_hash):
        if content_hash in self.ram_cache:
            return self.ram_cache[content_hash]
            
        cache_path = self._get_cache_path(content_hash)
        if cache_path.exists():
            try:
                img = Image.open(cache_path)
                self._add_to_ram(content_hash, img)
                return img
            except Exception:
                return None
        return None
        
    def put(self, content_hash, pil_image):
        cache_path = self._get_cache_path(content_hash)
        try:
            pil_image.save(cache_path, "JPEG", quality=85)
        except Exception as e:
            print(f"Cache save error: {e}")
        self._add_to_ram(content_hash, pil_image.copy())
        
    def _add_to_ram(self, content_hash, pil_image):
        if len(self.ram_cache) >= self.max_ram:
            oldest = next(iter(self.ram_cache))
            del self.ram_cache[oldest]
        self.ram_cache[content_hash] = pil_image
        
    def compute_content_hash(self, file_path, file_stat):
        hasher = hashlib.sha256()
        hasher.update(file_path.encode())
        hasher.update(str(file_stat.st_mtime).encode())
        hasher.update(str(file_stat.st_size).encode())
        return hasher.hexdigest()[:32]

class DatabaseManager:
    """Production SQLite with media support and perceptual hashing"""
    SCHEMA_VERSION = 3
    
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
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT UNIQUE NOT NULL,
                    media_type TEXT CHECK(media_type IN ('image', 'video')),
                    size INTEGER,
                    mtime REAL,
                    sha256 TEXT,
                    phash TEXT,
                    width INTEGER,
                    height INTEGER,
                    duration INTEGER,
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
            
            self._create_indexes(cursor)
            
    def _create_indexes(self, cursor):
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_path ON media(path)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_sha256 ON media(sha256)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_phash ON media(phash)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_favorite ON media(favorite)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_mtime ON media(mtime)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_view_count ON media(view_count)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_size ON media(size)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_type ON media(media_type)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_width ON media(width)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_height ON media(height)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_tags_media_id ON media_tags(media_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_tags_tag_id ON media_tags(tag_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_album_media_album_id ON album_media(album_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_album_media_media_id ON album_media(media_id)')
        
    def migrate_if_needed(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT version FROM schema_version')
            row = cursor.fetchone()
            current = row['version'] if row else 0
            
            if current < 1:
                try:
                    cursor.execute('ALTER TABLE media ADD COLUMN media_type TEXT DEFAULT "image"')
                    cursor.execute('ALTER TABLE media ADD COLUMN phash TEXT')
                    cursor.execute('ALTER TABLE media ADD COLUMN duration INTEGER')
                except:
                    pass
                cursor.execute('INSERT OR REPLACE INTO schema_version (version) VALUES (1)')
                
            if current < 2:
                try:
                    cursor.execute('ALTER TABLE images RENAME TO media')
                except:
                    pass
                cursor.execute('INSERT OR REPLACE INTO schema_version (version) VALUES (2)')
                
            if current < 3:
                self._create_indexes(cursor)
                cursor.execute('INSERT OR REPLACE INTO schema_version (version) VALUES (3)')
                
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
                        width = ?, height = ?, duration = ?, phash = ?
                    WHERE id = ?
                ''', (media_type, size, mtime, sha256, width, height, duration, phash, existing_id))
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
                
    def find_similar_images(self, phash, max_distance=10):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, path, phash FROM media WHERE phash IS NOT NULL AND media_type = "image"')
            
            similar = []
            target_hash = imagehash.hex_to_hash(phash)
            
            for row in cursor.fetchall():
                if row['phash']:
                    try:
                        other_hash = imagehash.hex_to_hash(row['phash'])
                        distance = target_hash - other_hash
                        if distance <= max_distance:
                            similar.append((row['id'], row['path'], distance))
                    except:
                        continue
                        
            return sorted(similar, key=lambda x: x[2])

class CoquetteGalleryApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Lumina Gallery 💗")
        self.root.geometry("1600x1000")
        self.root.minsize(1200, 800)
        
        self.is_windows = platform.system() == "Windows"
        
        # Core systems
        self.db = DatabaseManager()
        self.thumb_cache = ThumbnailCache()
        self.tk_queue = TkQueue(root)
        self.worker = BackgroundWorker(self.tk_queue)
        
        # Media collections
        self.all_media = []
        self.media = []
        self.media_metadata = {}
        
        # State
        self.current_index = 0
        self.view_mode = "grid"
        self.sort_mode = "date"
        self.filter_query = ""
        self.showing_favorites = False
        self.showing_videos_only = False
        
        # Video player
        self.vlc_instance = None
        self.vlc_player = None
        if HAS_VLC:
            self.vlc_instance = vlc.Instance('--quiet')
            self.vlc_player = self.vlc_instance.media_player_new()
        
        # UI setup - Coquette theme
        self.is_dark = False  # Light pink theme
        self.colors = self.get_coquette_theme()
        self.theme_registry = []
        
        # Font configuration - soft and modern
        self.font_main = ("Nunito", 11)
        self.font_bold = ("Nunito", 12, "bold")
        self.font_title = ("Nunito", 20, "bold")
        self.font_emoji = ("Segoe UI Emoji", 22)
        self.font_small = ("Nunito", 9)
        
        self.create_widgets()
        self.bind_events()
        
        # Load initial
        self.root.after(100, self.load_initial_media)
        
    def get_coquette_theme(self):
        """Soft pink coquette palette"""
        return {
            'bg': '#fff0f6',           # Very light pink background
            'surface': '#ffd6e7',       # Soft pink surface
            'surface_hover': '#ffc2db', # Darker pink on hover
            'accent': '#ff69b4',        # Hot pink accent
            'accent_hover': '#ff4fa3',  # Darker hot pink
            'text': '#4a2a3a',          # Deep rose text
            'text_secondary': '#8a5a6f', # Muted rose
            'border': '#ffb6d5',        # Pink borders
            'danger': '#ff4d6d',        # Soft red
            'success': '#ff8fab',       # Rose success
            'favorite': '#ff85c1',      # Pink heart
            'video': '#ff99cc',         # Light pink video
            'duplicate': '#ff66b2'     # Bright pink duplicate
        }
        
    def create_cute_button(self, parent, text, command, is_accent=False, emoji=""):
        """Create a soft, interactive button with hover effects"""
        full_text = f"{emoji} {text}" if emoji else text
        
        btn = tk.Label(
            parent,
            text=full_text,
            font=self.font_bold if is_accent else self.font_main,
            bg=self.colors['accent'] if is_accent else self.colors['surface'],
            fg=self.colors['text'],
            padx=18,
            pady=8,
            cursor="hand2",
            relief="flat",
            bd=0
        )
        
        # Soft rounded feel using highlight
        btn.config(highlightbackground=self.colors['border'], highlightthickness=1)
        
        # Hover effects
        def on_enter(e, b=btn, accent=is_accent):
            if accent:
                b.config(bg=self.colors['accent_hover'])
            else:
                b.config(bg=self.colors['surface_hover'])
                
        def on_leave(e, b=btn, accent=is_accent):
            if accent:
                b.config(bg=self.colors['accent'])
            else:
                b.config(bg=self.colors['surface'])
                
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        btn.bind('<Button-1>', lambda e: command())
        
        return btn
        
    def register_widget(self, widget, config_type, color_key=None, fg_attr=None):
        self.theme_registry.append((widget, config_type, color_key, fg_attr))
        
    def apply_theme(self):
        for widget, config_type, color_key, fg_attr in self.theme_registry:
            try:
                if config_type == 'bg':
                    widget.config(bg=self.colors[color_key])
                elif config_type == 'both':
                    widget.config(bg=self.colors[color_key], fg=self.colors[fg_attr])
            except:
                pass
        self.root.configure(bg=self.colors['bg'])
        
    def create_widgets(self):
        # Gradient background canvas
        self.gradient_canvas = tk.Canvas(self.root, highlightthickness=0)
        self.gradient_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.draw_gradient()
        
        # Main container
        self.main_container = tk.Frame(self.root, bg=self.colors['bg'])
        self.main_container.pack(fill=tk.BOTH, expand=True, padx=25, pady=20)
        self.register_widget(self.main_container, 'bg', 'bg')
        
        self.setup_drag_drop()
        self.create_header()
        self.create_content()
        self.create_status_bar()
        self.apply_theme()
        
    def draw_gradient(self):
        """Draw soft pink gradient background"""
        width = self.root.winfo_screenwidth()
        height = self.root.winfo_screenheight()
        
        # Create gradient from light pink to rose
        for i in range(height):
            ratio = i / height
            # Interpolate between colors
            r = int(255 - (255 - 255) * ratio)      # 255 to 255
            g = int(240 - (240 - 182) * ratio)      # 240 to 182 (rose)
            b = int(246 - (246 - 193) * ratio)      # 246 to 193 (rose)
            
            color = f'#{r:02x}{g:02x}{b:02x}'
            self.gradient_canvas.create_line(0, i, width, i, fill=color, width=1)
            
    def setup_drag_drop(self):
        try:
            self.root.drop_target_register(tk.DND_FILES)
            self.root.dnd_bind('<<Drop>>', self.on_drop)
        except:
            pass
            
    def on_drop(self, event):
        paths = event.data.split()
        for path in paths:
            if os.path.isdir(path):
                self.scan_directory_background(path)
            elif os.path.isfile(path):
                self.add_single_file(path)
                
    def create_header(self):
        self.header = tk.Frame(self.main_container, height=80, bg=self.colors['surface'])
        self.header.pack(fill=tk.X, pady=(0, 20))
        self.header.pack_propagate(False)
        
        # Title with heart emoji
        title_frame = tk.Frame(self.header, bg=self.colors['surface'])
        title_frame.pack(side=tk.LEFT, padx=25, pady=20)
        
        tk.Label(title_frame, text="💗", font=self.font_emoji, 
                bg=self.colors['surface'], fg=self.colors['accent']).pack(side=tk.LEFT)
        
        tk.Label(title_frame, text="Lumina", font=self.font_title,
                bg=self.colors['surface'], fg=self.colors['text']).pack(side=tk.LEFT, padx=(8, 0))
        
        self.stats_label = tk.Label(title_frame, text="", font=self.font_small,
                                   bg=self.colors['surface'], fg=self.colors['text_secondary'])
        self.stats_label.pack(side=tk.LEFT, padx=(20, 0))
        
        # Controls with cute buttons
        controls = tk.Frame(self.header, bg=self.colors['surface'])
        controls.pack(side=tk.RIGHT, padx=25, pady=15)
        
        # Video filter button
        self.video_filter_btn = self.create_cute_button(
            controls, "Videos", self.toggle_video_filter, emoji="🎬"
        )
        self.video_filter_btn.pack(side=tk.LEFT, padx=8)
        
        # Favorites button
        self.fav_filter_btn = self.create_cute_button(
            controls, "Favorites", self.toggle_favorites, emoji="💗"
        )
        self.fav_filter_btn.pack(side=tk.LEFT, padx=8)
        
        # Sort dropdown - styled cute
        self.sort_var = tk.StringVar(value="Sort: Date 💕")
        sort_menu = tk.OptionMenu(controls, self.sort_var, 
                                 "Sort: Date 💕", "Sort: Name 🌸", "Sort: Size ✨", "Sort: Views 🌟",
                                 command=self.on_sort_change)
        sort_menu.config(font=self.font_main, bg=self.colors['surface'], 
                        fg=self.colors['text'], relief="flat", highlightthickness=0)
        sort_menu["menu"].config(font=self.font_main, bg=self.colors['surface'], 
                                fg=self.colors['text'])
        sort_menu.pack(side=tk.LEFT, padx=8)
        
        # Add folder button - accent
        self.add_btn = self.create_cute_button(
            controls, "Add Folder", self.add_folder_dialog, is_accent=True, emoji="📂"
        )
        self.add_btn.pack(side=tk.LEFT, padx=8)
        
        # Search entry with cute placeholder
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
        
    def create_content(self):
        self.content_frame = tk.Frame(self.main_container, bg=self.colors['bg'])
        self.content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Grid view with soft styling
        self.grid_frame = tk.Frame(self.content_frame, bg=self.colors['bg'])
        self.grid_canvas = tk.Canvas(self.grid_frame, highlightthickness=0, bg=self.colors['bg'])
        self.grid_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        
        scrollbar = ttk.Scrollbar(self.grid_frame, command=self.on_scroll)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.grid_canvas.configure(yscrollcommand=scrollbar.set)
        
        self.thumbnails_frame = tk.Frame(self.grid_canvas, bg=self.colors['bg'])
        self.canvas_window = self.grid_canvas.create_window((0, 0), window=self.thumbnails_frame, anchor="nw")
        
        # Single view
        self.single_frame = tk.Frame(self.content_frame, bg=self.colors['bg'])
        
        # Toolbar
        toolbar = tk.Frame(self.single_frame, height=60, bg=self.colors['surface'])
        toolbar.pack(fill=tk.X, pady=(0, 15))
        
        nav = tk.Frame(toolbar, bg=self.colors['surface'])
        nav.pack(side=tk.LEFT, padx=20, pady=15)
        
        back_btn = self.create_cute_button(nav, "Back", self.show_grid_view, emoji="←")
        back_btn.pack(side=tk.LEFT, padx=5)
        
        prev_btn = self.create_cute_button(nav, "Prev", self.prev_media, emoji="◀")
        prev_btn.pack(side=tk.LEFT, padx=5)
        
        next_btn = self.create_cute_button(nav, "Next", self.next_media, emoji="▶")
        next_btn.pack(side=tk.LEFT, padx=5)
        
        # Media container
        self.media_container = tk.Frame(self.single_frame, bg=self.colors['surface'],
                                       highlightbackground=self.colors['border'],
                                       highlightthickness=2)
        self.media_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        self.image_label = tk.Label(self.media_container, bg=self.colors['surface'])
        self.image_label.pack(expand=True)
        
        if HAS_VLC:
            self.video_frame = tk.Frame(self.media_container, bg=self.colors['surface'])
            self.vlc_player.set_hwnd(self.video_frame.winfo_id() if self.is_windows else self.video_frame.winfo_xid())
            
            self.video_controls = tk.Frame(self.single_frame, height=50, bg=self.colors['surface'])
            self.video_controls.pack(fill=tk.X, pady=10, padx=20)
            
            self.play_btn = self.create_cute_button(self.video_controls, "", self.toggle_video_playback, emoji="▶")
            self.play_btn.pack(side=tk.LEFT, padx=10)
            
            self.timeline = ttk.Scale(self.video_controls, from_=0, to=100, orient=tk.HORIZONTAL)
            self.timeline.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=15)
            self.timeline.bind('<ButtonRelease-1>', self.seek_video)
            
            self.time_label = tk.Label(self.video_controls, text="0:00 / 0:00",
                                      font=self.font_main, bg=self.colors['surface'], fg=self.colors['text'])
            self.time_label.pack(side=tk.RIGHT, padx=15)
        
        # Info panel
        self.info_frame = tk.Frame(self.single_frame, bg=self.colors['bg'])
        self.info_frame.pack(fill=tk.X, pady=15, padx=20)
        
        self.filename_label = tk.Label(self.info_frame, text="", font=self.font_title,
                                      bg=self.colors['bg'], fg=self.colors['text'])
        self.filename_label.pack(anchor=tk.W)
        
        self.details_label = tk.Label(self.info_frame, text="", font=self.font_main,
                                     bg=self.colors['bg'], fg=self.colors['text_secondary'])
        self.details_label.pack(anchor=tk.W, pady=(8, 0))
        
        # Similar images
        self.similar_frame = tk.Frame(self.single_frame, height=120, bg=self.colors['surface'],
                                     highlightbackground=self.colors['border'], highlightthickness=1)
        self.similar_frame.pack(fill=tk.X, pady=15, padx=20)
        tk.Label(self.similar_frame, text="Similar Photos 🌸", font=self.font_bold,
                bg=self.colors['surface'], fg=self.colors['text']).pack(anchor=tk.W, padx=15, pady=10)
        
        self.show_grid_view()
        
    def create_status_bar(self):
        self.status_bar = tk.Frame(self.main_container, height=35, bg=self.colors['surface'])
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM, pady=(20, 0))
        
        self.status_label = tk.Label(self.status_bar, text="Ready 💕", font=self.font_small,
                                    bg=self.colors['surface'], fg=self.colors['text_secondary'])
        self.status_label.pack(side=tk.LEFT, padx=25, pady=8)
        
        self.progress_label = tk.Label(self.status_bar, text="", font=self.font_small,
                                      bg=self.colors['surface'], fg=self.colors['accent'])
        self.progress_label.pack(side=tk.LEFT, padx=20, pady=8)
        
    def load_initial_media(self):
        """Load media on startup"""
        self.load_media_from_db()
        
        if not self.all_media:
            home = Path.home()
            default_dirs = [home / "Pictures", home / "Videos", home / "Downloads", home / "Desktop"]
            
            for dir_path in default_dirs:
                if dir_path.exists():
                    self.scan_directory_background(str(dir_path))
                    break
        
    def on_search_focus_in(self, event):
        if self.search_entry.get() == "Search your photos 💕":
            self.search_entry.delete(0, tk.END)
            self.search_entry.config(fg=self.colors['text'])
            
    def on_search_focus_out(self, event):
        if not self.search_entry.get():
            self.search_entry.insert(0, "Search your photos 💕")
            self.search_entry.config(fg=self.colors['text_secondary'])
        
    def show_empty_state(self):
        """Cute empty state"""
        for widget in self.thumbnails_frame.winfo_children():
            widget.destroy()

        empty = tk.Frame(self.thumbnails_frame, bg=self.colors['bg'])
        empty.pack(expand=True, fill=tk.BOTH)

        icon = tk.Label(empty, text="🎀", font=("Segoe UI", 72), 
                       bg=self.colors['bg'], fg=self.colors['accent'])
        icon.pack(pady=30)

        text = tk.Label(
            empty,
            text="Your gallery is empty\nAdd photos to get started 💗",
            font=self.font_title,
            bg=self.colors['bg'],
            fg=self.colors['text_secondary']
        )
        text.pack()
        
        hint = tk.Label(
            empty,
            text="Drag a folder here or click 📂 Add Folder",
            font=self.font_main,
            bg=self.colors['bg'],
            fg=self.colors['text_secondary']
        )
        hint.pack(pady=15)
        
    def scan_directory_background(self, directory):
        """Non-blocking scan with progress"""
        self.update_status(f"Scanning {directory}... 🌸")
        
        def scan_task():
            image_ext = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff'}
            video_ext = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v'}
            
            all_files = []
            path_obj = Path(directory)
            
            for ext in image_ext | video_ext:
                all_files.extend(path_obj.rglob(f"*{ext}"))
                all_files.extend(path_obj.rglob(f"*{ext.upper()}"))
                
            all_files = list(dict.fromkeys(all_files))
            total = len(all_files)
            
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
                    phash = None
                    
                    if is_video:
                        cap = cv2.VideoCapture(file_path)
                        if cap.isOpened():
                            ret, frame = cap.read()
                            if ret:
                                height, width = frame.shape[:2]
                                thumb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                                pil_img = Image.fromarray(thumb)
                                pil_img.thumbnail((300, 300))
                                
                                cache_key = self.thumb_cache.compute_content_hash(file_path, stat)
                                self.thumb_cache.put(cache_key, pil_img)
                                
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
                                phash = str(imagehash.phash(img))
                        except:
                            pass
                    
                    media_id, is_new = self.db.get_or_create_media(
                        file_path, media_type, size, mtime,
                        width=width, height=height, duration=duration, phash=phash
                    )
                    
                    if idx % 10 == 0:
                        self.tk_queue.put(lambda i=idx, t=total: 
                                        self.progress_label.config(text=f"Loading {i}/{t} ✨"))
                        
                except Exception as e:
                    print(f"Error scanning {file_path}: {e}")
                    
            self.tk_queue.put(lambda: self.finish_scan())
            
        self.worker.submit(f"scan_{directory}", scan_task)
        
    def finish_scan(self):
        self.progress_label.config(text="")
        self.update_status("All done! 💕")
        self.load_media_from_db()
        
    def load_media_from_db(self):
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM media ORDER BY mtime DESC')
            
            self.all_media = []
            self.media_metadata = {}
            
            for row in cursor.fetchall():
                path = row['path']
                self.all_media.append(path)
                self.media_metadata[path] = dict(row)
                
        self.apply_filters()
        self.update_stats()
        
    def apply_filters(self):
        filtered = self.all_media.copy()
        
        if self.showing_favorites:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT path FROM media WHERE favorite = 1')
                fav_paths = {r['path'] for r in cursor.fetchall()}
                filtered = [p for p in filtered if p in fav_paths]
                
        if self.showing_videos_only:
            filtered = [p for p in filtered if self.media_metadata.get(p, {}).get('media_type') == 'video']
            
        if self.filter_query:
            q = self.filter_query.lower()
            filtered = [p for p in filtered if q in os.path.basename(p).lower()]
            
        if self.sort_mode == 'date':
            filtered.sort(key=lambda p: self.media_metadata.get(p, {}).get('mtime', 0), reverse=True)
        elif self.sort_mode == 'name':
            filtered.sort(key=lambda p: os.path.basename(p).lower())
        elif self.sort_mode == 'size':
            filtered.sort(key=lambda p: self.media_metadata.get(p, {}).get('size', 0), reverse=True)
        elif self.sort_mode == 'views':
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT path, view_count FROM media')
                views = {r['path']: r['view_count'] for r in cursor.fetchall()}
                filtered.sort(key=lambda p: views.get(p, 0), reverse=True)
                
        self.media = filtered
        self.refresh_grid()
        
    def refresh_grid(self):
        for widget in self.thumbnails_frame.winfo_children():
            widget.destroy()
            
        if not self.media:
            self.show_empty_state()
            return
            
        width = self.content_frame.winfo_width()
        self.columns = max(3, width // 180)
        
        self.thumbnail_widgets = []
        
        for idx in range(len(self.media)):
            row = idx // self.columns
            col = idx % self.columns
            
            # Cute thumbnail frame with soft border
            frame = tk.Frame(
                self.thumbnails_frame,
                width=160,
                height=160,
                bg=self.colors['surface'],
                highlightbackground=self.colors['border'],
                highlightthickness=2
            )
            frame.grid(row=row, column=col, padx=12, pady=12)
            frame.grid_propagate(False)
            
            # Loading placeholder
            tk.Label(frame, text="✨", font=("Segoe UI", 20),
                    bg=self.colors['surface'], fg=self.colors['accent']).place(relx=0.5, rely=0.5, anchor="center")
            
            self.thumbnail_widgets.append(frame)
            
        self.thumbnails_frame.update_idletasks()
        self.grid_canvas.configure(scrollregion=(0, 0, width, (len(self.media) // self.columns + 1) * 180))
        
        self.update_visible_thumbnails()
        
    def update_visible_thumbnails(self):
        if not self.media:
            return
            
        y1 = self.grid_canvas.canvasy(0)
        y2 = y1 + self.content_frame.winfo_height()
        
        row_start = max(0, int(y1 // 180) - 1)
        row_end = int(y2 // 180) + 2
        
        idx_start = row_start * self.columns
        idx_end = min(len(self.media), (row_end + 1) * self.columns)
        
        for idx in range(idx_start, idx_end):
            if idx < len(self.thumbnail_widgets):
                self.load_thumbnail(idx)
                
    def load_thumbnail(self, idx):
        if idx >= len(self.media):
            return
            
        path = self.media[idx]
        frame = self.thumbnail_widgets[idx]
        
        # Clear frame
        for child in frame.winfo_children():
            child.destroy()
            
        try:
            meta = self.media_metadata.get(path, {})
            is_fav = meta.get('favorite', False)
            is_video = meta.get('media_type') == 'video'
            
            # Load image
            if is_video:
                # Show video thumbnail (first frame)
                cap = cv2.VideoCapture(path)
                ret, frame_img = cap.read()
                if ret:
                    frame_img = cv2.cvtColor(frame_img, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(frame_img)
                else:
                    img = Image.new('RGB', (150, 150), self.colors['surface'])
                cap.release()
            else:
                img = Image.open(path)
                img = ImageOps.exif_transpose(img)
                
            img.thumbnail((140, 140), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            
            label = tk.Label(frame, image=photo, bg=self.colors['surface'])
            label.image = photo
            label.place(relx=0.5, rely=0.45, anchor="center")
            
            # Heart for favorites
            if is_fav:
                heart = tk.Label(frame, text="♥", font=("Segoe UI", 14),
                               fg=self.colors['favorite'], bg=self.colors['surface'])
                heart.place(x=8, y=8)
                
            # Video indicator
            if is_video:
                vid_icon = tk.Label(frame, text="▶", font=("Segoe UI", 12),
                                  fg=self.colors['video'], bg=self.colors['surface'])
                vid_icon.place(relx=0.5, y=5, anchor="n")
                
            # Filename
            name = os.path.basename(path)[:15] + "..." if len(os.path.basename(path)) > 15 else os.path.basename(path)
            tk.Label(frame, text=name, font=self.font_small,
                    bg=self.colors['surface'], fg=self.colors['text_secondary']).place(relx=0.5, rely=0.88, anchor="center")
                    
            label.bind('<Button-1>', lambda e, i=idx: self.open_media(self.media[i]))
            
            # Hover effect
            def on_enter(e, f=frame):
                f.config(highlightbackground=self.colors['accent'])
            def on_leave(e, f=frame):
                f.config(highlightbackground=self.colors['border'])
                
            frame.bind('<Enter>', on_enter)
            frame.bind('<Leave>', on_leave)
            
        except Exception as e:
            tk.Label(frame, text="💔", font=("Segoe UI", 24),
                    bg=self.colors['surface'], fg=self.colors['danger']).place(relx=0.5, rely=0.5, anchor="center")
                    
    def open_media(self, path):
        meta = self.media_metadata.get(path, {})
        media_type = meta.get('media_type', 'image')
        
        self.current_index = self.media.index(path) if path in self.media else 0
        self.show_single_view()
        
        if 'id' in meta:
            self.db.update_view_stats(meta['id'])
            
        if media_type == 'video' and HAS_VLC:
            self.play_video(path)
        else:
            self.show_image(path)
            
        self.show_similar_images(path)
        
    def play_video(self, path):
        self.image_label.pack_forget()
        if HAS_VLC:
            self.video_frame.pack(fill=tk.BOTH, expand=True)
            media = self.vlc_instance.media_new(path)
            self.vlc_player.set_media(media)
            self.vlc_player.play()
            
            meta = self.media_metadata.get(path, {})
            duration = meta.get('duration', 0)
            mins, secs = divmod(duration, 60)
            self.time_label.config(text=f"0:00 / {mins}:{secs:02d}")
            
    def toggle_video_playback(self, event=None):
        if self.vlc_player:
            if self.vlc_player.is_playing():
                self.vlc_player.pause()
                self.play_btn.config(text="▶")
            else:
                self.vlc_player.play()
                self.play_btn.config(text="⏸")
                
    def seek_video(self, event):
        if self.vlc_player:
            pos = self.timeline.get()
            self.vlc_player.set_position(pos / 100.0)
            
    def show_image(self, path):
        if HAS_VLC:
            self.video_frame.pack_forget()
        self.image_label.pack(fill=tk.BOTH, expand=True)
        
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        
        container_w = self.media_container.winfo_width() - 40
        container_h = self.media_container.winfo_height() - 40
        img.thumbnail((container_w, container_h), Image.Resampling.LANCZOS)
        
        photo = ImageTk.PhotoImage(img)
        self.image_label.config(image=photo)
        self.image_label.image = photo
        
    def show_similar_images(self, path):
        meta = self.media_metadata.get(path, {})
        phash = meta.get('phash')
        
        if not phash:
            return
            
        similar = self.db.find_similar_images(phash, max_distance=10)
        
        for widget in self.similar_frame.winfo_children()[1:]:
            widget.destroy()
            
        for media_id, sim_path, distance in similar[:5]:
            if sim_path != path:
                btn = self.create_cute_button(self.similar_frame, os.path.basename(sim_path)[:20],
                                             lambda p=sim_path: self.open_media(p))
                btn.pack(side=tk.LEFT, padx=8)
                
    def show_grid_view(self):
        self.view_mode = "grid"
        self.single_frame.pack_forget()
        self.grid_frame.pack(fill=tk.BOTH, expand=True)
        self.refresh_grid()
        
    def show_single_view(self):
        self.view_mode = "single"
        self.grid_frame.pack_forget()
        self.single_frame.pack(fill=tk.BOTH, expand=True)
        
    def add_folder_dialog(self):
        folder = filedialog.askdirectory()
        if folder:
            self.scan_directory_background(folder)
            
    def toggle_favorites(self):
        self.showing_favorites = not self.showing_favorites
        
        if self.showing_favorites:
            self.video_filter_btn.pack_forget()
            self.fav_filter_btn.config(text="💗 All Photos", bg=self.colors['favorite'])
        else:
            self.video_filter_btn.pack(side=tk.LEFT, padx=8, before=self.fav_filter_btn)
            self.fav_filter_btn.config(text="💗 Favorites", bg=self.colors['surface'])
            
        self.apply_filters()
        
    def toggle_video_filter(self):
        self.showing_videos_only = not self.showing_videos_only
        
        if self.showing_videos_only:
            self.fav_filter_btn.pack_forget()
            self.video_filter_btn.config(text="🎬 All Photos", bg=self.colors['video'])
        else:
            self.fav_filter_btn.pack(side=tk.LEFT, padx=8, after=self.video_filter_btn)
            self.video_filter_btn.config(text="🎬 Videos", bg=self.colors['surface'])
            
        self.apply_filters()
        
    def on_sort_change(self, value):
        self.sort_mode = value.replace(" Sort: ", "").replace(" 💕", "").replace(" 🌸", "").replace(" ✨", "").replace(" 🌟", "").lower()
        self.apply_filters()
        
    def on_search(self, event):
        self.filter_query = self.search_var.get().lower()
        if self.filter_query == "search your photos 💕":
            self.filter_query = ""
        self.apply_filters()
        
    def on_scroll(self, *args):
        self.grid_canvas.yview(*args)
        self.update_visible_thumbnails()
        
    def update_status(self, text):
        self.status_label.config(text=text)
        
    def update_stats(self):
        total = len(self.all_media)
        videos = sum(1 for m in self.media_metadata.values() if m.get('media_type') == 'video')
        favs = sum(1 for m in self.media_metadata.values() if m.get('favorite'))
        self.stats_label.config(text=f"{total} photos ✨ {videos} videos 🎬 {favs} favorites 💗")
        
    def prev_media(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.open_media(self.media[self.current_index])
            
    def next_media(self):
        if self.current_index < len(self.media) - 1:
            self.current_index += 1
            self.open_media(self.media[self.current_index])
            
    def bind_events(self):
        self.root.bind('<Left>', lambda e: self.prev_media())
        self.root.bind('<Right>', lambda e: self.next_media())
        self.root.bind('<space>', lambda e: self.toggle_video_playback() if self.view_mode == "single" else None)
        self.root.bind('<Escape>', lambda e: self.show_grid_view())
        self.root.bind('<f>', lambda e: self.toggle_favorite_current())
        
    def toggle_favorite_current(self):
        if not self.media or self.current_index >= len(self.media):
            return
            
        path = self.media[self.current_index]
        meta = self.media_metadata.get(path, {})
        
        if 'id' in meta:
            new_state = self.db.toggle_favorite(meta['id'])
            meta['favorite'] = new_state
            self.update_status("Added to favorites 💗" if new_state else "Removed from favorites")
            self.refresh_grid()

def main():
    root = tk.Tk()
    app = CoquetteGalleryApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
    