#!/usr/bin/env python3
"""
Lumina Gallery Pro Max - Entry Point
"""

import sys
from pathlib import Path

# Get the directory containing this file (lumina/)
current_dir = Path(__file__).parent
# Get parent directory (Lumina Gallery Pro Max/)
parent_dir = current_dir.parent

# Add parent to path if not already there
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

# Now imports will work
import tkinter as tk
from PIL import Image

from lumina.config import Config
Config.THUMB_QUALITY = Image.Resampling.LANCZOS

from lumina.core import LuminaGalleryProMax


def main():
    root = tk.Tk()
    app = LuminaGalleryProMax(root)
    root.mainloop()


if __name__ == "__main__":
    main()