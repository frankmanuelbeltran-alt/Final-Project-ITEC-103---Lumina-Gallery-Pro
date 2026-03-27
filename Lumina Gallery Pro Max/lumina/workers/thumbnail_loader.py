import threading
import queue
from concurrent.futures import ThreadPoolExecutor

from lumina.utils.logging_utils import logger


class ThumbnailLoader:
    def __init__(self, tk_queue, max_workers=6, max_concurrent=6):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.semaphore = threading.Semaphore(max_concurrent)
        self.tk_queue = tk_queue
        self.load_queue = queue.PriorityQueue(maxsize=300)
        self.immediate_queue = queue.Queue(maxsize=100)
        self.pending_futures = {}
        self.lock = threading.RLock()
        self._shutdown = False
        self._start_processor()

    def _start_processor(self):
        self.processor_thread = threading.Thread(
            target=self._process_queue, daemon=True
        )
        self.processor_thread.start()

    def _process_queue(self):
        while not self._shutdown:
            try:
                try:
                    priority, task_id, func, callback = self.immediate_queue.get_nowait()
                except queue.Empty:
                    try:
                        priority, task_id, func, callback = self.load_queue.get(timeout=0.02)
                    except queue.Empty:
                        continue

                with self.lock:
                    if (
                        task_id not in self.pending_futures
                        or self.pending_futures[task_id] is not None
                    ):
                        continue

                def wrapped():
                    with self.semaphore:
                        try:
                            return func()
                        except Exception as e:
                            logger.debug(f"Worker error: {e}")
                            return None

                future = self.executor.submit(wrapped)

                with self.lock:
                    if task_id in self.pending_futures:
                        self.pending_futures[task_id] = future
                    else:
                        future.cancel()
                        continue

                def on_complete(fut, cb=callback, tid=task_id):
                    with self.lock:
                        should_callback = tid in self.pending_futures
                        if tid in self.pending_futures:
                            del self.pending_futures[tid]

                    if not should_callback or self._shutdown:
                        return

                    try:
                        result = fut.result()
                        if cb and result is not None:
                            try:
                                self.tk_queue.put(lambda r=result: cb(r))
                            except Exception as e:
                                logger.debug(f"TkQueue error: {e}")
                    except Exception as e:
                        logger.debug(f"Thumbnail load error: {e}")

                future.add_done_callback(on_complete)

            except Exception as e:
                logger.debug(f"Queue processor error: {e}")

    def submit(self, task_id: str, priority: int, func, callback=None):
        with self.lock:
            if self._shutdown:
                return

            if task_id in self.pending_futures:
                old_future = self.pending_futures[task_id]
                if old_future and not old_future.done():
                    old_future.cancel()
                del self.pending_futures[task_id]

            self.pending_futures[task_id] = None

        try:
            self.load_queue.put_nowait((priority, task_id, func, callback))
        except queue.Full:
            pass

    def submit_immediate(self, task_id: str, func, callback=None):
        with self.lock:
            if self._shutdown:
                return

            if task_id in self.pending_futures:
                old_future = self.pending_futures[task_id]
                if old_future and not old_future.done():
                    old_future.cancel()
                del self.pending_futures[task_id]

            self.pending_futures[task_id] = None

        try:
            self.immediate_queue.put_nowait((-1, task_id, func, callback))
        except queue.Full:
            pass

    def cancel(self, task_id: str):
        with self.lock:
            if task_id in self.pending_futures:
                future = self.pending_futures[task_id]
                if future and not future.done():
                    future.cancel()
                del self.pending_futures[task_id]

    def cancel_all(self):
        with self.lock:
            for task_id, future in list(self.pending_futures.items()):
                if future and not future.done():
                    future.cancel()
            self.pending_futures.clear()

        while not self.load_queue.empty():
            try:
                self.load_queue.get_nowait()
            except queue.Empty:
                break

        while not self.immediate_queue.empty():
            try:
                self.immediate_queue.get_nowait()
            except queue.Empty:
                break

    def shutdown(self, wait=True):
        self._shutdown = True
        self.cancel_all()
        self.executor.shutdown(wait=wait)