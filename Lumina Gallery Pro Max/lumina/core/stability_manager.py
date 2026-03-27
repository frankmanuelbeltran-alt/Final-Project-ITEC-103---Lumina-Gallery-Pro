import time
from functools import wraps
from lumina.utils.logging_utils import logger


def rate_limited(cooldown_ms=100):
    """Simple rate limiter - per function, not global"""
    last_call = {}
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            now = time.time() * 1000
            key = func.__name__
            
            if now - last_call.get(key, 0) < cooldown_ms:
                return None
            
            last_call[key] = now
            return func(*args, **kwargs)
        return wrapper
    return decorator


def debounced(delay_ms=50):
    """Debounce rapid-fire calls - requires self.root (first arg must have .root)"""
    timers = {}
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = func.__name__
            
            if key in timers:
                try:
                    args[0].root.after_cancel(timers[key])
                except (AttributeError, IndexError):
                    pass
            
            def execute():
                timers.pop(key, None)
                func(*args, **kwargs)
            
            # Need reference to root - assume first arg is self with root
            try:
                timer_id = args[0].root.after(delay_ms, execute)
                timers[key] = timer_id
            except (AttributeError, IndexError):
                # Fallback if no root available
                pass
            
        return wrapper
    return decorator


class CrashTracker:
    """Simple crash counter - no recovery, just tracking"""
    
    def __init__(self):
        self.crashes = 0
        self.last_crash = 0
        self.RESET_AFTER = 30  # seconds
    
    def record(self, error):
        now = time.time()
        
        if now - self.last_crash > self.RESET_AFTER:
            self.crashes = 0
        
        self.crashes += 1
        self.last_crash = now
        
        logger.error(f"Crash #{self.crashes}: {error}")
        
        return self.crashes >= 5  # Return True if too many crashes
    
    def is_healthy(self):
        return self.crashes < 5