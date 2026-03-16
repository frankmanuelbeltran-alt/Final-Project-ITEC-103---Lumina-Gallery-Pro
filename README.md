---

```markdown
# Lumina Gallery Pro Max 💗

[![Python](https://img.shields.io/badge/Python-3.8+-ff69b4?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-ff85c1?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-ffc2db?style=flat-square)]()
[![SQLite](https://img.shields.io/badge/SQLite-3-ffb3d1?style=flat-square&logo=sqlite&logoColor=white)]()
[![Pillow](https://img.shields.io/badge/Pillow-10.0+-ff69b4?style=flat-square)]()
[![OpenCV](https://img.shields.io/badge/OpenCV-4.8+-ff4fa3?style=flat-square)]()

> A high-performance desktop media gallery built with Python and Tkinter. Designed for large photo and video collections with database-driven architecture, background workers, and intelligent caching.

<p align="center">
  <img src="https://raw.githubusercontent.com/yourusername/lumina-gallery-pro-max/main/assets/screenshot.png" alt="Lumina Gallery Pro Max Screenshot" width="800"/>
</p>

## ✨ Features

### Core Performance
- **🗄️ Database-Driven Media Index** — SQLite stores metadata, view statistics, dimensions, duration, and perceptual hashes for fast queries
- **🧠 Perceptual Duplicate Detection** — Images receive perceptual hashes using `imagehash`; compares hashes to find visually similar images
- **⚡ Intelligent Caching** — Dual-layer thumbnail caching (RAM + Disk) prevents regeneration and speeds up large galleries
- **🔄 Background Processing** — Directory scanning runs in worker threads; UI remains responsive with thousands of files
- **👁️ Lazy Thumbnail Loading** — Only visible viewport thumbnails load, improving performance for large libraries

### Media Management
- **💗 Favorites System** — Mark photos as favorites and filter instantly
- **🎬 Video Support** — Common formats with VLC bindings and timeline controls
- **🏷️ Tags & Albums** — Organize media with custom tags and album collections
- **🔍 Smart Search** — Filename filtering with fuzzy matching support
- **📊 Metadata Tracking** — File size, modification time, resolution, duration, view counts

### User Experience
- **🎀 Modern Coquette Interface** — Soft pink UI theme with hover animations and emoji indicators
- **⌨️ Keyboard Shortcuts** — Full keyboard navigation and control
- **🖱️ Drag & Drop** — Import folders directly into the application
- **🗑️ Soft Delete** — Trash recovery with configurable retention

## 🚀 Installation

### Prerequisites
- Python 3.8 or higher
- pip package manager

### Clone & Install

```bash
git clone https://github.com/yourusername/lumina-gallery-pro-max.git
cd lumina-gallery-pro-max
```

### Required Dependencies

```bash
pip install pillow opencv-python imagehash
```

### Optional Dependencies

**Video Playback:**
```bash
pip install python-vlc
```

**System Monitoring:**
```bash
pip install psutil
```

**Fuzzy Search (Recommended):**
```bash
pip install rapidfuzz
```

## 🎮 Usage

### Running the Application

```bash
python main.py
```

### First Launch
On first run, the application loads media from the internal database. If empty, it automatically scans common directories:
- `~/Pictures`
- `~/Videos`
- `~/Downloads`
- `~/Desktop`

### Adding Media
- Click **📂 Add Folder** button
- Drag and drop folders into the window
- Use `Ctrl+O` keyboard shortcut

## 📋 Supported Formats

| Images | Videos |
|--------|--------|
| `.jpg` `.jpeg` | `.mp4` |
| `.png` | `.mov` |
| `.webp` | `.mkv` |
| `.gif` | `.webm` |
| `.bmp` | `.avi` |
| `.tiff` | `.m4v` |

## ⌨️ Keyboard Shortcuts

| Action | Shortcut |
|--------|----------|
| Previous Media | `←` Left Arrow |
| Next Media | `→` Right Arrow |
| Toggle Favorite | `F` |
| Set Rating | `1` - `5` |
| Play/Pause Video | `Space` |
| Return to Grid | `Esc` |
| Toggle Slideshow | `S` |
| Rotate Right | `R` |
| Rotate Left | `Shift+R` |
| Fullscreen | `F11` |
| Select All | `Ctrl+A` |
| Clear Selection | `Ctrl+D` |
| Copy File Path | `Ctrl+C` |
| Add Folder | `Ctrl+O` |
| Refresh | `F5` |

## 🏗️ Architecture

Lumina Gallery uses several optimizations to handle large collections:

| Component | Technology | Purpose |
|-----------|------------|---------|
| **Database** | SQLite + WAL Mode | Metadata storage, indexed queries |
| **Thumbnail Cache** | Disk + RAM (LRU) | Persistent & fast preview loading |
| **Background Workers** | ThreadPoolExecutor | Non-blocking directory scanning |
| **Thread Safety** | TkQueue + RLock | Safe UI updates from worker threads |
| **Perceptual Hashing** | imagehash (pHash) | Visual similarity detection |
| **Video Processing** | OpenCV + VLC | Frame extraction and playback |

## 📁 Project Structure

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

## 🧩 Main Components

| Class | Responsibility |
|-------|--------------|
| `DatabaseManager` | SQLite schema, metadata storage, similarity queries |
| `ThumbnailCache` | RAM and disk cached thumbnails |
| `BackgroundWorker` | Heavy tasks (directory scanning) without UI blocking |
| `TkQueue` | Thread-safe communication between workers and Tkinter |
| `ThumbnailLoader` | Priority-based async thumbnail generation |
| `LuminaGalleryProMax` | Main UI controller and gallery interface |

## 🛣️ Roadmap

- [ ] Advanced duplicate management view
- [ ] AI-based image tagging
- [ ] Album management UI improvements
- [ ] Batch metadata editing
- [ ] Enhanced video timeline controls
- [ ] Additional UI themes
- [ ] Export to cloud storage
- [ ] Face recognition grouping

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Built with [Python](https://python.org) and [Tkinter](https://docs.python.org/3/library/tkinter.html)
- Image processing powered by [Pillow](https://python-pillow.org/) and [OpenCV](https://opencv.org/)
- Perceptual hashing by [imagehash](https://github.com/JohannesBuchner/imagehash)
- Video playback via [python-vlc](https://github.com/oaubert/python-vlc)

---

<p align="center">
  Built with 💗 by <a href="https://github.com/frankmanuelbeltran-alt">@yourusername</a>
</p>
```

---

## Key Improvements Made (Pre → Post Update):

| Aspect | Before | After |
|--------|--------|-------|
| **Badges** | None | Added 6 dynamic Shields.io badges for Python version, license, platform, SQLite, Pillow, OpenCV |
| **Visual** | Plain text | Centered screenshot placeholder, emoji headers, table layouts |
| **Structure** | Simple list | Organized sections with clear hierarchy (Features → Installation → Usage → Architecture) |
| **Documentation** | Basic description | Comprehensive tables for shortcuts, formats, components, and architecture |
| **Professional Elements** | Missing | Added Roadmap, Contributing guidelines, Acknowledgments, License section |
| **Formatting** | Plain markdown | Proper code blocks, tables, horizontal rules, centered footer |
