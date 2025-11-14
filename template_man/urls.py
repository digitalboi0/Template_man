
from django.contrib import admin
from django.urls import path, include

from template_api.views import (
    TemplateAPIView,
    TemplateRenderView, 
    HealthCheckView,
    MetricsView,
    
    TemplateVariablesValidationView, 
    InternalOrganizationSyncView, 
    
)



from template_api.views import (
    TemplateAPIView,
    TemplateRenderView, 
    HealthCheckView,
    MetricsView,
    TemplateVariablesValidationView, 
    InternalOrganizationSyncView,
)
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView
)



class PublicSpectacularAPIView(SpectacularAPIView):
    permission_classes = []
    authentication_classes = []


class PublicSpectacularSwaggerView(SpectacularSwaggerView):
    permission_classes = []
    authentication_classes = []


class PublicSpectacularRedocView(SpectacularRedocView):
    permission_classes = []
    authentication_classes = []



urlpatterns = [
    path('admin/', admin.site.urls),

    
    
    path('api/v1/templates/', TemplateAPIView.as_view(), name='template-list-create'),
    
    path('api/v1/templates/<str:code>/', TemplateAPIView.as_view(), name='template-retrieve-update-lifecycle'),
    
    path('api/v1/templates/<str:code>/validate/', TemplateVariablesValidationView.as_view(), name='template-validate'),

    
    
    path('internal/templates/render/', TemplateRenderView.as_view(), name='template-render'),
    
    path('internal/organizations/create-template-org/', InternalOrganizationSyncView.as_view(), name='internal-org-sync'),

    
    
    path('health/', HealthCheckView.as_view(), name='health-check'),
    
    path('metrics/', MetricsView.as_view(), name='metrics'),
    
    
     path('api/schema/', PublicSpectacularAPIView.as_view(), name='schema'),
    
    path('api/docs/', PublicSpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    
    path('api/redoc/', PublicSpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    
    
    

    
    

]