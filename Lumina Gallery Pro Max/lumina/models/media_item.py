from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any
import os


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
    phash: Optional[str] = None

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