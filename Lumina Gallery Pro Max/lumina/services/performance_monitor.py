import time
from collections import deque
from dataclasses import dataclass
from functools import wraps
from typing import Optional

from lumina.utils.logging_utils import logger


@dataclass 
class Timing:
    name: str
    start: float
    threshold_ms: float
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        elapsed = (time.time() - self.start) * 1000
        if elapsed > self.threshold_ms:
            logger.warning(f"Slow {self.name}: {elapsed:.1f}ms")


class SimplePerfMonitor:
    """No threads. No psutil. Just timing."""
    
    def __init__(self):
        self.thumb_times = deque(maxlen=50)
        self.query_times = deque(maxlen=50)
    
    def time_thumb(self, func):
        """Decorator to time thumbnail loading"""
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            result = func(*args, **kwargs)
            elapsed = (time.time() - start) * 1000
            self.thumb_times.append(elapsed)
            return result
        return wrapper
    
    def time_query(self, name, threshold_ms=100):
        """Context manager for DB queries"""
        return Timing(name, time.time(), threshold_ms)
    
    def get_stats(self):
        """Return simple stats"""
        avg_thumb = sum(self.thumb_times) / len(self.thumb_times) if self.thumb_times else 0
        avg_query = sum(self.query_times) / len(self.query_times) if self.query_times else 0
        
        return {
            'thumb_avg_ms': round(avg_thumb, 1),
            'query_avg_ms': round(avg_query, 1),
            'slow_thumbs': sum(1 for t in self.thumb_times if t > 500),
            'slow_queries': sum(1 for q in self.query_times if q > 100)
        }