Lumina Gallery Pro Max 💗

A high performance desktop media gallery built with Python and Tkinter. Designed for large photo and video collections. Uses a database driven architecture, background workers, and intelligent caching to keep the interface responsive even with thousands of files. Includes perceptual hashing, duplicate detection, video playback, and favorites management inside a soft coquette themed interface.

Lumina focuses on speed, organization, and simplicity while keeping a visually pleasant experience.

Features

• Database driven media index
SQLite stores metadata, view statistics, dimensions, duration, and perceptual hashes for fast queries.

• Perceptual duplicate detection
Images receive perceptual hashes using imagehash. The system compares hashes and shows visually similar images.

• Favorites system 💗
Mark photos as favorites and filter your gallery instantly.

• Video support 🎬
Supports common video formats and plays them using VLC bindings with timeline controls.

• Smart metadata tracking
Stores file size, modification time, resolution, duration, and view counts.

• Thumbnail caching
Disk and RAM caching prevents repeated thumbnail generation and speeds up large galleries.

• Background scanning
Directory scanning runs in worker threads so the interface remains responsive.

• Lazy thumbnail loading
Only thumbnails inside the visible viewport load. This improves performance for large libraries.

• Search system 🔍
Search images and videos using filename filtering.

• Sorting options ✨

Sort media by
• date
• name
• size
• view count

• Filtering tools

Filter your library by
• favorites
• videos only
• search query

• Drag and drop folders

Drop a directory directly into the application to import media.

• Modern coquette interface 🎀

Soft pink UI theme with hover animations and emoji indicators.

• Cross platform

Runs on
• Windows
• macOS
• Linux

Installation

Clone the repository

git clone https://github.com/yourusername/lumina-gallery-pro-max.git
cd lumina-gallery-pro-max

Install dependencies

pip install pillow opencv-python imagehash

Optional dependencies

Video playback

pip install python-vlc

System monitoring

pip install psutil
Running the Application

Run the gallery

python main.py

On first launch the application loads media from the internal database.
If the database contains no media the app scans common directories such as

• Pictures
• Videos
• Downloads
• Desktop

You can add additional folders through the Add Folder 📂 button or by dragging folders into the window.

Supported Formats

Images

• jpg
• jpeg
• png
• webp
• gif
• bmp
• tiff

Videos

• mp4
• mov
• mkv
• webm
• avi
• m4v

Keyboard Shortcuts

Navigation

• Left Arrow
Previous media

• Right Arrow
Next media

Video

• Space
Play or pause video

Gallery

• Esc
Return to grid view

Favorites

• F
Toggle favorite status 💗

Performance Architecture

Lumina Gallery uses several optimizations to handle large collections.

• Background worker threads for directory scanning
• SQLite indexed queries for metadata retrieval
• Disk thumbnail cache for persistent previews
• RAM thumbnail cache for frequently viewed images
• Lazy thumbnail loading based on viewport position
• Perceptual hashing for similarity detection

These systems allow the application to manage thousands of files without freezing the interface.

Project Structure
main.py
gallery.db
.cache/
    thumbnails/

Main components inside the application

• DatabaseManager
Handles SQLite schema, metadata storage, and similarity queries.

• ThumbnailCache
Manages RAM and disk cached thumbnails.

• BackgroundWorker
Executes heavy tasks such as directory scanning without blocking the UI.

• TkQueue
Thread safe communication between worker threads and the Tkinter interface.

• CoquetteGalleryApp
Main UI controller and gallery interface.

Future Improvements 🌸

• Advanced duplicate management view
• AI based image tagging
• Album management UI improvements
• Batch metadata editing
• Improved video timeline controls
• Additional UI themes

License

MIT License
