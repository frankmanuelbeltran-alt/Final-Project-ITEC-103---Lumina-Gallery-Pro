import threading


class ThreadSafeDict:
    def __init__(self):
        self._dict = {}
        self._lock = threading.RLock()

    def get(self, key, default=None):
        with self._lock:
            return self._dict.get(key, default)

    def __getitem__(self, key):
        with self._lock:
            return self._dict[key]

    def __setitem__(self, key, value):
        with self._lock:
            self._dict[key] = value

    def __delitem__(self, key):
        with self._lock:
            del self._dict[key]

    def pop(self, key, default=None):
        with self._lock:
            return self._dict.pop(key, default)

    def keys(self):
        with self._lock:
            return list(self._dict.keys())

    def values(self):
        with self._lock:
            return list(self._dict.values())

    def items(self):
        with self._lock:
            return list(self._dict.items())

    def __contains__(self, key):
        with self._lock:
            return key in self._dict

    def __len__(self):
        with self._lock:
            return len(self._dict)

    def clear(self):
        with self._lock:
            self._dict.clear()


class ThreadSafeList:
    def __init__(self):
        self._list = []
        self._lock = threading.RLock()

    def append(self, item):
        with self._lock:
            self._list.append(item)

    def extend(self, items):
        with self._lock:
            self._list.extend(items)

    def pop(self, index=-1):
        with self._lock:
            return self._list.pop(index)

    def __getitem__(self, index):
        with self._lock:
            return self._list[index]

    def __setitem__(self, index, value):
        with self._lock:
            self._list[index] = value

    def __len__(self):
        with self._lock:
            return len(self._list)

    def __iter__(self):
        with self._lock:
            return iter(self._list.copy())

    def copy(self):
        with self._lock:
            return self._list.copy()

    def index(self, item):
        with self._lock:
            return self._list.index(item)

    def clear(self):
        with self._lock:
            self._list.clear()