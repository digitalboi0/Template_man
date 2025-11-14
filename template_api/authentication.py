from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.conf import settings
from decouple import config

class InternalAPIAuthentication(BaseAuthentication):
    """
    Internal API authentication for worker services
    Uses X-Internal-Secret header for authentication
    """
    
    def authenticate(self, request):
        # Skip auth for health and metrics endpoints
        if request.path.startswith('/health') or request.path.startswith('/metrics'):
            return None
        
        # Internal endpoints require secret
        if (request.path.startswith('/internal/') or request.path.startswith('/api/v1/')):
            secret = request.headers.get('X-Internal-Secret')
            if not secret or secret != config("INTERNAL_API_SECRET"):
                raise AuthenticationFailed('Invalid internal secret')
            
            # Return dummy user for internal requests
            class InternalUser:
                is_authenticated = True
                is_internal = True
            
            return (InternalUser(), None)
        
        # Public API endpoints don't require auth
        return None