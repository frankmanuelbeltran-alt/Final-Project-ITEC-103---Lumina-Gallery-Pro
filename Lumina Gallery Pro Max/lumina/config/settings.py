import os
from pathlib import Path
import enum


class Config:
    
    APP_DIR = Path.home() / ".lumina_gallery"
    APP_DIR.mkdir(parents=True, exist_ok=True)
    
    THUMB_SIZE = int(os.getenv('LUMINA_THUMB_SIZE', '160')) 
    THUMB_PADDING = int(os.getenv('LUM_PADDING', '16'))  
    THUMB_QUALITY = None  # Set to Image.Resampling.LANCZOS in main
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
    DB_PATH = os.getenv('LUMINA_DB_PATH', str(APP_DIR / 'gallery.db'))
    CACHE_DIR = os.getenv('LUMINA_CACHE_DIR', str(APP_DIR / '.cache' / 'thumbnails'))
    TRASH_DIR = str(APP_DIR / 'trash')

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

    @classmethod
    def load_preferences(cls, db_manager):
        """Load configuration from database preferences"""
        try:
            thumb_size = db_manager.get_preference('thumb_size')
            if thumb_size:
                cls.THUMB_SIZE = int(thumb_size)
            
            slideshow_interval = db_manager.get_preference('slideshow_interval')
            if slideshow_interval:
                cls.SLIDESHOW_INTERVAL_MS = int(slideshow_interval)
            
            trash_retention = db_manager.get_preference('trash_retention')
            if trash_retention:
                cls.TRASH_RETENTION_DAYS = int(trash_retention)
                
            from lumina.utils.logging_utils import logger
            logger.info("Preferences loaded from database")
        except Exception as e:
            from lumina.utils.logging_utils import logger
            logger.warning(f"Could not load preferences: {e}")


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