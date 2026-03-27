import tkinter as tk
import queue

from lumina.utils.logging_utils import logger


class TkQueue:
    def __init__(self, root, maxsize=200):
        self.root = root
        self.queue = queue.Queue(maxsize=maxsize)
        self._running = True
        self._check_queue()

    def _check_queue(self):
        if not self._running:
            return

        processed = 0
        max_per_cycle = 20  

        while processed < max_per_cycle:
            try:
                func = self.queue.get_nowait()
                try:
                    self.root.after_idle(func)
                except tk.TclError as e:
                    logger.debug(f"Tkinter error: {e}")
                except Exception as e:
                    logger.debug(f"Queue func error: {e}")
                processed += 1
            except queue.Empty:
                break

        delay = 5 if not self.queue.empty() else 16
        self.root.after(delay, self._check_queue)

    def put(self, func):
        try:
            self.queue.put_nowait(func)
        except queue.Full:
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(func)
            except queue.Empty:
                pass

    def shutdown(self):
        self._running = False