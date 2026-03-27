import tkinter as tk
import queue
import time

from lumina.utils.logging_utils import logger


class ToastManager:
    def __init__(self, root, colors):
        self.root = root
        self.colors = colors
        self.active_toasts = []
        self.toast_queue = queue.Queue()
        self._process_queue()

    def _process_queue(self):
        try:
            while True:
                message, duration, emoji = self.toast_queue.get_nowait()
                self._show_toast(message, duration, emoji)
        except queue.Empty:
            pass
        self.root.after(100, self._process_queue)

    def show(self, message, duration=2000, emoji="✨"):
        self.toast_queue.put((message, duration, emoji))

    def _show_toast(self, message, duration=2000, emoji="✨"):
        try:
            toast = tk.Toplevel(self.root)
            toast.overrideredirect(True)
            toast.attributes('-topmost', True)
            toast.attributes('-alpha', 0)

            frame = tk.Frame(toast, bg=self.colors['surface'], 
                            highlightbackground=self.colors['accent'],
                            highlightthickness=2, padx=15, pady=10)
            frame.pack()

            lbl = tk.Label(frame, text=f"{emoji} {message}", 
                          font=self._get_font(11),
                          bg=self.colors['surface'], 
                          fg=self.colors['text'])
            lbl.pack()

            self.root.update_idletasks()
            x = self.root.winfo_x() + self.root.winfo_width()//2 - 100
            y = self.root.winfo_y() + self.root.winfo_height() - 80
            toast.geometry(f"+{x}+{y}")

            for alpha in range(0, 91, 15):
                toast.attributes('-alpha', alpha / 100)
                toast.update()
                time.sleep(0.02)

            def dismiss():
                try:
                    for alpha in range(90, -1, -15):
                        toast.attributes('-alpha', alpha / 100)
                        toast.update()
                        time.sleep(0.02)
                    toast.destroy()
                except tk.TclError:
                    pass

            self.root.after(duration, dismiss)

        except tk.TclError as e:
            logger.error(f"Toast error (window destroyed): {e}")
        except Exception as e:
            logger.error(f"Toast error: {e}")

    def _get_font(self, size):
        return (self._get_font_family(), size)

    def _get_font_family(self):
        return "Nunito" if self._font_exists("Nunito") else "Segoe UI" if self._font_exists("Segoe UI") else "Arial"

    def _font_exists(self, family):
        try:
            import tkinter.font as tkfont
            return family in tkfont.families()
        except Exception:
            return False