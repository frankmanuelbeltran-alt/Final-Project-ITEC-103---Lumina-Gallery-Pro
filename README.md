# Lumina Gallery Pro - Database Edition

A high-performance, production-ready image gallery built with Python and Tkinter. Tracks your photos, manages metadata, and helps you organize, sort, and filter your collection efficiently. Optimized for large libraries with smart hashing, duplicate detection, and favorites support.

## Features

* **Database-driven**: Uses SQLite with smart change detection to avoid unnecessary hashing.
* **Duplicate detection**: Detects identical images via SHA256 hashing.
* **Favorites & filtering**: Tag favorites and filter by favorites, duplicates, or search queries.
* **Metadata tracking**: Stores file size, modification time, image dimensions, and view counts.
* **Tagging system**: Add and edit tags for easier organization.
* **Albums support**: Group images into albums with fast queries.
* **Slideshow mode**: Auto-advance through images.
* **Sorting options**: Sort by name, date, size, views, or resolution.
* **Lazy loading thumbnails**: Loads thumbnails on demand for performance.
* **Theme system**: Light and dark modes with customizable accent colors.
* **Cross-platform**: Works on Windows, macOS, and Linux.

## Installation

1. Clone the repository:

```
git clone https://github.com/yourusername/lumina-gallery-pro.git
cd lumina-gallery-pro
```

2. Install dependencies:

```
pip install pillow
```

Optional: For memory monitoring, install `psutil`:

```
pip install psutil
```

## Usage

Run the application:

```
python main.py
```

The app will automatically scan common directories (`Pictures`, `Downloads`, `Desktop`) on first launch. You can add folders manually from the interface.

## Project Structure

* `main.py` – Entry point and UI application.
* `database_manager.py` – Handles SQLite database, smart inserts, updates, and queries.
* `gallery_ui.py` – Tkinter UI layer with grid and single-image views.
* `utils.py` – Helper functions for hashing, file scanning, and metadata extraction.

## Future Plans

* Background hashing to avoid UI freeze.
* Advanced duplicate management (auto-remove, group view).
* More theme options and custom layouts.

## License

This project is MIT licensed.
