import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from PIL import Image, ImageTk, ImageOps
import os
from pathlib import Path
import time
import platform
import threading
import gc
import shutil
import zipfile
import sqlite3
import math
from collections import OrderedDict
from datetime import datetime
from functools import partial

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False

try:
    import imagehash
    HAS_IMAGEHASH = True
except ImportError:
    HAS_IMAGEHASH = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import vlc
    HAS_VLC = True
except (ImportError, FileNotFoundError, OSError) as e:
    HAS_VLC = False

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from lumina.core.stability_manager import rate_limited, debounced, CrashTracker
from lumina.services.performance_monitor import SimplePerfMonitor
from lumina.ui.ux_enhancements import SimpleLoadingIndicator, EmptyState, KeyHintManager
from lumina.config import Config, ViewMode, SortMode
from lumina.models import MediaItem
from lumina.database import DatabaseManager
from lumina.services import ThumbnailCache
from lumina.workers import TkQueue, ThumbnailLoader, BackgroundWorker
from lumina.ui import ToastManager
from lumina.utils import logger, ThreadSafeDict, ThreadSafeList, ExifReader


class LuminaGalleryProMax:
    """
    Main application class for Lumina Gallery Pro Max.
    FIXED VERSION - Addresses all critical threading and UI issues.
    """
    
    def __init__(self, root):
        self.root = root
        
        # Window setup
        self.root.state('zoomed')
        self.root.title("Lumina Gallery Pro Max 💗")
        self.root.minsize(1100, 750)
        
        self.is_windows = platform.system() == "Windows"
        self.is_linux = platform.system() == "Linux"
        self.is_mac = platform.system() == "Darwin"

        if self.is_mac:
            self.root.attributes('-zoomed', 1)

        self.root.report_callback_exception = self._handle_error
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Initialize fonts FIRST
        self._init_fonts()
        
        # Initialize database
        try:
            self.db = DatabaseManager()
            Config.load_preferences(self.db)
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            self.db = None
        
        if self.db is None:
            self._show_db_error_and_exit()
            return

        self.colors = Config.COLORS

        # Initialize services (before widgets)
        self.thumb_cache = ThumbnailCache()
        self.tk_queue = TkQueue(root)
        self.worker = BackgroundWorker(self.tk_queue)

        self.thumb_loader = ThumbnailLoader(
            self.tk_queue,
            max_workers=Config.THUMB_WORKERS,
            max_concurrent=Config.MAX_CONCURRENT_LOADS
        )
        
        # State initialization
        self._init_state()
        
        # Create widgets
        self.create_widgets()
        
        # Initialize widget-dependent services
        self.loader = SimpleLoadingIndicator(self.main_container, self.colors)
        self.empty = EmptyState(self)
        self.keys = KeyHintManager(self)
        self.exif_reader = ExifReader()
        
        self.toast = ToastManager(root, Config.COLORS)
        self.crash_tracker = CrashTracker()
        self.perf = SimplePerfMonitor()
        
        self.bind_events()
        self.setup_key_hints()

        # Load initial data AFTER widgets are ready
        self.root.after(100, self.load_initial_media)
        
        # Schedule initial grid refresh after window is rendered
        self.root.after(200, self._initial_refresh)

        logger.info("LuminaGalleryProMax initialized")

    def _init_state(self):
        """Initialize all state variables"""
        # Directories
        self.trash_dir = Path(Config.TRASH_DIR)
        self.trash_dir.mkdir(parents=True, exist_ok=True)

        # Cleanup old trash
        try:
            deleted = self.db.cleanup_old_trash(Config.TRASH_RETENTION_DAYS)
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old trash items")
        except Exception as e:
            logger.error(f"Trash cleanup error: {e}")

        # Media storage
        self.all_media = []
        self.media = []
        self.media_by_id = ThreadSafeDict()
        self.media_by_path = ThreadSafeDict()

        # Selection state
        self.selected_items = set()
        self.last_selected_idx = None
        self.loading_thumbs = set()

        # View state
        self.current_index = 0
        self.view_mode = ViewMode.GRID
        self.sort_mode = SortMode.DATE
        self.filter_query = ""
        self.showing_favorites = False
        self.showing_videos_only = False
        self.showing_deleted = False
        self.showing_album = None
        self.showing_tag = None
        self.slideshow_active = False
        self.slideshow_items = []
        self.slideshow_index = 0
        
        # Timer tracking
        self.slideshow_after_id = None
        self._resize_after = None
        self._scroll_update_after = None
        self.video_timeline_after_id = None
        self.preview_after_id = None
        self._refresh_after = None

        # VLC setup
        self.vlc_instance = None
        self.vlc_player = None
        self.vlc_attached = False

        if HAS_VLC:
            try:
                self.vlc_instance = vlc.Instance('--quiet', '--avcodec-hw=any')
                self.vlc_player = self.vlc_instance.media_player_new()
            except Exception as e:
                logger.error(f"VLC initialization error: {e}")

        # Image viewing state
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
        self.current_photo = None
        self.rotation_angle = 0
        self.fullscreen = False

        # Thumbnail display state
        self.visible_thumbs = {}
        self.thumb_size = Config.THUMB_SIZE
        self.thumb_padding = Config.THUMB_PADDING
        self.columns = 4
        self._render_lock = threading.RLock()
        self._refreshing = False
        self.canvas_window = None
        self.preview_window = None
        
        # Scanning state
        self.scanning = False
        self.scan_start_time = 0
        
        # Track widget existence
        self._destroyed = False

    def _initial_refresh(self):
        """Initial grid refresh after window is ready"""
        if self._destroyed:
            return
        self.refresh_grid()

    def _init_fonts(self):
        """Initialize all fonts before widget creation"""
        self.font_main = self._get_font(11)
        self.font_bold = self._get_font(12, bold=True)
        self.font_title = self._get_font(20, bold=True)
        self.font_emoji = ("Segoe UI Emoji", 22) if self._font_exists("Segoe UI Emoji") else ("Arial", 22)
        self.font_small = self._get_font(9)

    def _get_font(self, size, bold=False):
        """Get font with fallback support"""
        family = self._get_font_family()
        weight = "bold" if bold else "normal"
        return (family, size, weight) if bold else (family, size)

    def _get_font_family(self):
        """Return available font family with fallbacks"""
        if self._font_exists("Nunito"):
            return "Nunito"
        elif self._font_exists("Segoe UI"):
            return "Segoe UI"
        elif self._font_exists("Helvetica"):
            return "Helvetica"
        else:
            return "Arial"

    def _font_exists(self, family):
        """Check if font family exists"""
        try:
            import tkinter.font as tkfont
            return family in tkfont.families()
        except Exception:
            return False

    def _handle_error(self, exc, val, tb):
        import traceback
        logger.error("Error:", exc_info=(exc, val, tb))
        
        if self.crash_tracker.record(val):
            try:
                messagebox.showerror("Error", 
                    "Multiple errors detected. Please restart the application.")
            except:
                pass
            self._on_close()
        else:
            try:
                self.toast.show("An error occurred", emoji="⚠️")
            except:
                pass

    def _on_close(self):
        """Clean shutdown of the application"""
        logger.info("Shutting down Lumina Gallery Pro Max...")
        self._destroyed = True

        self.stop_slideshow()

        if HAS_VLC and self.vlc_player:
            try:
                self.vlc_player.stop()
            except Exception:
                pass

        self.thumb_loader.cancel_all()
        self.thumb_loader.shutdown(wait=False)
        self.worker.shutdown()
        self.tk_queue.shutdown()

        self.thumb_cache.clear_ram()

        if self.original_image:
            try:
                self.original_image.close()
            except Exception:
                pass

        logger.info("Shutdown complete")
        try:
            self.root.destroy()
        except:
            pass

    def _show_db_error_and_exit(self):
        """Show error dialog when database fails to initialize"""
        try:
            messagebox.showerror(
                "Database Error", 
                "Failed to initialize database. Please check permissions and try again."
            )
        except:
            pass
        self.root.destroy()

    def create_widgets(self):
        """Create all UI widgets"""
        self.gradient_canvas = tk.Canvas(self.root, highlightthickness=0)
        self.gradient_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._draw_gradient() 
        
        self.main_container = tk.Frame(self.root, bg=self.colors['bg'])
        self.main_container.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.main_container.lift()

        self.create_menu()
        self.create_header()
        self.create_sidebar()
        self.create_content()
        self.create_status_bar()
        
        self.setup_drag_drop()

    def _draw_gradient(self):
        """Draw background gradient"""
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
        except tk.TclError as e:
            logger.error(f"Gradient draw error (widget destroyed): {e}")
        except Exception as e:
            logger.error(f"Gradient draw error: {e}")

    def _draw_header_decoration(self):
        """Draw decorative header border"""
        try:
            if not self.header.winfo_exists():
                return
            width = self.header.winfo_width()
            if width < 100:
                width = 1200
            
            colors = [self.colors['accent'], self.colors['favorite'], 
                     self.colors['success'], self.colors['accent']]
            segment_width = width // len(colors)
            
            self.header_border.delete("all")
            
            for i, color in enumerate(colors):
                x1 = i * segment_width
                x2 = (i + 1) * segment_width
                self.header_border.create_line(x1, 2, x2, 2, fill=color, width=3, smooth=True)
            
            for x in range(50, width, 200):
                self.header_border.create_oval(x-2, 1, x+2, 3, fill='white', outline='')
                
        except tk.TclError as e:
            logger.debug(f"Header decoration error (widget destroyed): {e}")
        except Exception as e:
            logger.debug(f"Header decoration error: {e}")

    def create_menu(self):
        """Create application menu bar"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Add Folder", command=self.add_folder_dialog, accelerator="Ctrl+O")
        file_menu.add_command(label="Add File", command=self.add_file_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Export Selected", command=self.export_selected)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close, accelerator="Alt+F4")

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Grid View", command=self.show_grid_view)
        view_menu.add_command(label="Slideshow", command=self.toggle_slideshow, accelerator="S")
        view_menu.add_separator()
        view_menu.add_command(label="Favorites", command=self.toggle_favorites)
        view_menu.add_command(label="Videos Only", command=self.toggle_video_filter)
        view_menu.add_command(label="Trash", command=self.show_trash)
        view_menu.add_separator()
        view_menu.add_command(label="Refresh", command=self.load_media_from_db, accelerator="F5")

        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Find Duplicates", command=self.show_duplicates)
        tools_menu.add_command(label="Manage Tags", command=self.show_tag_manager)
        tools_menu.add_command(label="Manage Albums", command=self.show_album_manager)
        tools_menu.add_separator()
        tools_menu.add_command(label="Preferences", command=self.show_preferences)

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Keyboard Shortcuts", command=self.show_shortcuts)
        help_menu.add_command(label="About", command=self.show_about)

    def setup_drag_drop(self):
        """Setup drag and drop functionality"""
        try:
            import tkinterdnd2
            self.root.drop_target_register(tkinterdnd2.DND_FILES)
            self.root.dnd_bind('<<Drop>>', self.on_drop)
            logger.info("Drag and drop enabled via tkinterdnd2")
        except ImportError:
            logger.info("tkinterdnd2 not available - drag and drop disabled")
        except Exception as e:
            logger.info(f"Drag and drop setup error: {e}")

    def on_drop(self, event):
        """Handle dropped files/folders"""
        try:
            if not hasattr(event, 'data'):
                return
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
        """Create application header"""
        self.header = tk.Frame(self.main_container, height=100, bg=self.colors['surface'])
        self.header.pack(fill=tk.X, pady=(0, 20))
        self.header.pack_propagate(False)
        
        self.header_border = tk.Canvas(self.header, height=4, bg=self.colors['surface'], 
                                       highlightthickness=0)
        self.header_border.pack(fill=tk.X, side=tk.TOP)
        
        # Delay decoration drawing until header is rendered
        self.header.after(100, self._draw_header_decoration)

        title_frame = tk.Frame(self.header, bg=self.colors['surface'])
        title_frame.pack(side=tk.LEFT, padx=25, pady=15)

        title_container = tk.Frame(title_frame, bg=self.colors['surface'])
        title_container.pack(side=tk.LEFT)
        
        tk.Label(title_container, text="✨", font=("Segoe UI", 16) if self._font_exists("Segoe UI") else ("Arial", 16), 
                bg=self.colors['surface'], fg=self.colors['accent']).pack(side=tk.LEFT, padx=(0, 5))
        
        tk.Label(title_container, text="💗", font=self.font_emoji, 
                bg=self.colors['surface'], fg=self.colors['accent']).pack(side=tk.LEFT)

        tk.Label(title_container, text="Lumina Pro Max", font=self.font_title,
                bg=self.colors['surface'], fg=self.colors['text']).pack(side=tk.LEFT, padx=(8, 5))
        
        tk.Label(title_container, text="✨", font=("Segoe UI", 16) if self._font_exists("Segoe UI") else ("Arial", 16), 
                bg=self.colors['surface'], fg=self.colors['accent']).pack(side=tk.LEFT)

        self.stats_label = tk.Label(title_frame, text="", font=self.font_small,
                                   bg=self.colors['surface'], fg=self.colors['text_secondary'])
        self.stats_label.pack(side=tk.LEFT, padx=(20, 0))

        controls = tk.Frame(self.header, bg=self.colors['surface'])
        controls.pack(side=tk.RIGHT, padx=25, pady=15)

        self.slideshow_btn_frame, self.slideshow_btn = self._create_button(
            controls, "Slideshow", self.toggle_slideshow, emoji="🎬"
        )
        self.slideshow_btn_frame.pack(side=tk.LEFT, padx=8)
        
        self.video_filter_btn_frame, self.video_filter_btn = self._create_button(
            controls, "Videos", self.toggle_video_filter, emoji="🎬"
        )
        self.video_filter_btn_frame.pack(side=tk.LEFT, padx=8)

        self.fav_filter_btn_frame, self.fav_filter_btn = self._create_button(
            controls, "Favorites", self.toggle_favorites, emoji="💗"
        )
        self.fav_filter_btn_frame.pack(side=tk.LEFT, padx=8)
        
        self.export_btn_frame, self.export_btn = self._create_button(
            controls, "Export", self.export_selected, emoji="📤"
        )
        self.export_btn_frame.pack(side=tk.LEFT, padx=8)

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

        self.add_btn_frame, self.add_btn = self._create_button(
            controls, "Add Folder", self.add_folder_dialog, is_accent=True, emoji="📂"
        )
        self.add_btn_frame.pack(side=tk.LEFT, padx=8)

    def create_sidebar(self):
        """Create sidebar with navigation and filters"""
        self.sidebar_container = tk.Frame(self.main_container, width=280, bg=self.colors['surface'])
        self.sidebar_container.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        self.sidebar_container.pack_propagate(False)

        self.sidebar_canvas = tk.Canvas(self.sidebar_container, bg=self.colors['surface'], 
                                        highlightthickness=0, width=280)
        self.sidebar_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.sidebar_scrollbar = ttk.Scrollbar(self.sidebar_container, orient="vertical", 
                                                command=self.sidebar_canvas.yview)
        self.sidebar_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.sidebar_canvas.configure(yscrollcommand=self.sidebar_scrollbar.set)

        self.sidebar = tk.Frame(self.sidebar_canvas, bg=self.colors['surface'], width=260)
        self.sidebar_window = self.sidebar_canvas.create_window((0, 0), window=self.sidebar, 
                                                                 anchor="nw", width=260)
        
        self.sidebar.bind("<Configure>", lambda e: self._on_sidebar_configure())
        self._bind_sidebar_mousewheel()

        # Search frame
        search_frame = tk.Frame(self.sidebar, bg=self.colors['surface'], 
                                highlightbackground=self.colors['accent'],
                                highlightthickness=2, bd=0)
        search_frame.pack(fill=tk.X, pady=(15, 10), padx=15)
        
        search_icon = tk.Label(search_frame, text="🔍", font=("Segoe UI", 12) if self._font_exists("Segoe UI") else ("Arial", 12),
                              bg=self.colors['surface'], fg=self.colors['accent'])
        search_icon.pack(side=tk.LEFT, padx=(10, 5))
        
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(search_frame, textvariable=self.search_var,
                                    font=self.font_main, width=18,
                                    bg=self.colors['surface'], fg=self.colors['text'],
                                    relief="flat", highlightthickness=0,
                                    insertbackground=self.colors['accent'])
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5), ipady=8)

        self.search_entry.insert(0, "Search photos...")
        self.search_entry.bind('<FocusIn>', self.on_search_focus_in)
        self.search_entry.bind('<FocusOut>', self.on_search_focus_out)
        self.search_entry.bind('<KeyRelease>', self.on_search)
        
        self.clear_search_btn = tk.Label(search_frame, text="✕", font=("Segoe UI", 10, "bold") if self._font_exists("Segoe UI") else ("Arial", 10, "bold"),
                                        bg=self.colors['surface'], fg=self.colors['text_secondary'],
                                        cursor="hand2", padx=5)
        self.clear_search_btn.bind('<Button-1>', lambda e: self.clear_search())

        # Library section
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

        # Albums section
        albums_frame = tk.Frame(self.sidebar, bg=self.colors['surface'])
        albums_frame.pack(fill=tk.X, pady=15, padx=15)

        tk.Label(albums_frame, text="Albums", font=self.font_bold,
                bg=self.colors['surface'], fg=self.colors['text']).pack(anchor=tk.W)

        self.albums_container = tk.Frame(albums_frame, bg=self.colors['surface'])
        self.albums_container.pack(fill=tk.X, pady=5)
        self.refresh_albums_list()

        new_album_btn = self._create_sidebar_button(
            albums_frame, "➕ New Album", self.create_new_album
        )
        new_album_btn.pack(fill=tk.X, pady=5)

        # Tags section
        tags_frame = tk.Frame(self.sidebar, bg=self.colors['surface'])
        tags_frame.pack(fill=tk.X, pady=15, padx=15)

        tk.Label(tags_frame, text="Tags", font=self.font_bold,
                bg=self.colors['surface'], fg=self.colors['text']).pack(anchor=tk.W)

        self.tags_container = tk.Frame(tags_frame, bg=self.colors['surface'])
        self.tags_container.pack(fill=tk.X, pady=5)
        self.refresh_tags_list()

        # Batch operations section
        batch_frame = tk.Frame(self.sidebar, bg=self.colors['surface'])
        batch_frame.pack(fill=tk.X, pady=15, padx=15)

        tk.Label(batch_frame, text="Batch Operations", font=self.font_bold,
                bg=self.colors['surface'], fg=self.colors['text']).pack(anchor=tk.W)

        self.batch_fav_btn = self._create_sidebar_button(
            batch_frame, "💗 Favorite Selected", self.batch_favorite
        )
        self.batch_fav_btn.pack(fill=tk.X, pady=5)

        self.batch_tag_btn = self._create_sidebar_button(
            batch_frame, "🏷️ Tag Selected", self.batch_tag
        )
        self.batch_tag_btn.pack(fill=tk.X, pady=5)

        self.batch_album_btn = self._create_sidebar_button(
            batch_frame, "📁 Add to Album", self.batch_add_to_album
        )
        self.batch_album_btn.pack(fill=tk.X, pady=5)

        self.batch_delete_btn = self._create_sidebar_button(
            batch_frame, "🗑️ Delete Selected", self.batch_delete
        )
        self.batch_delete_btn.pack(fill=tk.X, pady=5)

        self.clear_sel_btn = self._create_sidebar_button(
            batch_frame, "✓ Clear Selection", self.clear_selection
        )
        self.clear_sel_btn.pack(fill=tk.X, pady=5)

        tk.Frame(self.sidebar, height=20, bg=self.colors['surface']).pack()

    def _bind_sidebar_mousewheel(self):
        """Bind mousewheel events to sidebar canvas"""
        if self.is_linux:
            self.sidebar_canvas.bind("<Button-4>", self._on_sidebar_scroll)
            self.sidebar_canvas.bind("<Button-5>", self._on_sidebar_scroll)
        else:
            self.sidebar_canvas.bind("<MouseWheel>", self._on_sidebar_scroll)

    def _on_sidebar_scroll(self, event):
        """Handle sidebar scrolling"""
        if self.is_linux:
            delta = -1 if event.num == 5 else 1
        else:
            delta = -int(event.delta / 120)

        self.sidebar_canvas.yview_scroll(delta, "units")
        return "break"

    def _on_sidebar_configure(self):
        """Update sidebar scroll region"""
        self.sidebar_canvas.configure(scrollregion=self.sidebar_canvas.bbox("all"))

    def refresh_albums_list(self):
        """Refresh the albums list in sidebar"""
        for widget in self.albums_container.winfo_children():
            widget.destroy()
        
        albums = self.db.get_all_albums()
        for album in albums:
            btn = self._create_sidebar_button(
                self.albums_container, f"📔 {album['name']}", 
                partial(self.show_album, album['id'])
            )
            btn.pack(fill=tk.X, pady=2)
        
        self.sidebar.update_idletasks()
        self._on_sidebar_configure()

    def refresh_tags_list(self):
        """Refresh the tags list in sidebar"""
        for widget in self.tags_container.winfo_children():
            widget.destroy()
        
        tags = self.db.get_all_tags()
        for tag in tags[:10]:
            btn = self._create_sidebar_button(
                self.tags_container, f"🏷️ {tag['name']}", 
                partial(self.show_tag_filter, tag['id'])
            )
            btn.pack(fill=tk.X, pady=2)
        
        self.sidebar.update_idletasks()
        self._on_sidebar_configure()

    def _create_sidebar_button(self, parent, text, command):
        """Create a sidebar button with hover effects"""
        container = tk.Frame(parent, bg=self.colors['surface'])
        
        btn = tk.Label(container, text=text, font=self.font_main,
                      bg=self.colors['surface'], fg=self.colors['text'],
                      padx=10, pady=8, cursor="hand2", anchor="w")
        btn.pack(fill=tk.X)

        def on_enter(e, b=btn):
            b.config(bg=self.colors['surface_hover'])
            b.config(highlightbackground=self.colors['accent'], highlightthickness=2)
        def on_leave(e, b=btn):
            b.config(bg=self.colors['surface'], highlightthickness=0)

        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        btn.bind('<Button-1>', lambda e: command())
        
        return container

    def create_content(self):
        """Create main content area with view frames"""
        self.content_frame = tk.Frame(self.main_container, bg=self.colors['bg'])
        self.content_frame.pack(fill=tk.BOTH, expand=True)

        self.pages = tk.Frame(self.content_frame, bg=self.colors['bg'])
        self.pages.pack(fill=tk.BOTH, expand=True)

        self.grid_frame = tk.Frame(self.pages, bg=self.colors['bg'])
        self.single_frame = tk.Frame(self.pages, bg=self.colors['bg'])
        self.slideshow_frame = tk.Frame(self.pages, bg='black')
        self.add_folder_frame = tk.Frame(self.pages, bg=self.colors['bg'])

        # Use pack instead of place for better layout management
        for frame in (self.grid_frame, self.single_frame, self.slideshow_frame, self.add_folder_frame):
            frame.pack(fill=tk.BOTH, expand=True)
            frame.pack_forget()  # Hide all initially
    
        self.slideshow_label = tk.Label(self.slideshow_frame, bg='black')
        self.slideshow_label.place(relx=0.5, rely=0.5, anchor="center")

        self.grid_canvas = tk.Canvas(self.grid_frame, highlightthickness=0, bg=self.colors['bg'])
        self.grid_canvas.configure(yscrollincrement=20)
        self.grid_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)

        self.scrollbar = ttk.Scrollbar(self.grid_frame, orient="vertical", command=self.grid_canvas.yview)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.grid_canvas.configure(yscrollcommand=self.scrollbar.set)

        self.grid_inner_frame = tk.Frame(self.grid_canvas, bg=self.colors['bg'])
        self.canvas_window = self.grid_canvas.create_window(
            (0, 0), window=self.grid_inner_frame, anchor="nw", tags="inner"
        )

        self.grid_inner_frame.bind("<Configure>", lambda e: self._update_scrollregion())
        self.grid_canvas.bind("<Configure>", self._on_canvas_resize)

        self.create_single_view()
        
        # Show grid view by default
        self.grid_frame.pack(fill=tk.BOTH, expand=True)

    def _update_scrollregion(self):
        """Update canvas scroll region"""
        try:
            if self.grid_canvas.winfo_exists():
                self.grid_canvas.configure(scrollregion=self.grid_canvas.bbox("all"))
        except tk.TclError as e:
            logger.debug(f"Scrollregion update error: {e}")

    def _on_canvas_resize(self, event):
        """Handle canvas resize with debouncing"""
        if event.widget != self.grid_canvas:
            return
        
        if self._resize_after:
            try:
                self.root.after_cancel(self._resize_after)
            except:
                pass
        
        self._resize_after = self.root.after(Config.RESIZE_DEBOUNCE_MS, self._debounced_refresh)

    def _debounced_refresh(self):
        """Perform actual refresh after resize debounce"""
        self._resize_after = None
        if self.view_mode == ViewMode.GRID:
            self.refresh_grid()

    def create_single_view(self):
        """Create single media view"""
        toolbar = tk.Frame(self.single_frame, height=60, bg=self.colors['surface'])
        toolbar.pack(fill=tk.X, pady=(0, 15))
        toolbar.pack_propagate(False)

        nav = tk.Frame(toolbar, bg=self.colors['surface'])
        nav.pack(side=tk.LEFT, padx=20, pady=15)

        back_btn = self._create_button(nav, "Back", self.show_grid_view, emoji="←")[0]
        back_btn.pack(side=tk.LEFT, padx=5)
        prev_btn = self._create_button(nav, "Prev", self.prev_media, emoji="◀")[0]
        prev_btn.pack(side=tk.LEFT, padx=5)
        next_btn = self._create_button(nav, "Next", self.next_media, emoji="▶")[0]
        next_btn.pack(side=tk.LEFT, padx=5)

        actions = tk.Frame(toolbar, bg=self.colors['surface'])
        actions.pack(side=tk.RIGHT, padx=20)

        self.fav_btn_frame, self.fav_btn = self._create_button(actions, "", self.toggle_favorite_current, emoji="💗")
        self.fav_btn_frame.pack(side=tk.LEFT, padx=5)

        rotate_left_btn = self._create_button(actions, "", self.rotate_left, emoji="↺")[0]
        rotate_left_btn.pack(side=tk.LEFT, padx=5)
        rotate_right_btn = self._create_button(actions, "", self.rotate_right, emoji="↻")[0]
        rotate_right_btn.pack(side=tk.LEFT, padx=5)
        fullscreen_btn = self._create_button(actions, "", self.toggle_fullscreen, emoji="⛶")[0]
        fullscreen_btn.pack(side=tk.LEFT, padx=5)

        folder_btn = self._create_button(actions, "", self.open_current_folder, emoji="📁")[0]
        folder_btn.pack(side=tk.LEFT, padx=5)
        zoom_btn = self._create_button(actions, "", self.reset_zoom, emoji="🔍")[0]
        zoom_btn.pack(side=tk.LEFT, padx=5)
        copy_btn = self._create_button(actions, "", self.copy_current_path, emoji="📋")[0]
        copy_btn.pack(side=tk.LEFT, padx=5)
        info_btn = self._create_button(actions, "", self.show_exif_info, emoji="ℹ️")[0]
        info_btn.pack(side=tk.LEFT, padx=5)
        tag_btn = self._create_button(actions, "", self.tag_current, emoji="🏷️")[0]
        tag_btn.pack(side=tk.LEFT, padx=5)

        self.delete_btn_frame, self.delete_btn = self._create_button(
            actions, "", self.delete_current, emoji="🗑️", 
            bg=self.colors['danger'], hover_bg=self.colors['danger_hover']
        )
        self.delete_btn_frame.pack(side=tk.LEFT, padx=5)

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

            self.play_btn_frame, self.play_btn = self._create_button(self.video_controls, "", self.toggle_video_playback, emoji="▶")
            self.play_btn_frame.pack(side=tk.LEFT, padx=10)

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

    def _create_button(self, parent, text, command, is_accent=False, emoji="", 
                      bg=None, hover_bg=None):
        """Create a styled button"""
        full_text = f"{emoji} {text}" if emoji else text

        btn_bg = bg or (self.colors['accent'] if is_accent else self.colors['surface'])
        btn_hover = hover_bg or (self.colors['accent_hover'] if is_accent else self.colors['surface_hover'])

        btn_frame = tk.Frame(parent, bg=self.colors['border'], padx=1, pady=1)
        
        btn = tk.Label(btn_frame, text=full_text, font=self.font_bold if is_accent else self.font_main,
                      bg=btn_bg, fg=self.colors['text'], padx=16, pady=6,
                      cursor="hand2", relief="flat", bd=0)
        btn.pack()

        def on_enter(e, b=btn):
            b.config(bg=btn_hover)
        def on_leave(e, b=btn):
            b.config(bg=btn_bg)

        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        btn.bind('<Button-1>', lambda e: command())

        return btn_frame, btn

    def create_status_bar(self):
        """Create status bar"""
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
        """Bind all keyboard and mouse events"""
        self.header.bind('<Configure>', lambda e: self._draw_header_decoration())
        
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
        self.root.bind('<r>', lambda e: self.rotate_right())
        self.root.bind('<R>', lambda e: self.rotate_left())
        self.root.bind('<F11>', lambda e: self.toggle_fullscreen())

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

        self.root.bind("<Configure>", self._on_resize)

    def _on_resize(self, event):
        """Handle window resize"""
        if event.widget != self.root:
            return

        if self.view_mode == ViewMode.GRID:
            if self._resize_after:
                try:
                    self.root.after_cancel(self._resize_after)
                except:
                    pass
            self._resize_after = self.root.after(Config.RESIZE_DEBOUNCE_MS, self.refresh_grid)

    def handle_escape(self):
        """Handle escape key press"""
        if self.slideshow_active:
            self.stop_slideshow()
        elif self.view_mode == ViewMode.SINGLE:
            self.show_grid_view()

    # =========================================================================
    # SECTION 4: MEDIA LOADING & SCANNING
    # =========================================================================

    def load_initial_media(self):
        """Load media from database or scan default directories"""
        self.load_media_from_db()

        if not self.all_media:
            home = Path.home()
            default_dirs = [home / "Pictures", home / "Videos", home / "Downloads"]

            for dir_path in default_dirs:
                if dir_path.exists():
                    self.scan_directory_background(str(dir_path))
                    break

    def load_media_from_db(self):
        """Load all media from database"""
        if self._destroyed:
            return
            
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
                    rating=row.get('rating', 0),
                    phash=row.get('phash')
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
        """Scan directory for media files in background"""
        self.update_status(f"Scanning {directory}...")
        self.scanning = True
        self.scan_start_time = time.time()
    
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
            
            batch_size = 50
            for batch_start in range(0, total, batch_size):
                batch = all_files[batch_start:batch_start + batch_size]
                self._process_scan_batch(batch, image_ext, video_ext)
                
                progress = min(batch_start + batch_size, total)
                self.tk_queue.put(lambda p=progress, t=total: 
                                self._update_scan_progress(p, t))
                
            self.tk_queue.put(self._finish_scan)
            
        self.worker.submit(f"scan_{directory}", scan_task)

    def _process_scan_batch(self, batch, image_ext, video_ext):
        """Process a batch of files during scanning"""
        db_batch = []
    
        for file_path in batch:
            file_path = str(file_path)
            ext = Path(file_path).suffix.lower()
            
            is_video = ext in video_ext
            media_type = 'video' if is_video else 'image'
        
            try:
                stat = os.stat(file_path)
                size = stat.st_size
                mtime = stat.st_mtime
                
                width = height = duration = phash = None
               
                if is_video and HAS_CV2:
                    cap = cv2.VideoCapture(file_path)
                    if cap.isOpened():
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 30)
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
                            width, height = img.size
                            if HAS_IMAGEHASH:
                                phash = str(imagehash.phash(img))
                    except (IOError, OSError):
                        pass
                
                db_batch.append({
                    'path': file_path,
                    'media_type': media_type,
                    'size': size,
                    'mtime': mtime,
                    'width': width,
                    'height': height,
                    'duration': duration,
                    'phash': phash
                })
                
            except (IOError, OSError) as e:
                logger.debug(f"Error scanning {file_path}: {e}")
        
        added = 0
        for item in db_batch:
            try:
                _, is_new = self.db.get_or_create_media(**item)
                if is_new:
                    added += 1
            except sqlite3.Error as e:
                logger.debug(f"Error inserting {item['path']}: {e}")
        
        return added
        
    def _update_scan_progress(self, current, total):
        """Update scan progress display"""
        if self._destroyed:
            return
        self.progress_label.config(text=f"Loading {current}/{total}")
        if current % 100 == 0:
            self.load_media_from_db()

    def _finish_scan(self):
        """Complete scanning operation"""
        if self._destroyed:
            return
        self.progress_label.config(text="")
        self.update_status("All done!")
        self.scanning = False
        elapsed = time.time() - self.scan_start_time
        self.toast.show(f"Scan complete! {len(self.all_media)} items in {elapsed:.1f}s")
        self.load_media_from_db()

    def add_file_dialog(self):
        """Open dialog to add individual files"""
        filetypes = [
            ("Image files", "*.jpg *.jpeg *.png *.webp *.gif *.bmp *.tiff *.heic"),
            ("Video files", "*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.flv"),
            ("All files", "*.*")
        ]
        paths = filedialog.askopenfilenames(filetypes=filetypes)
        for path in paths:
            self.add_single_file(path)

    def add_single_file(self, path):
        """Add a single file to the library"""
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

            width = height = duration = phash = None

            if is_video and HAS_CV2:
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
                except Exception as e:
                    logger.debug(f"Image read failed: {e}")
                    width = height = 0

            self.db.get_or_create_media(
                path, media_type, size, mtime,
                width=width, height=height, duration=duration, phash=phash
            )

            self.load_media_from_db()
            self.toast.show(f"Added {os.path.basename(path)}")

        except (IOError, OSError) as e:
            logger.error(f"Error adding file {path}: {e}")

    def add_folder_dialog(self):
        """Open dialog to add a folder"""
        folder = filedialog.askdirectory()
        if not folder:
            return
        self.scan_directory_background(folder)

    # =========================================================================
    # SECTION 5: FILTERING & SORTING
    # =========================================================================

    def clear_gallery(self):
        """Remove all existing gallery widgets"""
        if not hasattr(self, "grid_inner_frame"):
            return

        for widget in self.grid_inner_frame.winfo_children():
            widget.destroy()

        if hasattr(self, "loading_thumbs"):
            self.loading_thumbs.clear()

    def apply_filters(self):
        """Apply current filters and rebuild gallery with proper cleanup"""
        if self._destroyed:
            return
            
        # Clear existing thumbnails before applying new filters
        self._clear_all_thumbnails()
        
        items = self.all_media

        if self.showing_deleted:
            items = [i for i in items if i.soft_delete]
        elif self.showing_favorites:
            items = [i for i in items if i.favorite]
        elif self.showing_videos_only:
            items = [i for i in items if i.is_video]
        elif self.showing_album is not None:
            album_media = self.db.get_media_in_album(self.showing_album)
            album_ids = {m['id'] for m in album_media}
            items = [i for i in items if i.id in album_ids]
        elif self.showing_tag is not None:
            tag_media = self.db.get_media_by_tag(self.showing_tag)
            tag_ids = {m['id'] for m in tag_media}
            items = [i for i in items if i.id in tag_ids]
        else:
            items = [i for i in items if not i.soft_delete]

        if self.filter_query:
            q = self.filter_query.lower()
            if HAS_RAPIDFUZZ:
                items = [
                    m for m in items
                    if fuzz.partial_ratio(q, m.filename.lower()) > 60
                ]
            else:
                items = [m for m in items if q in m.filename.lower()]

        if self.sort_mode == SortMode.DATE:
            items.sort(key=lambda x: x.mtime, reverse=True)
        elif self.sort_mode == SortMode.NAME:
            items.sort(key=lambda x: x.filename.lower())
        elif self.sort_mode == SortMode.SIZE:
            items.sort(key=lambda x: x.size, reverse=True)
        elif self.sort_mode == SortMode.VIEWS:
            items.sort(key=lambda x: x.view_count, reverse=True)
        elif self.sort_mode == SortMode.RATING:
            items.sort(key=lambda x: x.rating, reverse=True)
        elif self.sort_mode == SortMode.RANDOM:
            import random
            random.shuffle(items)

        # Limit results for performance
        self.media = items[:500] if len(items) > 500 else items
        
        # Force complete grid refresh
        self.columns = 4  # Reset columns
        self.current_index = 0  # Reset scroll position
        
        # Schedule refresh after small delay to ensure cleanup completes
        if not self._destroyed:
            self.root.after(50, self.refresh_grid)

    def on_sort_change(self, value):
        """Handle sort mode change"""
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
        if self.filter_query == "search photos...":
            self.filter_query = ""
        
        if self.filter_query and self.filter_query != "search photos...":
            self.clear_search_btn.pack(side=tk.RIGHT, padx=(0, 10))
        else:
            self.clear_search_btn.pack_forget()
            
        self.apply_filters()

    def clear_search(self):
        """Clear search filter"""
        self.search_var.set("")
        self.filter_query = ""
        self.clear_search_btn.pack_forget()
        self.search_entry.focus()
        self.apply_filters()

    def on_search_focus_in(self, event):
        """Handle search entry focus in"""
        if self.search_entry.get() == "Search photos...":
            self.search_entry.delete(0, tk.END)
            self.search_entry.config(fg=self.colors['text'])

    def on_search_focus_out(self, event):
        """Handle search entry focus out"""
        if not self.search_entry.get():
            self.search_entry.insert(0, "Search photos...")
            self.search_entry.config(fg=self.colors['text_secondary'])

    # =========================================================================
    # SECTION 6: THUMBNAIL GRID MANAGEMENT - CRITICAL FIXES
    # =========================================================================
    
    def refresh_grid(self):
        """Refresh thumbnail grid display - FIXED VERSION"""
        if self._destroyed:
            return
            
        if not hasattr(self, 'grid_canvas') or not self.grid_canvas.winfo_exists():
            return

        if self._refreshing:
            return

        self._refreshing = True
        try:
            # Force update to get accurate dimensions
            self.grid_canvas.update_idletasks()
            
            canvas_width = self.grid_canvas.winfo_width()
            canvas_height = self.grid_canvas.winfo_height()
            
            # FIX: Handle case where canvas hasn't been rendered yet
            if canvas_width <= 1 or canvas_height <= 1:
                # Canvas not ready, schedule retry
                self.root.after(100, self.refresh_grid)
                return
            
            usable_width = max(canvas_width - 30, 400)
            total_item_width = self.thumb_size + (self.thumb_padding * 2)
            
            if total_item_width <= 0:
                return
            
            new_columns = max(2, usable_width // total_item_width)
            
            # FIX: Only clear if columns changed significantly or first load
            if abs(self.columns - new_columns) >= 1:
                self.columns = new_columns
                self._clear_all_thumbnails()
            
            self._recycle_thumbnail_layout(usable_width)
            self.update_scroll_region()
            
        finally:
            self._refreshing = False

    def show_empty_state(self):
        """Show empty state message"""
        if self._destroyed:
            return
            
        if self.showing_deleted:
            self.empty.show(self.grid_inner_frame, 'trash',
                "Your trash is empty 🗑️",
                "Deleted items will appear here")
        elif self.showing_favorites:
            self.empty.show(self.grid_inner_frame, 'favorites',
                "No favorites yet 💗",
                "Mark items as favorites to see them here")
        elif self.showing_album:
            self.empty.show(self.grid_inner_frame, 'album',
                "This album is empty 📔",
                "Add photos to this album")
        elif self.showing_tag:
            self.empty.show(self.grid_inner_frame, 'tag',
                "No items with this tag 🏷️",
                "Tag items to see them here")
        elif self.filter_query:
            self.empty.show(self.grid_inner_frame, 'search',
                f"No results for '{self.filter_query}' 🔍",
                "Try a different search term")
        else:
            self.empty.show(self.grid_inner_frame, 'empty',
                "Your gallery is empty 📷",
                "Add photos to get started",
                action_text="Add Folder",
                action_cmd=self.add_folder_dialog)
    
    def _recycle_thumbnail_layout(self, canvas_width):
        """Efficiently recycle thumbnail widgets - FIXED VERSION"""
        if self._destroyed:
            return
            
        if canvas_width is None or canvas_width <= 1:
            canvas_width = max(self.grid_canvas.winfo_width() - 30, 400)
        
        if canvas_width <= 0:
            return  

        if not self._render_lock.acquire(blocking=False):
            return

        try:
            if not self.grid_canvas.winfo_exists():
                return
                
            # Update canvas window width
            try:
                self.grid_canvas.itemconfig(self.canvas_window, width=canvas_width)
            except tk.TclError:
                return

            self.grid_inner_frame.config(width=canvas_width)

            if not self.media:
                self._clear_all_thumbnails()    
                self.show_empty_state()
                return

            total_item_width = self.thumb_size + (self.thumb_padding * 2)
            if total_item_width <= 0:
                return

            new_columns = max(2, canvas_width // total_item_width)
            
            if self.columns != new_columns:
                self.columns = new_columns
                self._clear_all_thumbnails()

            start, end = self.get_visible_range()
            
            start = max(0, min(start, len(self.media)))
            end = max(start, min(end, len(self.media)))
            
            if start >= len(self.media):
                return
            
            visible_indices = set(range(start, end))
            
            # FIX: Larger buffer for smoother scrolling
            buffer_size = self.columns * 6  # Increased from 4
            viewport_buffer = set(
                range(
                    max(0, start - buffer_size), 
                    min(len(self.media), end + buffer_size)
                )
            )
            
            # Remove thumbnails outside buffer
            for idx in list(self.visible_thumbs.keys()):
                if idx not in viewport_buffer:
                    self._remove_thumbnail(idx)

            # Hide thumbnails outside visible range but keep in buffer
            for idx in list(self.visible_thumbs.keys()):
                if idx not in visible_indices and idx in self.visible_thumbs:
                    frame = self.visible_thumbs[idx]
                    if frame.winfo_exists():
                        frame.grid_forget()

            # Create missing thumbnails
            for idx in visible_indices:
                if idx < len(self.media):
                    if idx in self.visible_thumbs:
                        self._reposition_thumbnail(idx)
                    else:
                        self._create_thumbnail_widget_fast(idx)

        finally:
            self._render_lock.release()

    def _update_thumbnail_selections(self):
        """Update selection visual state for all visible thumbnails"""
        if self._destroyed:
            return
            
        for idx, frame in list(self.visible_thumbs.items()):
            if idx >= len(self.media):
                self._remove_thumbnail(idx)
                continue

            item = self.media[idx]
            is_selected = item.id in self.selected_items

            bg_color = self.colors['surface_selected'] if is_selected else self.colors['surface']
            border_color = self.colors['selected'] if is_selected else self.colors['border']

            try:
                if frame.winfo_exists():
                    frame.config(bg=bg_color, highlightbackground=border_color,
                                highlightthickness=3 if is_selected else 2)
            except tk.TclError:
                pass

    def _reposition_thumbnail(self, idx):
        """Reposition an existing thumbnail widget"""
        if self._destroyed:
            return
            
        if idx not in self.visible_thumbs:
            return

        if idx >= len(self.media):
            self._remove_thumbnail(idx)
            return

        frame = self.visible_thumbs[idx]
        item = self.media[idx]

        if not frame.winfo_exists():
            return

        row = idx // self.columns
        col = idx % self.columns

        frame.grid(row=row, column=col, padx=self.thumb_padding//2, pady=self.thumb_padding//2)

        frame.media_path = item.path
        frame.media_id = item.id
        frame.media_idx = idx

        is_selected = item.id in self.selected_items
        bg_color = self.colors['surface_selected'] if is_selected else self.colors['surface']
        border_color = self.colors['selected'] if is_selected else self.colors['border']

        try:
            frame.config(bg=bg_color, highlightbackground=border_color,
                        highlightthickness=3 if is_selected else 2)
        except tk.TclError:
            pass

    def _create_thumbnail_widget_fast(self, idx):
        """Create thumbnail widget with validation - FIXED VERSION"""
        if self._destroyed:
            return
            
        if idx >= len(self.media) or idx < 0:
            return
        
        if self.thumb_size <= 0:
            return

        item = self.media[idx]
        row = idx // self.columns
        col = idx % self.columns

        is_selected = item.id in self.selected_items
        bg_color = self.colors['surface_selected'] if is_selected else self.colors['surface']
        border_color = self.colors['selected'] if is_selected else self.colors['border']

        try:
            frame = tk.Frame(
                self.grid_inner_frame, 
                width=self.thumb_size, 
                height=self.thumb_size,
                bg=bg_color, 
                highlightbackground=border_color,
                highlightthickness=3 if is_selected else 2
            )
            frame.grid(
                row=row, 
                column=col, 
                padx=self.thumb_padding//2, 
                pady=self.thumb_padding//2,
                sticky="nsew"
            )
            frame.grid_propagate(False)

            frame.media_path = item.path
            frame.media_id = item.id
            frame.media_idx = idx

            placeholder_size = min(100, self.thumb_size - 20)
            if placeholder_size > 0:
                placeholder = tk.Label(
                    frame, 
                    text="⏳", 
                    font=("Segoe UI", 20) if self._font_exists("Segoe UI") else ("Arial", 20),
                    bg=bg_color, 
                    fg=self.colors['text_secondary']
                )
                placeholder.place(relx=0.5, rely=0.5, anchor="center")

            self.visible_thumbs[idx] = frame

            frame.bind("<Enter>", lambda e, f=frame, i=idx: self._on_thumb_enter(e, f, i))
            frame.bind("<Leave>", lambda e, f=frame: self._on_thumb_leave(e, f))
            frame.bind("<Button-1>", lambda e, f=frame: self._on_thumbnail_click(e, f))
            frame.config(cursor="hand2")

            # Load thumbnail with priority based on visibility
            start, end = self.get_visible_range()
            priority = 0 if start <= idx < end else 2  

            task_id = f"thumb_{idx}_{hash(item.path)}"
            
            if item.path not in self.loading_thumbs:
                self.loading_thumbs.add(item.path)

                # FIX: Pass frame reference safely to callback
                self.thumb_loader.submit(
                    task_id,
                    priority,
                    lambda p=item.path: self._load_thumbnail_image(p),
                    lambda result, f=frame, i=idx, p=item.path:
                        self._safe_thumbnail_callback(f, i, result, p)
                )
        except tk.TclError:
            pass

    def _safe_thumbnail_callback(self, frame, idx, result, path):
        """Safely handle thumbnail load callback with existence checks"""
        if self._destroyed:
            return
            
        self.loading_thumbs.discard(path)
        
        # Check if frame still exists and is the correct one
        try:
            if not frame.winfo_exists():
                return
            current_idx = getattr(frame, 'media_idx', None)
            if current_idx != idx:
                return
            current_path = getattr(frame, 'media_path', None)
            if current_path != path:
                return
        except tk.TclError:
            return
            
        self._apply_thumbnail_image(frame, idx, result)

    def _refresh_thumbnail_by_item_id(self, item_id):
        """Refresh a specific thumbnail by item ID"""
        if self._destroyed:
            return
            
        for idx, media_item in enumerate(self.media):
            if media_item.id == item_id:
                if idx in self.visible_thumbs:
                    self._remove_thumbnail(idx)
                    self._create_thumbnail_widget_fast(idx)
                break 

    def _on_thumb_enter(self, event, frame, idx):
        """Handle mouse enter on thumbnail"""
        if self._destroyed:
            return
            
        if idx >= len(self.media):
            return
            
        item = self.media[idx]
        if item.id not in self.selected_items:
            try:
                if frame.winfo_exists():
                    frame.config(bg=self.colors['surface_hover'], highlightbackground=self.colors['accent'])
            except tk.TclError:
                pass
        
        try:
            if frame.winfo_exists():
                frame.tkraise()
        except tk.TclError:
            pass

        if self.preview_after_id:
            try:
                self.root.after_cancel(self.preview_after_id)
            except:
                pass

        self.preview_after_id = self.root.after(
            Config.PREVIEW_DELAY_MS, 
            lambda: self.show_preview(item.path, event.x_root, event.y_root)
        )

    def _on_thumb_leave(self, event, frame):
        """Handle mouse leave on thumbnail"""
        if self._destroyed:
            return
            
        idx = getattr(frame, 'media_idx', None)
        if idx is not None and idx < len(self.media):
            item = self.media[idx]
            if item.id not in self.selected_items:
                try:
                    if frame.winfo_exists():
                        frame.config(bg=self.colors['surface'], highlightbackground=self.colors['border'])
                except tk.TclError:
                    pass

        if self.preview_after_id:
            try:
                self.root.after_cancel(self.preview_after_id)
            except:
                pass
            self.preview_after_id = None
        self.hide_preview()

    def _load_thumbnail_image(self, path):
        """Load thumbnail image with proper caching"""
        try:
            item = self.media_by_path.get(path)
            if not item:
                return None

            target_size = self.thumb_size - 20

            # Generate cache key
            try:
                stat = os.stat(path)
                cache_key = self.thumb_cache.compute_content_hash(path, stat)
            except (OSError, IOError):
                cache_key = None

            # Try cache first
            if cache_key:
                cached = self.thumb_cache.get(cache_key)
                if cached:
                    return (cached, item.favorite, item.is_video, item.rating)

            # Load the image
            if item.is_video:
                if not HAS_CV2:
                    return None
                    
                cap = cv2.VideoCapture(path)
                ret, frame = cap.read()
                cap.release()

                if not ret:
                    return None

                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame)
            else:
                with Image.open(path) as img:
                    img = ImageOps.exif_transpose(img)
                    img = img.convert('RGB')

            # Resize to thumbnail size
            img.thumbnail((target_size, target_size), Config.THUMB_QUALITY)

            # Cache the result
            if cache_key:
                self.thumb_cache.put(cache_key, img.copy())

            return (img, item.favorite, item.is_video, item.rating)

        except Exception as e:
            logger.debug(f"Thumbnail error for {path}: {e}")
            return None

    def _apply_thumbnail_image(self, frame, idx, result):
        """Apply loaded thumbnail image with strict validation - FIXED"""
        if self._destroyed:
            return
            
        # Validate frame still exists
        try:
            if not frame.winfo_exists():
                return
        except tk.TclError:
            return
        
        # Validate this is still the correct index
        current_idx = getattr(frame, 'media_idx', None)
        if current_idx != idx:
            return
        
        # Validate path hasn't changed
        current_path = getattr(frame, 'media_path', None)
        if not current_path or not os.path.exists(current_path):
            return
        
        # Clear old widgets
        for widget in frame.winfo_children():
            try:
                widget.destroy()
            except tk.TclError:
                pass
        
        if result is None:
            # Show error state
            try:
                tk.Label(
                    frame, 
                    text="💔", 
                    font=("Segoe UI", 24) if self._font_exists("Segoe UI") else ("Arial", 24),
                    bg=frame.cget('bg'), 
                    fg=self.colors['danger']
                ).place(relx=0.5, rely=0.5, anchor="center")
            except tk.TclError:
                pass
            return
        
        img, is_fav, is_video, rating = result
        
        # Create PhotoImage with error handling
        try:
            photo = ImageTk.PhotoImage(img)
        except Exception as e:
            logger.debug(f"PhotoImage creation failed: {e}")
            return
        
        # Store reference on frame to prevent garbage collection
        frame._thumbnail_photo = photo

        try:
            # Create image label
            lbl = tk.Label(frame, image=photo, bg=frame.cget('bg'))
            lbl.place(relx=0.5, rely=0.45, anchor="center")
            
            # Add overlays
            if is_fav:
                tk.Label(
                    frame, 
                    text="♥", 
                    font=("Segoe UI", 12) if self._font_exists("Segoe UI") else ("Arial", 12),
                    fg=self.colors['favorite'], 
                    bg=frame.cget('bg')
                ).place(x=5, y=5)
            
            if rating > 0:
                stars = "★" * rating
                tk.Label(
                    frame, 
                    text=stars, 
                    font=("Segoe UI", 8) if self._font_exists("Segoe UI") else ("Arial", 8),
                    fg=self.colors['accent'], 
                    bg=frame.cget('bg')
                ).place(x=5, y=25)
            
            if is_video:
                tk.Label(
                    frame, 
                    text="▶", 
                    font=("Segoe UI", 10) if self._font_exists("Segoe UI") else ("Arial", 10),
                    fg=self.colors['video'], 
                    bg=frame.cget('bg')
                ).place(relx=0.5, y=2, anchor="n")
            
            # Filename label
            name = os.path.basename(current_path)
            if len(name) > 20:
                name = name[:17] + "..."
            
            tk.Label(
                frame, 
                text=name, 
                font=self.font_small,
                bg=frame.cget('bg'), 
                fg=self.colors['text_secondary']
            ).place(relx=0.5, rely=0.88, anchor="center")
        except tk.TclError:
            pass

    def _remove_thumbnail(self, idx):
        """Remove a thumbnail widget and cancel loading"""
        if idx in self.visible_thumbs:
            frame = self.visible_thumbs[idx]
            try:
                if frame.winfo_exists():
                    # Clear photo reference to help garbage collection
                    if hasattr(frame, '_thumbnail_photo'):
                        frame._thumbnail_photo = None
                    frame.destroy()
            except tk.TclError:
                pass
            del self.visible_thumbs[idx]

        for task_id in list(self.thumb_loader.pending_futures.keys()):
            if task_id.startswith(f"thumb_{idx}_"):
                self.thumb_loader.cancel(task_id)

    def _clear_all_thumbnails(self):
        """Clear all thumbnail widgets and reset loading state - FIXED"""
        if self._destroyed:
            return
            
        # Cancel all pending thumbnail loads
        if hasattr(self, "thumb_loader"):
            self.thumb_loader.cancel_all()

        # Destroy all thumbnail frames
        for idx in list(self.visible_thumbs.keys()):
            frame = self.visible_thumbs[idx]
            try:
                if frame.winfo_exists():
                    # Clear photo reference to help garbage collection
                    if hasattr(frame, '_thumbnail_photo'):
                        frame._thumbnail_photo = None
                    frame.destroy()
            except tk.TclError:
                pass

        # Clear tracking collections
        self.visible_thumbs.clear()
        self.loading_thumbs.clear()
        self.zoom_cache.clear()
        
        # Reset scroll position
        if hasattr(self, 'grid_canvas'):
            try:
                if self.grid_canvas.winfo_exists():
                    self.grid_canvas.yview_moveto(0)
            except tk.TclError:
                pass
        
        self.update_selection_label()

    def get_visible_range(self):
        """Calculate visible thumbnail range - FIXED VERSION"""
        if not self.media or self.columns <= 0:
            return 0, 0
        
        try:
            canvas_height = self.grid_canvas.winfo_height()
            canvas_width = self.grid_canvas.winfo_width()
        except tk.TclError:
            return 0, 0
        
        if canvas_height <= 0:
            canvas_height = 600
        if canvas_width <= 0:
            canvas_width = 800
        
        row_height = self.thumb_size + self.thumb_padding
        if row_height <= 0:
            row_height = 200
        
        try:
            first_y = self.grid_canvas.canvasy(0)
            last_y = self.grid_canvas.canvasy(canvas_height)
        except tk.TclError:
            return 0, 0
        
        start_row = max(0, int(first_y // row_height) - 1)
        visible_rows = max(1, int((last_y - first_y) // row_height) + 3)
        
        start = start_row * self.columns
        end = (start_row + visible_rows) * self.columns
        
        start = max(0, min(start, len(self.media)))
        end = max(start, min(end, len(self.media)))
        
        # FIX: Remove arbitrary limit that was causing early scroll stop
        # if end - start > Config.MAX_VISIBLE_THUMBS:
        #     end = start + Config.MAX_VISIBLE_THUMBS
        
        return start, end

    def update_visible_thumbnails(self):
        """Update visible thumbnails with synchronization"""
        if not self.media or getattr(self, '_refreshing', False):
            return
        
        if self._scroll_update_after is not None:
            try:
                self.root.after_cancel(self._scroll_update_after)
            except Exception:
                pass
        
        self._scroll_update_after = self.root.after(50, self._do_update_visible_thumbnails)

    def _do_update_visible_thumbnails(self):
        """Actual thumbnail update logic"""
        self._scroll_update_after = None
        
        if not self.media or self._destroyed:
            return
        
        start, end = self.get_visible_range()
        if start >= end:
            return
        
        visible_range = set(range(start, end))
        
        for idx, frame in list(self.visible_thumbs.items()):
            if idx in visible_range:
                try:
                    if not frame.winfo_ismapped():
                        self._reposition_thumbnail(idx)
                except tk.TclError:
                    pass
            else:
                try:
                    if frame.winfo_ismapped():
                        frame.grid_forget()
                except tk.TclError:
                    pass
        
        for idx in visible_range:
            if idx < len(self.media) and idx not in self.visible_thumbs:
                self._create_thumbnail_widget_fast(idx)
        
        buffer_start = max(0, start - self.columns * 3)
        buffer_end = min(len(self.media), end + self.columns * 3)
        viewport_buffer = set(range(buffer_start, buffer_end))
        
        for idx in list(self.visible_thumbs.keys()):
            if idx not in viewport_buffer:
                self._remove_thumbnail(idx)

    def _on_thumbnail_click(self, event, frame):
        """Handle thumbnail click"""
        if self._destroyed:
            return
            
        try:
            if not frame.winfo_exists():
                return
        except tk.TclError:
            return

        path = getattr(frame, 'media_path', None)
        idx = getattr(frame, 'media_idx', None)

        if path is None or idx is None or idx >= len(self.media):
            return

        item = self.media_by_path.get(path)
        if not item:
            return

        is_ctrl = (event.state & 0x4) != 0  
        is_shift = (event.state & 0x1) != 0  
        
        if is_ctrl:
            self.toggle_selection(item)
            self.refresh_grid()
            
        elif is_shift and self.last_selected_idx is not None:
            start = min(self.last_selected_idx, idx)
            end = max(self.last_selected_idx, idx)
            for i in range(start, end + 1):
                if 0 <= i < len(self.media):
                    self.selected_items.add(self.media[i].id)
            self.last_selected_idx = idx
            self.refresh_grid()
            
        else:
            self.clear_selection()
            self.last_selected_idx = idx
            self.open_media(item)

    def toggle_selection(self, item):
        """Toggle selection state for an item"""
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
        """Update the selection count label"""
        count = len(self.selected_items)
        
        if not hasattr(self, 'selection_label') or self.selection_label is None:
            return
            
        if count > 0:
            self.selection_label.config(text=f"{count} selected")
        else:
            self.selection_label.config(text="")

    def update_scroll_region(self):
        """Update the scroll region based on content"""
        if not self.media:
            return
        rows = math.ceil(len(self.media) / self.columns)
        height = rows * (self.thumb_size + self.thumb_padding)
        try:
            if self.grid_canvas.winfo_exists():
                self.grid_canvas.config(scrollregion=(0, 0, 0, height))
        except tk.TclError:
            pass

    def on_scroll(self, *args):
        """Handle scrollbar scroll events"""
        self.grid_canvas.yview(*args)

        if self._scroll_update_after is not None:
            try:
                self.root.after_cancel(self._scroll_update_after)
            except Exception:
                pass

        self._scroll_update_after = self.root.after(50, self.update_visible_thumbnails)

    def smooth_scroll(self, event):
        """Handle smooth scrolling"""
        if self.is_linux:
            if event.num == 4:
                delta = -3
            elif event.num == 5:
                delta = 3
            else:
                delta = 0
        else:
            if abs(event.delta) > 10:
                delta = event.delta // 40
            else:
                delta = event.delta // 4
        
        if delta != 0:
            self.grid_canvas.yview_scroll(int(-delta), "units")
        
        self.update_visible_thumbnails()
        
        return "break"

    def show_preview(self, path, x, y):
        """Show preview popup for an image"""
        if self._destroyed:
            return
            
        if self.preview_window:
            try:
                self.preview_window.destroy()
            except tk.TclError:
                pass

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

        except (IOError, OSError) as e:
            logger.debug(f"Preview error: {e}")
            self.hide_preview()

    def hide_preview(self):
        """Hide the preview popup"""
        if self.preview_window:
            try:
                self.preview_window.destroy()
            except tk.TclError:
                pass
            self.preview_window = None


    # =========================================================================
    # SECTION 7: MEDIA VIEWING (SINGLE VIEW)
    # =========================================================================

    def open_media(self, item):
        """Open media item in single view"""
        if self._destroyed:
            return
            
        if isinstance(item, str):
            item = self.media_by_path.get(item)
        if not item:
            return

        self.current_image_path = item.path
        self.rotation_angle = 0

        try:
            self.current_index = self.media.index(item)
        except ValueError:
            self.current_index = 0

        self.show_single_view()

        self.db.update_view_stats(item.id)
        item.view_count += 1

        if item.is_video:
            if HAS_VLC:
                self.play_video(item.path)
            else:
                self._show_video_placeholder()
        else:
           self.show_image(item.path)

        if self.current_index + 1 < len(self.media):
            next_item = self.media[self.current_index + 1]
            if next_item.is_image:
                self.worker.submit("preload", lambda p=next_item.path: self._preload_image(p))

    def _preload_image(self, path):
        """Preload image for faster viewing"""
        try:
            with Image.open(path) as img:
                img.convert('RGB')
        except (IOError, OSError):
            pass

    def show_image(self, path):
        """Display image in single view"""
        if self._destroyed:
            return
            
        self.image_canvas.delete("all")

        if self.original_image:
            try:
                self.original_image.close()
            except Exception:
                pass

        self.canvas_image_id = None
        self.original_image = None
        self.zoom_cache = OrderedDict()
        self.zoom_level = 1.0
        self.pan_x = 0
        self.pan_y = 0

        self.video_frame.pack_forget()
        if HAS_VLC and hasattr(self, 'video_controls'):
            self.video_controls.pack_forget()
        self.image_canvas.pack(fill=tk.BOTH, expand=True)

        try:
            self.original_image = Image.open(path).convert('RGB')
            self.original_image = ImageOps.exif_transpose(self.original_image)
        except (IOError, OSError) as e:
            logger.error(f"Error loading image {path}: {e}")
            self.image_canvas.create_text(
                self.image_canvas.winfo_width()//2, 
                self.image_canvas.winfo_height()//2,
                text="💔 Failed to load image", font=self.font_title,
                fill=self.colors['danger']
            )
            return

        self.media_container.update_idletasks()
        self.root.after(10, self.reset_zoom)

        item = self.media_by_path.get(path)
        if item:
            self.filename_label.config(text=item.filename)
            size_str = f"{item.width or '?'}x{item.height or '?'}"
            self.details_label.config(text=f"{size_str} • {item.format_size()}")
            self.fav_btn.config(fg=self.colors['favorite'] if item.favorite else self.colors['text'])

    def play_video(self, path):
        """Play video using VLC"""
        if self._destroyed:
            return
            
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

            if HAS_VLC and hasattr(self, 'video_controls'):
                self.video_controls.pack(fill=tk.X, pady=10, padx=20)
            self.update_video_timeline()

    def _attach_vlc_window(self):
        """Attach VLC player to Tkinter window"""
        if HAS_VLC and not self.vlc_attached:
            try:
                if self.is_windows:
                    self.vlc_player.set_hwnd(self.video_frame.winfo_id())
                elif self.is_linux:
                    try:
                        self.vlc_player.set_xwindow(self.video_frame.winfo_id())
                    except Exception:
                        pass
                else:
                    self.vlc_player.set_xwindow(self.video_frame.winfo_id())
                self.vlc_attached = True
            except Exception as e:
                logger.error(f"VLC attach error: {e}")

    def _show_video_placeholder(self):
        """Show placeholder when VLC is not available"""
        self.image_canvas.delete("all")
        self.image_canvas.create_text(
            self.image_canvas.winfo_width()//2,
            self.image_canvas.winfo_height()//2,
            text="🎬 VLC not available\\nInstall python-vlc for video playback",
            font=self.font_title,
            fill=self.colors['text_secondary'],
            justify="center"
        )

    # =========================================================================
    # SECTION 8: IMAGE MANIPULATION
    # =========================================================================

    def render_zoomed_image(self):
        """Render image at current zoom level"""
        if not self.original_image or self._destroyed:
            return

        try:
            if not self.image_canvas.winfo_exists():
                return
        except tk.TclError:
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

        canvas_w = self.image_canvas.winfo_width()
        canvas_h = self.image_canvas.winfo_height()
        x = canvas_w // 2 + self.pan_x
        y = canvas_h // 2 + self.pan_y

        if self.canvas_image_id:
            try:
                self.image_canvas.itemconfig(self.canvas_image_id, image=self.current_photo)
                self.image_canvas.coords(self.canvas_image_id, x, y)
                return
            except tk.TclError:
                self.canvas_image_id = None

        self.canvas_image_id = self.image_canvas.create_image(
            x, y, image=self.current_photo, anchor="center"
        )

    def reset_zoom(self, event=None):
        """Reset zoom to fit image in window"""
        if not self.original_image or self._destroyed:
            return

        container_w = self.image_canvas.winfo_width()
        container_h = self.image_canvas.winfo_height()

        if container_w <= 1 or container_h <= 1:
            self.root.after(50, self.reset_zoom)
            return

        scale_w = container_w / self.original_image.width
        scale_h = container_h / self.original_image.height

        self.zoom_level = min(scale_w, scale_h)
        self.pan_x = 0
        self.pan_y = 0
        self.zoom_cache.clear()
        self.canvas_image_id = None

        self.render_zoomed_image()

    def zoom_image(self, event):
        """Handle zoom in/out"""
        if not self.original_image or self._destroyed:
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
        """Start panning image"""
        self.is_panning = True
        self.pan_start_x = event.x
        self.pan_start_y = event.y
        self.image_canvas.config(cursor="fleur")

    def pan_image(self, event):
        """Pan image during drag"""
        if not self.is_panning or self.canvas_image_id is None or self._destroyed:
            return

        dx = event.x - self.pan_start_x
        dy = event.y - self.pan_start_y

        self.image_canvas.move(self.canvas_image_id, dx, dy)

        self.pan_start_x = event.x
        self.pan_start_y = event.y

    def end_pan(self, event):
        """End panning"""
        self.is_panning = False
        self.image_canvas.config(cursor="plus")

    def double_click_zoom(self, event):
        """Toggle zoom on double click"""
        if self.zoom_level > 1.5:
            self.reset_zoom()
        else:
            self.zoom_level = 2.5
            self.render_zoomed_image()

    def rotate_left(self):
        """Rotate image left 90 degrees"""
        if not self.original_image or not self.current_image_path or self._destroyed:
            return
        self.rotation_angle = (self.rotation_angle - 90) % 360
        self._apply_rotation()

    def rotate_right(self):
        """Rotate image right 90 degrees"""
        if not self.original_image or not self.current_image_path or self._destroyed:
            return
        self.rotation_angle = (self.rotation_angle + 90) % 360
        self._apply_rotation()

    def _apply_rotation(self):
        """Apply rotation to image"""
        if not self.original_image or self._destroyed:
            return
        
        try:
            self.original_image = self.original_image.rotate(self.rotation_angle, expand=True)
            self.zoom_cache.clear()
            self.canvas_image_id = None
            self.zoom_level = 1.0
            self.pan_x = 0
            self.pan_y = 0
            self.render_zoomed_image()
            self.toast.show(f"Rotated {self.rotation_angle}°", emoji="↻")
        except Exception as e:
            logger.error(f"Rotation error: {e}")

    # =========================================================================
    # SECTION 9: VIDEO PLAYBACK
    # =========================================================================

    def toggle_video_playback(self, event=None):
        """Toggle video play/pause"""
        if not HAS_VLC or not self.vlc_player or self._destroyed:
            return
        if self.vlc_player.is_playing():
            self.vlc_player.pause()
            if hasattr(self, 'play_btn'):
                self.play_btn.config(text="▶")
        else:
            self.vlc_player.play()
            if hasattr(self, 'play_btn'):
                self.play_btn.config(text="⏸")
            self.update_video_timeline()

    def update_video_timeline(self):
        """Update video timeline position"""
        if self.video_timeline_after_id is not None:
            try:
                self.root.after_cancel(self.video_timeline_after_id)
            except Exception:
                pass
            self.video_timeline_after_id = None

        if not HAS_VLC or not self.vlc_player or not self.vlc_player.is_playing() or self._destroyed:
            return

        try:
            pos = self.vlc_player.get_position() * 100
            if hasattr(self, 'timeline'):
                self.timeline.set(pos)

            length = self.vlc_player.get_length() / 1000
            current = self.vlc_player.get_time() / 1000
            if length > 0 and hasattr(self, 'time_label'):
                self.time_label.config(text=f"{int(current//60)}:{int(current%60):02d} / {int(length//60)}:{int(length%60):02d}")
        except Exception:
            pass

        self.video_timeline_after_id = self.root.after(500, self.update_video_timeline)

    def seek_video(self, event):
        """Seek video to position"""
        if HAS_VLC and self.vlc_player and hasattr(self, 'timeline'):
            pos = self.timeline.get()
            self.vlc_player.set_position(pos / 100.0)

    # =========================================================================
    # SECTION 10: SLIDESHOW - FIXED VERSION
    # =========================================================================
    
    def toggle_slideshow(self):
        """Toggle slideshow mode - FIXED"""
        if self._destroyed:
            return
            
        if self.slideshow_active:
            self.stop_slideshow()
        else:
            self.start_slideshow()

    def start_slideshow(self):
        """Start slideshow - FIXED"""
        if self._destroyed:
            return
            
        image_items = [m for m in self.media if m.is_image]
        
        if not image_items:
            self.toast.show("No images to display in slideshow", emoji="⚠️")
            return

        self.slideshow_active = True
        self.slideshow_btn.config(bg=self.colors['accent'])

        # FIX: Use pack_forget/pack instead of place_forget/place
        self.grid_frame.pack_forget()
        self.single_frame.pack_forget()
        self.slideshow_frame.pack(fill=tk.BOTH, expand=True)

        self.slideshow_items = image_items
        self.slideshow_index = 0
        
        self.show_slideshow_image()

    def stop_slideshow(self):
        """Stop slideshow"""
        if not self.slideshow_active or self._destroyed:
            return

        self.slideshow_active = False
        self.slideshow_btn.config(bg=self.colors['surface'])

        if self.slideshow_after_id is not None:
            try:
                self.root.after_cancel(self.slideshow_after_id)
            except Exception:
                pass
            self.slideshow_after_id = None

        self.slideshow_items = []
        self.slideshow_index = 0

        self.show_grid_view()

    def show_slideshow_image(self):
        """Show next slideshow image - FIXED"""
        if not self.slideshow_active or not getattr(self, 'slideshow_items', []) or self._destroyed:
            return

        item = self.slideshow_items[self.slideshow_index]

        try:
            with Image.open(item.path) as img:
                img = img.convert('RGB')
                img = ImageOps.exif_transpose(img)

                # FIX: Get actual frame dimensions
                self.slideshow_frame.update_idletasks()
                screen_w = self.slideshow_frame.winfo_width()
                screen_h = self.slideshow_frame.winfo_height()
                
                # Use fallback if not ready
                if screen_w < 100:
                    screen_w = self.root.winfo_width()
                if screen_h < 100:
                    screen_h = self.root.winfo_height()
                    
                # FIX: Proper thumbnail sizing
                img.thumbnail((screen_w - 40, screen_h - 40), Image.Resampling.LANCZOS)

                photo = ImageTk.PhotoImage(img)
                self.slideshow_label.config(image=photo)
                self.slideshow_label.image = photo

        except (IOError, OSError) as e:
            logger.error(f"Slideshow image error: {e}")
            self.slideshow_index = (self.slideshow_index + 1) % len(self.slideshow_items)
            self.slideshow_after_id = self.root.after(100, self.show_slideshow_image)
            return

        self.slideshow_index = (self.slideshow_index + 1) % len(self.slideshow_items)
        self.slideshow_after_id = self.root.after(Config.SLIDESHOW_INTERVAL_MS, self.show_slideshow_image)

    # =========================================================================
    # SECTION 11: NAVIGATION - FIXED VERSION
    # =========================================================================

    def show_grid_view(self):
        """Show grid view with proper cleanup and state reset - FIXED"""
        if self._destroyed:
            return
            
        self.view_mode = ViewMode.GRID
        
        # Stop any active media
        self.hide_preview()
        self.stop_slideshow()
        
        # Stop video playback
        if HAS_VLC and self.vlc_player:
            try:
                self.vlc_player.stop()
                self.video_frame.pack_forget()
                if hasattr(self, 'video_controls'):
                    self.video_controls.pack_forget()
            except Exception as e:
                logger.debug(f"Video cleanup error: {e}")
        
        # Reset image view state
        self.zoom_level = 1.0
        self.pan_x = 0
        self.pan_y = 0
        self.rotation_angle = 0
        
        # Clear any loaded image
        if self.original_image:
            try:
                self.original_image.close()
            except Exception:
                pass
            self.original_image = None
        
        # FIX: Use pack_forget/pack for all frames
        self.single_frame.pack_forget()
        self.slideshow_frame.pack_forget()
        
        self.grid_frame.pack(fill=tk.BOTH, expand=True)
        
        # Force thumbnail refresh
        self._clear_all_thumbnails()
        self.root.after(100, self.refresh_grid)

    def show_single_view(self):
        """Show single media view - FIXED"""
        if self._destroyed:
            return
            
        self.view_mode = ViewMode.SINGLE
        
        self.grid_frame.pack_forget()
        self.slideshow_frame.pack_forget()
        
        self.single_frame.pack(fill=tk.BOTH, expand=True)
        
        self.single_frame.focus_set()

    def show_all_photos(self):
        """Show all photos (clear filters) - FIXED"""
        if self._destroyed:
            return
            
        self.showing_favorites = False
        self.showing_videos_only = False
        self.showing_deleted = False
        self.showing_album = None
        self.showing_tag = None

        self.clear_selection()
        self._clear_all_thumbnails()
        self.apply_filters()

    def show_trash(self):
        """Show trash/deleted items - FIXED"""
        if self._destroyed:
            return
            
        self.showing_favorites = False
        self.showing_videos_only = False
        self.showing_deleted = True
        self.showing_album = None
        self.showing_tag = None
        
        self.clear_selection()
        self._clear_all_thumbnails()
        self.apply_filters()

    def show_album(self, album_id):
        """Show album contents - FIXED"""
        if self._destroyed:
            return
            
        self.showing_favorites = False
        self.showing_videos_only = False
        self.showing_deleted = False
        self.showing_album = album_id
        self.showing_tag = None
        
        self.clear_selection()
        self._clear_all_thumbnails()
        self.apply_filters()

    def show_tag_filter(self, tag_id):
        """Show items with specific tag - FIXED"""
        if self._destroyed:
            return
            
        self.showing_favorites = False
        self.showing_videos_only = False
        self.showing_deleted = False
        self.showing_album = None
        self.showing_tag = tag_id
        
        self.clear_selection()
        self._clear_all_thumbnails()
        self.apply_filters()

    def show_duplicates(self):
        """Show duplicate items - FIXED"""
        if self._destroyed:
            return
            
        duplicates = self.db.get_duplicates()
        if not duplicates:
            self.toast.show("No duplicates found!", emoji="✨")
            return

        duplicate_hashes = {d['sha256'] for d in duplicates}
        self.all_media = [m for m in self.all_media if m.sha256 in duplicate_hashes]
        self._clear_all_thumbnails()
        self.apply_filters()
        self.toast.show(f"Found {len(duplicates)} duplicate groups", emoji="🔍")

    def prev_media(self):
        """Go to previous media"""
        if self._destroyed:
            return
            
        if self.current_index > 0:
            self.current_index -= 1
            self.open_media(self.media[self.current_index])

    def next_media(self):
        """Go to next media"""
        if self._destroyed:
            return
            
        if self.current_index < len(self.media) - 1:
            self.current_index += 1
            self.open_media(self.media[self.current_index])

    def toggle_fullscreen(self):
        """Toggle fullscreen mode"""
        if self._destroyed:
            return
            
        self.fullscreen = not self.fullscreen
        self.root.attributes('-fullscreen', self.fullscreen)
        if not self.fullscreen:
            self.show_grid_view()

    # =========================================================================
    # SECTION 12: USER ACTIONS
    # =========================================================================

    def toggle_favorite_current(self):
        """Toggle favorite status of current item"""
        if self._destroyed or not self.media or self.current_index >= len(self.media):
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
        if self._destroyed or not self.media or self.current_index >= len(self.media):
            return

        item = self.media[self.current_index]
        self.db.set_rating(item.id, rating)
        item.rating = rating

        stars = "★" * rating + "☆" * (5 - rating)
        self.toast.show(f"Rated {stars}")

    def delete_current(self):
        """Move current item to trash"""
        if self._destroyed or not self.media or self.current_index >= len(self.media):
            return

        item = self.media[self.current_index]

        if not messagebox.askyesno("Confirm Delete", 
                                   f"Move '{item.filename}' to Recently Deleted?\\n\\n"
                                   f"Items are permanently removed after {Config.TRASH_RETENTION_DAYS} days."):
            return

        success, result = self.db.soft_delete_media(item.id, str(self.trash_dir))

        if success:
            self.toast.show(f"Moved to trash", emoji="🗑️")
            item.soft_delete = True
            item.deleted_at = datetime.now()
            item.path = result
            self.media_by_path[item.path] = item

            self.apply_filters()
            
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
        if self._destroyed or not self.media or self.current_index >= len(self.media):
            return

        item = self.media[self.current_index]

        if not messagebox.askyesno("Confirm Permanent Delete", 
                                   f"Permanently delete '{item.filename}'?\\n\\n"
                                   f"This action cannot be undone!"):
            return

        success, result = self.db.permanently_delete(item.id)

        if success:
            self.toast.show(f"Permanently deleted", emoji="💀")

            if item.id in self.media_by_id:
                del self.media_by_id[item.id]
            if item.path in self.media_by_path:
                del self.media_by_path[item.path]

            self.apply_filters()

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
        if self._destroyed or not self.media or self.current_index >= len(self.media):
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

            self.apply_filters()

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

    def copy_current_path(self):
        """Copy current item path to clipboard"""
        if self._destroyed or not self.media or self.current_index >= len(self.media):
            return

        item = self.media[self.current_index]
        self.root.clipboard_clear()
        self.root.clipboard_append(item.path)
        self.toast.show("Path copied to clipboard", emoji="📋")

    def open_current_folder(self):
        """Open folder containing current item"""
        if self._destroyed or not self.media or self.current_index >= len(self.media):
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
        """Show EXIF information for current image"""
        if self._destroyed or not self.media or self.current_index >= len(self.media):
            return

        item = self.media[self.current_index]
        if item.is_video:
            return

        exif_data = self.exif_reader.read_exif(item.path)

        if not exif_data:
            messagebox.showinfo("EXIF Data", "No EXIF data available for this image.")
            return

        info_text = "📷 Image Information\\n" + "=" * 30 + "\\n\\n"
        for key, value in exif_data.items():
            info_text += f"{key}: {value}\\n"

        messagebox.showinfo("EXIF Data", info_text)

    def tag_current(self):
        """Add tag to current item"""
        if self._destroyed or not self.media or self.current_index >= len(self.media):
            return
        
        item = self.media[self.current_index]
        current_tags = self.db.get_tags_for_media(item.id)
        current_tag_names = [t['name'] for t in current_tags]
        
        tag_name = simpledialog.askstring("Tag", f"Current tags: {', '.join(current_tag_names) or 'None'}\\n\\nEnter new tag:")
        if tag_name:
            self.db.add_tag_to_media(item.id, tag_name)
            self.refresh_tags_list()
            self.toast.show(f"Tagged: {tag_name}", emoji="🏷️")

    # =========================================================================
    # SECTION 13: BATCH OPERATIONS
    # =========================================================================

    def batch_favorite(self):
        """Favorite all selected items"""
        if self._destroyed or not self.selected_items:
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

    def batch_tag(self):
        """Tag all selected items"""
        if self._destroyed or not self.selected_items:
            self.toast.show("No items selected", emoji="⚠️")
            return
        
        tag_name = simpledialog.askstring("Tag", "Enter tag name:")
        if not tag_name:
            return
        
        for item_id in self.selected_items:
            self.db.add_tag_to_media(item_id, tag_name)
        
        self.refresh_tags_list()
        self.toast.show(f"Tagged {len(self.selected_items)} items", emoji="🏷️")

    def batch_add_to_album(self):
        """Add selected items to album"""
        if self._destroyed or not self.selected_items:
            self.toast.show("No items selected", emoji="⚠️")
            return
        
        albums = self.db.get_all_albums()
        if not albums:
            self.toast.show("No albums exist. Create one first!", emoji="⚠️")
            return
        
        album_names = [a['name'] for a in albums]
        dialog = tk.Toplevel(self.root)
        dialog.title("Select Album")
        dialog.geometry("300x400")
        dialog.transient(self.root)
        
        tk.Label(dialog, text="Select an album:", font=self.font_bold).pack(pady=10)
        
        listbox = tk.Listbox(dialog, font=self.font_main, height=10)
        for name in album_names:
            listbox.insert(tk.END, name)
        listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        def on_select():
            selection = listbox.curselection()
            if selection:
                album_id = albums[selection[0]]['id']
                for item_id in self.selected_items:
                    self.db.add_media_to_album(album_id, item_id)
                self.toast.show(f"Added to album", emoji="📔")
                dialog.destroy()
        
        tk.Button(dialog, text="Add", command=on_select, font=self.font_bold).pack(pady=10)

    def batch_delete(self):
        """Delete all selected items"""
        if self._destroyed or not self.selected_items:
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
        if self._destroyed or not self.selected_items:
            self.toast.show("No items selected", emoji="⚠️")
            return

        export_type = messagebox.askyesnocancel(
            "Export", 
            "Export to:\\n\\nYes = Folder\\nNo = ZIP file\\nCancel = Abort"
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
                    except (IOError, OSError) as e:
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
            except (IOError, OSError) as e:
                logger.error(f"ZIP export error: {e}")
                messagebox.showerror("Error", f"Export failed: {e}")

    def toggle_favorites(self):
        """Toggle favorites filter"""
        if self._destroyed:
            return
            
        self.showing_favorites = not self.showing_favorites
        self.showing_deleted = False
        self.showing_album = None
        self.showing_tag = None

        if self.showing_favorites:
            self.fav_filter_btn.config(bg=self.colors['favorite'])
        else:
            self.fav_filter_btn.config(bg=self.colors['surface'])

        self.clear_selection()
        self._clear_all_thumbnails()
        self.apply_filters()

    def toggle_video_filter(self):
        """Toggle videos only filter"""
        if self._destroyed:
            return
            
        self.showing_videos_only = not self.showing_videos_only
        self.showing_favorites = False
        self.showing_deleted = False
        self.showing_album = None
        self.showing_tag = None

        if self.showing_videos_only:
            self.video_filter_btn.config(bg=self.colors['video'])
        else:
            self.video_filter_btn.config(bg=self.colors['surface'])

        self._clear_all_thumbnails()
        self.apply_filters()

    # =========================================================================
    # SECTION 14: DIALOGS & WINDOWS
    # =========================================================================

    def create_new_album(self):
        """Create new album dialog"""
        if self._destroyed:
            return
            
        name = simpledialog.askstring("New Album", "Enter album name:")
        if not name:
            return
        
        description = simpledialog.askstring("Description", "Enter description (optional):")
        
        album_id = self.db.create_album(name, description)
        self.refresh_albums_list()
        self.toast.show(f"Created album: {name}", emoji="📔")

    def show_tag_manager(self):
        """Show tag manager dialog"""
        if self._destroyed:
            return
            
        dialog = tk.Toplevel(self.root)
        dialog.title("Tag Manager")
        dialog.geometry("400x500")
        dialog.transient(self.root)
        dialog.config(bg=self.colors['bg'])
        
        tk.Label(dialog, text="🏷️ All Tags", font=self.font_title, 
                bg=self.colors['bg'], fg=self.colors['text']).pack(pady=10)
        
        tags = self.db.get_all_tags()
        
        canvas = tk.Canvas(dialog, bg=self.colors['bg'], highlightthickness=0)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=self.colors['bg'])
        
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        for tag in tags:
            frame = tk.Frame(scroll_frame, bg=self.colors['surface'], padx=5, pady=5)
            frame.pack(fill=tk.X, pady=2)
            tk.Label(frame, text=tag['name'], font=self.font_main,
                    bg=self.colors['surface'], fg=self.colors['text']).pack(side=tk.LEFT)
            count = len(self.db.get_media_by_tag(tag['id']))
            tk.Label(frame, text=f"({count} items)", font=self.font_small,
                    bg=self.colors['surface'], fg=self.colors['text_secondary']).pack(side=tk.LEFT, padx=10)

    def show_album_manager(self):
        """Show album manager dialog"""
        if self._destroyed:
            return
            
        dialog = tk.Toplevel(self.root)
        dialog.title("Album Manager")
        dialog.geometry("500x500")
        dialog.transient(self.root)
        dialog.config(bg=self.colors['bg'])
        
        tk.Label(dialog, text="📔 Albums", font=self.font_title,
                bg=self.colors['bg'], fg=self.colors['text']).pack(pady=10)
        
        albums = self.db.get_all_albums()
        
        canvas = tk.Canvas(dialog, bg=self.colors['bg'], highlightthickness=0)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=self.colors['bg'])
        
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        for album in albums:
            frame = tk.Frame(scroll_frame, bg=self.colors['surface'], padx=10, pady=10)
            frame.pack(fill=tk.X, pady=5)
            
            tk.Label(frame, text=album['name'], font=self.font_bold,
                    bg=self.colors['surface'], fg=self.colors['text']).pack(anchor=tk.W)
            if album['description']:
                tk.Label(frame, text=album['description'], font=self.font_small,
                        bg=self.colors['surface'], fg=self.colors['text_secondary']).pack(anchor=tk.W)
            
            count = len(self.db.get_media_in_album(album['id']))
            tk.Label(frame, text=f"{count} items", font=self.font_small,
                    bg=self.colors['surface'], fg=self.colors['accent']).pack(anchor=tk.W)
            
            btn_frame = tk.Frame(frame, bg=self.colors['surface'])
            btn_frame.pack(anchor=tk.W, pady=(5, 0))
            
            tk.Button(btn_frame, text="View", command=lambda a=album['id']: self.show_album(a),
                     bg=self.colors['accent'], fg='white', relief='flat').pack(side=tk.LEFT, padx=2)
            tk.Button(btn_frame, text="Delete", command=lambda a=album['id'], d=dialog: self.delete_album_and_close(a, d),
                     bg=self.colors['danger'], fg='white', relief='flat').pack(side=tk.LEFT, padx=2)

    def delete_album_and_close(self, album_id, dialog):
        """Delete album and close dialog"""
        if messagebox.askyesno("Confirm", "Delete this album?"):
            self.db.delete_album(album_id)
            self.refresh_albums_list()
            dialog.destroy()
            self.show_album_manager()

    def show_preferences(self):
        """Show preferences dialog"""
        if self._destroyed:
            return
            
        dialog = tk.Toplevel(self.root)
        dialog.title("Preferences")
        dialog.geometry("400x500")
        dialog.transient(self.root)
        dialog.config(bg=self.colors['bg'])
        
        tk.Label(dialog, text="⚙️ Preferences", font=self.font_title,
                bg=self.colors['bg'], fg=self.colors['text']).pack(pady=10)
        
        frame = tk.Frame(dialog, bg=self.colors['surface'], padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        tk.Label(frame, text="Thumbnail Size:", font=self.font_main,
                bg=self.colors['surface'], fg=self.colors['text']).pack(anchor=tk.W)
        
        size_var = tk.IntVar(value=self.thumb_size)
        tk.Scale(frame, from_=80, to=300, orient=tk.HORIZONTAL, variable=size_var,
                bg=self.colors['surface'], highlightthickness=0).pack(fill=tk.X, pady=5)
        
        tk.Label(frame, text="Slideshow Interval (seconds):", font=self.font_main,
                bg=self.colors['surface'], fg=self.colors['text']).pack(anchor=tk.W, pady=(10, 0))
        
        interval_var = tk.IntVar(value=Config.SLIDESHOW_INTERVAL_MS // 1000)
        tk.Scale(frame, from_=1, to=60, orient=tk.HORIZONTAL, variable=interval_var,
                bg=self.colors['surface'], highlightthickness=0).pack(fill=tk.X, pady=5)
        
        tk.Label(frame, text="Trash Retention (days):", font=self.font_main,
                bg=self.colors['surface'], fg=self.colors['text']).pack(anchor=tk.W, pady=(10, 0))
        
        trash_var = tk.IntVar(value=Config.TRASH_RETENTION_DAYS)
        tk.Scale(frame, from_=1, to=90, orient=tk.HORIZONTAL, variable=trash_var,
                bg=self.colors['surface'], highlightthickness=0).pack(fill=tk.X, pady=5)
        
        def save_prefs():
            self.thumb_size = size_var.get()
            Config.THUMB_SIZE = self.thumb_size
            Config.SLIDESHOW_INTERVAL_MS = interval_var.get() * 1000
            Config.TRASH_RETENTION_DAYS = trash_var.get()

            self.db.set_preference('thumb_size', str(self.thumb_size))
            self.db.set_preference('slideshow_interval', str(Config.SLIDESHOW_INTERVAL_MS))
            self.db.set_preference('trash_retention', str(Config.TRASH_RETENTION_DAYS))

            self.refresh_grid()
            dialog.destroy()
            self.toast.show("Preferences saved", emoji="✅")

        tk.Button(
            dialog,
            text="Save",
            command=save_prefs,
            bg=self.colors['accent'],
            fg='white',
            font=self.font_bold,
            relief='flat',
            padx=20,
            pady=10
        ).pack(pady=20)
    
    def setup_key_hints(self):
        """Register keyboard shortcuts with visual feedback"""
        self.keys.register('f', 'Toggle favorite', lambda e: self.toggle_favorite_current())
        self.keys.register('s', 'Slideshow', lambda e: self.toggle_slideshow())
        self.keys.register('Left', 'Previous media', lambda e: self.prev_media())
        self.keys.register('Right', 'Next media', lambda e: self.next_media())
        self.keys.register('r', 'Rotate right', lambda e: self.rotate_right(), shift=True)
        self.keys.register('r', 'Rotate left', lambda e: self.rotate_left())
        self.keys.register('h', 'Show help', lambda e: self.keys.show_help())
        
    def show_shortcuts(self):
        """Show keyboard shortcuts dialog"""
        shortcuts = """
Keyboard Shortcuts:

Navigation:
← / →         Previous/Next media
Escape        Back to grid / Stop slideshow

Actions:
F             Toggle favorite
1-5           Set rating (1-5 stars)
Delete        Move to trash
Shift+Delete  Permanently delete
Space         Play/Pause video
R / Shift+R   Rotate right/left
F11           Toggle fullscreen
S             Start/Stop slideshow
Ctrl+A        Select all
Ctrl+D        Clear selection
Ctrl+C        Copy file path
Ctrl+O        Add folder
F5            Refresh
"""
        messagebox.showinfo("Keyboard Shortcuts", shortcuts)

    def show_about(self):
        """Show about dialog"""
        about_text = """Lumina Gallery Pro Max 💗

A beautiful, fast media gallery with:
• Image and video support
• Tags and albums
• Favorites and ratings
• Slideshow and fullscreen
• Duplicate detection
• Soft delete with trash recovery

Built with Python, Tkinter, and love ✨
by frankmanuebeltran_alt Github👌
"""
        messagebox.showinfo("About", about_text)

    # =========================================================================
    # SECTION 15: STATUS UPDATES
    # =========================================================================

    def update_status(self, text):
        """Update status bar text"""
        if self._destroyed:
            return
        try:
            if self.status_label.winfo_exists():
                self.status_label.config(text=text)
        except tk.TclError:
            pass

    def update_stats(self):
        """Update statistics display"""
        if self._destroyed:
            return
        try:
            stats = self.db.get_stats()
            stats_text = f"{stats['total']} photos ✨ {stats['videos']} videos 🎬 {stats['favorites']} favorites 💗"
            if stats['deleted'] > 0:
                stats_text += f" {stats['deleted']} in trash 🗑️"
            if self.stats_label.winfo_exists():
                self.stats_label.config(text=stats_text)
        except Exception as e:
            logger.error(f"Stats update error: {e}")
