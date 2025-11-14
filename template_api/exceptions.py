from rest_framework.views import exception_handler

def custom_exception_handler(exc, context):
    
    response = exception_handler(exc, context)
    
    if response is not None:
        
        request = context['request']
        correlation_id = getattr(request, 'correlation_id', 'unknown')
        
        error_response = {
            'success': False,
            'error': str(exc),
            'message': response.data.get('detail', str(exc)),
            'meta': {'correlation_id': correlation_id}
        }
        
        response.data = error_response
    
    return response