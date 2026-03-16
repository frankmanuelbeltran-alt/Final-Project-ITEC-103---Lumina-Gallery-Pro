---

# Lumina Gallery Pro Max

[![Python](https://img.shields.io/badge/Python-3.8+-ff69b4?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-ff85c1?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-ffc2db?style=flat-square)]()

A high-performance desktop media gallery built with Python and Tkinter. Designed for large photo and video collections with database-driven architecture, background workers, and intelligent caching.

## Features

**Core Performance**
- Database-Driven Media Index - SQLite stores metadata, view statistics, dimensions, duration, and perceptual hashes for fast queries
- Perceptual Duplicate Detection - Images receive perceptual hashes using imagehash
- Intelligent Caching - Dual-layer thumbnail caching (RAM + Disk)
- Background Processing - Directory scanning runs in worker threads
- Lazy Thumbnail Loading - Only visible viewport thumbnails load

**Media Management**
- Favorites System - Mark photos as favorites and filter instantly
- Video Support - Common formats with VLC bindings and timeline controls
- Tags & Albums - Organize media with custom tags and album collections
- Smart Search - Filename filtering with fuzzy matching support
- Metadata Tracking - File size, modification time, resolution, duration, view counts

**User Experience**
- Modern Coquette Interface - Soft pink UI theme with hover animations
- Keyboard Shortcuts - Full keyboard navigation and control
- Drag & Drop - Import folders directly into the application
- Soft Delete - Trash recovery with configurable retention

## Installation

**Prerequisites**
- Python 3.8 or higher
- pip package manager

**Clone & Install**

```
git clone https://github.com/frankmanuelbeltran_alt/lumina-gallery-pro-max.git
cd lumina-gallery-pro-max
```

**Required Dependencies**

```
pip install pillow opencv-python imagehash
```

**Optional Dependencies**

Video Playback:
```
pip install python-vlc
```

System Monitoring:
```
pip install psutil
```

Fuzzy Search (Recommended):
```
pip install rapidfuzz
```

## Usage

**Running the Application**

```
python main.py
```

**First Launch**
On first run, the application loads media from the internal database. If empty, it automatically scans common directories:
- ~/Pictures
- ~/Videos
- ~/Downloads
- ~/Desktop

**Adding Media**
- Click Add Folder button
- Drag and drop folders into the window
- Use Ctrl+O keyboard shortcut

## Supported Formats

**Images:** .jpg .jpeg .png .webp .gif .bmp .tiff

**Videos:** .mp4 .mov .mkv .webm .avi .m4v

## Keyboard Shortcuts

| Action | Shortcut |
|--------|----------|
| Previous Media | Left Arrow |
| Next Media | Right Arrow |
| Toggle Favorite | F |
| Set Rating | 1-5 |
| Play/Pause Video | Space |
| Return to Grid | Esc |
| Toggle Slideshow | S |
| Rotate Right | R |
| Rotate Left | Shift+R |
| Fullscreen | F11 |
| Select All | Ctrl+A |
| Clear Selection | Ctrl+D |
| Copy File Path | Ctrl+C |
| Add Folder | Ctrl+O |
| Refresh | F5 |

## Architecture

| Component | Technology | Purpose |
|-----------|------------|---------|
| Database | SQLite + WAL Mode | Metadata storage, indexed queries |
| Thumbnail Cache | Disk + RAM (LRU) | Persistent & fast preview loading |
| Background Workers | ThreadPoolExecutor | Non-blocking directory scanning |
| Thread Safety | TkQueue + RLock | Safe UI updates from worker threads |
| Perceptual Hashing | imagehash (pHash) | Visual similarity detection |
| Video Processing | OpenCV + VLC | Frame extraction and playback |

## Project Structure

```
lumina-gallery-pro-max/
├── main.py                 # Application entry point
├── gallery.db              # SQLite database (auto-created)
├── .cache/
│   └── thumbnails/         # Disk thumbnail cache
├── .lumina_trash/          # Soft delete storage
├── lumina_gallery_pro_max.log  # Application logs
└── README.md
```

## Main Components

| Class | Responsibility |
|-------|--------------|
| DatabaseManager | SQLite schema, metadata storage, similarity queries |
| ThumbnailCache | RAM and disk cached thumbnails |
| BackgroundWorker | Heavy tasks (directory scanning) without UI blocking |
| TkQueue | Thread-safe communication between workers and Tkinter |
| ThumbnailLoader | Priority-based async thumbnail generation |
| LuminaGalleryProMax | Main UI controller and gallery interface |

## Roadmap

- [ ] Advanced duplicate management view
- [ ] AI-based image tagging
- [ ] Album management UI improvements
- [ ] Batch metadata editing
- [ ] Enhanced video timeline controls
- [ ] Additional UI themes
- [ ] Export to cloud storage
- [ ] Face recognition grouping

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (git checkout -b feature/AmazingFeature)
3. Commit your changes (git commit -m 'Add some AmazingFeature')
4. Push to the branch (git push origin feature/AmazingFeature)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Built with Python and Tkinter
- Image processing powered by Pillow and OpenCV
- Perceptual hashing by imagehash
- Video playback via python-vlc

---

Built with love by frankmanuelbeltran_alt
