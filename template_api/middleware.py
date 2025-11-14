
import uuid
from .logging_filters import set_correlation_id, clear_correlation_id 

class CorrelationIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        
        correlation_id = request.headers.get('X-Correlation-ID', str(uuid.uuid4()))
        request.correlation_id = correlation_id 

        
        set_correlation_id(correlation_id)

        response = self.get_response(request)
        response['X-Correlation-ID'] = correlation_id
        
        clear_correlation_id()
        return response