import threading
import queue

from lumina.utils.logging_utils import logger


class BackgroundWorker:
    def __init__(self, tk_queue):
        self.tk_queue = tk_queue
        self.task_queue = queue.Queue()
        self.active_tasks = {}
        self.lock = threading.RLock()
        self._shutdown = False
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def _worker_loop(self):
        while not self._shutdown:
            try:
                task = self.task_queue.get(timeout=0.1)
                if task is None:
                    break

                task_id, func, callback = task

                with self.lock:
                    if task_id not in self.active_tasks:
                        continue

                try:
                    result = func()
                    with self.lock:
                        should_callback = task_id in self.active_tasks and callback and not self._shutdown
                    if should_callback:
                        self.tk_queue.put(lambda: callback(result))
                except Exception as e:
                    logger.error(f"Worker task {task_id} error: {e}")

                with self.lock:
                    if task_id in self.active_tasks:
                        del self.active_tasks[task_id]

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Worker loop error: {e}")

    def submit(self, task_id, func, callback=None):
        with self.lock:
            if self._shutdown:
                return
            self.active_tasks[task_id] = True
        self.task_queue.put((task_id, func, callback))

    def cancel(self, task_id):
        with self.lock:
            if task_id in self.active_tasks:
                del self.active_tasks[task_id]

    def shutdown(self):
        self._shutdown = True
        with self.lock:
            self.active_tasks.clear()
        self.task_queue.put(None)