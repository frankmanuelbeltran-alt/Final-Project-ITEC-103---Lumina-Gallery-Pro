import hashlib
import gc
from pathlib import Path
from collections import OrderedDict
import threading

from PIL import Image

from lumina.config import Config
from lumina.utils.logging_utils import logger


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
            except (IOError, OSError) as e:
                logger.warning(f"Failed to load cached thumbnail: {e}")
                return None
        return None

    def put(self, content_hash, pil_image):
        cache_path = self._get_cache_path(content_hash)
        try:
            pil_image.save(cache_path, "JPEG", quality=85, optimize=True)
        except (IOError, OSError) as e:
            logger.warning(f"Cache save error: {e}")

        self._add_to_ram(content_hash, pil_image.copy())

    def _add_to_ram(self, content_hash, pil_image):
        with self.lock:
            if content_hash in self.ram_cache:
                self.ram_cache.move_to_end(content_hash)
                return

            while len(self.ram_cache) >= Config.MAX_RAM_CACHE:
                _, old_img = self.ram_cache.popitem(last=False)
                try:
                    old_img.close()
                except Exception as e:
                    logger.debug(f"Silent error: {e}")

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