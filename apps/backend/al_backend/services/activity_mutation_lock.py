"""Pause accounting during publisher rebuilds; intake and ordinary parallel workers stay available."""
from contextlib import contextmanager
from functools import wraps
from threading import Condition, get_ident


class ActivityMutationGate:
    def __init__(self):
        self._condition = Condition()
        self._readers = 0
        self._reader_depth = {}
        self._writer = None
        self._depth = 0
        self._waiting_writers = 0

    @contextmanager
    def accounting(self):
        with self._condition:
            self._condition.wait_for(lambda: self._reader_depth.get(get_ident(), 0) or self._writer == get_ident() or (self._writer is None and not self._waiting_writers))
            self._readers += 1
            self._reader_depth[get_ident()] = self._reader_depth.get(get_ident(), 0) + 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                self._reader_depth[get_ident()] -= 1
                if not self._reader_depth[get_ident()]:
                    del self._reader_depth[get_ident()]
                self._condition.notify_all()

    def __enter__(self):
        with self._condition:
            if self._writer == get_ident():
                self._depth += 1
                return self
            self._waiting_writers += 1
            try:
                self._condition.wait_for(lambda: self._writer is None and self._readers == 0)
                self._writer = get_ident()
                self._depth = 1
            finally:
                self._waiting_writers -= 1
        return self

    def __exit__(self, *_):
        with self._condition:
            self._depth -= 1
            if not self._depth:
                self._writer = None
                self._condition.notify_all()


activity_mutation_lock = ActivityMutationGate()


def serialized_activity_mutation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with activity_mutation_lock.accounting():
            return function(*args, **kwargs)
    return wrapped
