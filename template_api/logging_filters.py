
import logging
import threading


_thread_locals = threading.local()

class CorrelationIdFilter(logging.Filter):
    """
    Adds the correlation ID from thread-local storage to log records.
    Expects the correlation ID to be set by CorrelationIdMiddleware.
    """
    def filter(self, record):
        
        correlation_id = getattr(_thread_locals, 'correlation_id', 'unknown')
        record.correlation_id = correlation_id
        return True

def set_correlation_id(correlation_id):
    """Set the correlation ID in thread local storage."""
    setattr(_thread_locals, 'correlation_id', correlation_id)

def clear_correlation_id():
    """Clear the correlation ID from thread local storage."""
    if hasattr(_thread_locals, 'correlation_id'):
        delattr(_thread_locals, 'correlation_id')