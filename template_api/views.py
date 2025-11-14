

import logging
import json
import time
from django.utils import timezone
from django.db import transaction, connection
from django.core.exceptions import ValidationError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from prometheus_client import Counter, Histogram
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import requests 

from .models import Template, TemplateUsageLog, Organization 
from .cache_manager import TemplateCacheManager
from .renderer import TemplateRenderer, RenderError
from .redis_client import redis_client
from .authentication import InternalAPIAuthentication 
from rest_framework import status

from django.core.cache import cache

from django.conf import settings

from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse, OpenApiExample
from drf_spectacular.types import OpenApiTypes
from template_api.serializers import (
    TemplateCreateSerializer,
    TemplateUpdateSerializer,
    TemplateLifecycleSerializer,
    TemplateResponseSerializer,
    TemplateRenderRequestSerializer,
    TemplateRenderResponseSerializer,
    TemplateValidationRequestSerializer,
    TemplateValidationResponseSerializer,
    OrganizationSyncSerializer,
    StandardResponseSerializer,
    HealthCheckSerializer,
)



logger = logging.getLogger(__name__)


TEMPLATE_REQUESTS = Counter(
    'template_requests_total',
    'Total template requests',
    ['template_code', 'cache_hit']
)
TEMPLATE_RENDERS = Counter(
    'template_renders_total',
    'Total template renders',
    ['template_code', 'success']
)
REQUEST_LATENCY = Histogram(
    'template_request_duration_seconds',
    'Request latency in seconds',
    ['endpoint']
)


def get_standard_meta():
    
    return {"total": 1, "limit": 1, "page": 1, "total_pages": 1, "has_next": False, "has_previous": False}


class StandardResultsSetPagination(PageNumberPagination):
    """Standard pagination class for API responses"""
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 100
    
    def get_paginated_response(self, data):
        return Response({
            'success': True,
            'data': data,
            'message': 'Templates retrieved successfully',
            'meta': {
                'total': self.page.paginator.count,
                'limit': self.get_page_size(self.request),
                'page': self.page.number,
                'total_pages': self.page.paginator.num_pages,
                'has_next': self.page.has_next(),
                'has_previous': self.page.has_previous()
            }
        })


class TemplateAPIView(APIView):
    """
    Comprehensive template management API
    - Create, read, update templates
    - Version management
    - Publishing workflow
    - Scoped to Organization
    """
    
    
    authentication_classes = [InternalAPIAuthentication]
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cache_manager = TemplateCacheManager()
        
    @extend_schema(
        operation_id='list_or_get_templates',
        summary='List all templates or get specific template',
        description='''
        **GET /api/v1/templates/** - List all templates for your organization
        **GET /api/v1/templates/{code}/** - Get specific template by code
        
        Templates are scoped to your organization. You can only see your own templates.
        
        **Filters available for listing:**
        - type (email/push/sms)
        - language (en, es, fr, etc.)
        - status (draft, active, archived)
        - tag
        - search (searches in code, name, description)
        ''',
        tags=['Templates'],
        parameters=[
            OpenApiParameter(
                name='code',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                required=False,
                description='Template code (for single template retrieval)'
            ),
            OpenApiParameter(
                name='X-Internal-Secret',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description='Internal service secret'
            ),
            OpenApiParameter(
                name='X-Organization-ID',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description='Organization ID'
            ),
            OpenApiParameter(
                name='type',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter by template type',
                enum=['email', 'push', 'sms']
            ),
            OpenApiParameter(
                name='language',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter by language code',
                default='en'
            ),
            OpenApiParameter(
                name='status',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter by status',
                enum=['draft', 'active', 'archived'],
                default='active'
            ),
            OpenApiParameter(
                name='tag',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter by tag'
            ),
            OpenApiParameter(
                name='search',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Search in code, name, and description'
            ),
            OpenApiParameter(
                name='page',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Page number',
                default=1
            ),
            OpenApiParameter(
                name='page_size',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Items per page (max 100)',
                default=50
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=StandardResponseSerializer,
                description='Template(s) retrieved successfully',
                examples=[
                    OpenApiExample(
                        'Single Template',
                        value={
                            'success': True,
                            'data': {
                                'id': 'template_123',
                                'code': 'welcome_email',
                                'name': 'Welcome Email',
                                'type': 'email',
                                'subject': 'Welcome to {{company}}!',
                                'content': 'Hi {{name}}...',
                                'variables': ['name', 'company'],
                                'status': 'active',
                                'version': 1
                            },
                            'message': 'Template retrieved successfully',
                            'meta': {}
                        }
                    ),
                    OpenApiExample(
                        'Template List',
                        value={
                            'success': True,
                            'data': [
                                {
                                    'code': 'welcome_email',
                                    'name': 'Welcome Email',
                                    'type': 'email',
                                    'status': 'active'
                                },
                                {
                                    'code': 'password_reset',
                                    'name': 'Password Reset',
                                    'type': 'email',
                                    'status': 'active'
                                }
                            ],
                            'message': 'Templates retrieved successfully',
                            'meta': {
                                'total': 25,
                                'page': 1,
                                'total_pages': 1
                            }
                        }
                    )
                ]
            ),
            400: OpenApiResponse(description='Bad request - missing X-Organization-ID'),
            404: OpenApiResponse(description='Template not found'),
            500: OpenApiResponse(description='Internal server error'),
        }
    )    
    
    def get(self, request, code=None):
        """
        GET /api/v1/templates/ - List all templates for the requesting organization
        GET /api/v1/templates/<code>/ - Get template by code for the requesting organization
        """
        with REQUEST_LATENCY.labels(endpoint='get_template').time():
            try:
                
                org_id = request.headers.get('X-Organization-ID')
                if not org_id:
                     logger.warning(f"X-Organization-ID header missing for GET request")
                     return Response({
                         'success': False,
                         'error': 'missing_organization',
                         'message': 'X-Organization-ID header is required',
                         'meta': get_standard_meta()
                     }, status=http_status.HTTP_400_BAD_REQUEST)

                if code:
                    return self._get_single_template(request, code, org_id)
                else:
                    return self._list_templates(request, org_id)
            except Exception as e:
                logger.error(f"Failed to get template(s) for org {org_id}: {str(e)}", exc_info=True)
                return Response({
                    'success': False,
                    'error': 'internal_error',
                    'message': 'Unable to retrieve templates',
                    'meta': get_standard_meta()
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _get_single_template(self, request, code, org_id):
        """Get a single template for the specified organization"""
        language = request.query_params.get('language', 'en')
        
        
        template = self.cache_manager.get_template(code, language, org_id) 

        cache_hit = template is not None
        TEMPLATE_REQUESTS.labels(template_code=code, cache_hit='hit' if cache_hit else 'miss').inc()
        
        if not template:
            logger.info(f"Template '{code}' not found for organization '{org_id}' and language '{language}'")
            return Response({
                'success': False,
                'error': 'template_not_found',
                'message': f'Template "{code}" not found for your organization',
                'meta': get_standard_meta()
            }, status=status.HTTP_404_NOT_FOUND)
        
        
        
        
        
        
        
        
        
        
        return Response({
            'success': True,
            'data': template,
            'message': 'Template retrieved successfully',
            'meta': get_standard_meta()
        })

    def _list_templates(self, request, org_id):
        """List templates with filtering and pagination, scoped to the organization"""
        template_type = request.query_params.get('type')
        language = request.query_params.get('language')
        tag = request.query_params.get('tag')
        status_filter = request.query_params.get('status', Template.STATUS_CHOICES.active)
        search_query = request.query_params.get('search')
        
        
        
        
        if template_type:
            templates = self.cache_manager.get_templates_by_type(template_type, org_id) 
        elif tag:
            templates = self.cache_manager.get_templates_by_tag(tag, org_id) 
        else:
            templates = self.cache_manager.get_all_templates(org_id) 
        
        
        if language:
            templates = [t for t in templates if t['language'] == language]
        
        if status_filter:
            templates = [t for t in templates if t['status'] == status_filter]
        
        if search_query:
            search_query = search_query.lower()
            templates = [
                t for t in templates
                if search_query in t['code'].lower() or
                search_query in t['name'].lower() or
                search_query in t['description'].lower()
            ]
        
        
        templates = sorted(templates, key=lambda t: t.get('usage_count', 0), reverse=True)
        
        
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(templates, request)
        
        return paginator.get_paginated_response(page)
    
    def post(self, request):
        """
        POST /api/v1/templates/ - Create new template for the requesting organization
        """
        with REQUEST_LATENCY.labels(endpoint='create_template').time():
            try:
                return self._create_template(request)
            except ValidationError as e:
                return Response({
                    'success': False,
                    'error': 'validation_error',
                    'message': str(e),
                    'meta': get_standard_meta()
                }, status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                logger.error(f"Failed to create template: {str(e)}", exc_info=True)
                return Response({
                    'success': False,
                    'error': 'internal_error',
                    'message': 'Unable to create template',
                    'meta': get_standard_meta()
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _create_template(self, request):
        """Create a new template for the requesting organization"""
        data = request.data
        
        
        org_id = request.headers.get('X-Organization-ID')
        if not org_id:
             logger.warning(f"X-Organization-ID header missing for CREATE request")
             return Response({
                 'success': False,
                 'error': 'missing_organization',
                 'message': 'X-Organization-ID header is required',
                 'meta': get_standard_meta()
             }, status=status.HTTP_400_BAD_REQUEST)

        
        required_fields = ['code', 'name', 'type', 'content']
        missing_fields = [field for field in required_fields if not data.get(field)]
        if missing_fields:
            raise ValidationError(f"Missing required fields: {', '.join(missing_fields)}")
        
        
        
        try:
            existing = Template.objects.get(
                code=data['code'],
                organization_id=org_id, 
                language=data.get('language', 'en')
            )
            
            raise ValidationError(f"Template with code '{data['code']}' already exists for your organization")
        except Template.DoesNotExist:
            pass 

        
        with transaction.atomic():
            template = Template(
                code=data['code'],
                name=data['name'],
                description=data.get('description', ''),
                template_type=data['type'],
                subject=data.get('subject', ''),
                content=data['content'],
                html_content=data.get('html_content', ''),
                language=data.get('language', 'en'),
                status=Template.STATUS_CHOICES.draft, 
                variables=data.get('variables', []),
                optional_variables=data.get('optional_variables', []),
                metadata=data.get('metadata', {}),
                tags=data.get('tags', []),
                
                organization_id=org_id, 
                created_by=request.headers.get('X-User-ID', 'system'), 
                updated_by=request.headers.get('X-User-ID', 'system')
            )
            
            
            template.clean()
            template.save()
            
            
            template.version = 1
            template.save()
        
        logger.info(f"Template created: {template.code} (v{template.version}) for org {org_id}")
        
        return Response({
            'success': True,
            'data': self._serialize_template(template),
            'message': 'Template created successfully',
            'meta': get_standard_meta()
        }, status=status.HTTP_201_CREATED)
    
    @extend_schema(
        operation_id='create_template',
        summary='Create a new template',
        description='''
        Create a new notification template for your organization.
        
        **Status:** Templates are created in "draft" status. You must publish them to make them active.
        
        **Variables:** Use {{variable_name}} syntax in subject and content.
        
        **Next step:** After creation, use PATCH with action="publish" to activate the template.
        ''',
        tags=['Templates'],
        request=TemplateCreateSerializer,
        responses={
            201: OpenApiResponse(
                response=StandardResponseSerializer,
                description='Template created successfully',
                examples=[
                    OpenApiExample(
                        'Created Template',
                        value={
                            'success': True,
                            'data': {
                                'id': 'template_123',
                                'code': 'welcome_email',
                                'name': 'Welcome Email',
                                'status': 'draft',
                                'version': 1
                            },
                            'message': 'Template created successfully',
                            'meta': {}
                        }
                    )
                ]
            ),
            400: OpenApiResponse(description='Bad request - validation error'),
            409: OpenApiResponse(description='Conflict - template code already exists'),
            500: OpenApiResponse(description='Internal server error'),
        },
        parameters=[
            OpenApiParameter(
                name='X-Internal-Secret',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description='Internal service secret'
            ),
            OpenApiParameter(
                name='X-Organization-ID',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description='Organization ID'
            ),
        ],
        examples=[
            OpenApiExample(
                'Email Template',
                value={
                    'code': 'welcome_email',
                    'name': 'Welcome Email',
                    'type': 'email',
                    'subject': 'Welcome to {{company}}!',
                    'content': 'Hi {{name}},\n\nWelcome to {{company}}!',
                    'variables': ['name', 'company']
                },
                request_only=True
            ),
            OpenApiExample(
                'Push Template',
                value={
                    'code': 'new_message',
                    'name': 'New Message Notification',
                    'type': 'push',
                    'subject': 'New message from {{sender}}',
                    'content': '{{message_preview}}',
                    'variables': ['sender', 'message_preview']
                },
                request_only=True
            ),
        ]
    )
        
    @extend_schema(
        operation_id='update_template',
        summary='Update template (creates new version)',
        description='''
        Update an existing template by creating a new version.
        
        **Versioning:** This creates a new draft version. The current active version remains unchanged.
        
        **To activate:** After updating, use PATCH with action="publish" to make the new version active.
        ''',
        tags=['Templates'],
        request=TemplateUpdateSerializer,
        responses={
            200: OpenApiResponse(
                response=StandardResponseSerializer,
                description='Template updated (new version created)',
                examples=[
                    OpenApiExample(
                        'New Version',
                        value={
                            'success': True,
                            'data': {
                                'code': 'welcome_email',
                                'version': 2,
                                'status': 'draft',
                                'name': 'Updated Welcome Email'
                            },
                            'message': 'Template updated successfully (new version created)',
                            'meta': {}
                        }
                    )
                ]
            ),
            400: OpenApiResponse(description='Bad request'),
            404: OpenApiResponse(description='Template not found'),
            500: OpenApiResponse(description='Internal server error'),
        },
        parameters=[
            OpenApiParameter(
                name='code',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                required=True,
                description='Template code'
            ),
            OpenApiParameter(
                name='X-Internal-Secret',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description='Internal service secret'
            ),
            OpenApiParameter(
                name='X-Organization-ID',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description='Organization ID'
            ),
        ]
    )
    
    def put(self, request, code):
        """
        PUT /api/v1/templates/<code>/ - Update template (creates new version) for the requesting organization
        """
        with REQUEST_LATENCY.labels(endpoint='update_template').time():
            try:
                return self._update_template(request, code)
            except Template.DoesNotExist:
                return Response({
                    'success': False,
                    'error': 'template_not_found',
                    'message': f'Template "{code}" not found for your organization',
                    'meta': get_standard_meta()
                }, status=status.HTTP_404_NOT_FOUND)
            except ValidationError as e:
                return Response({
                    'success': False,
                    'error': 'validation_error',
                    'message': str(e),
                    'meta': get_standard_meta()
                }, status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                logger.error(f"Failed to update template: {str(e)}", exc_info=True)
                return Response({
                    'success': False,
                    'error': 'internal_error',
                    'message': 'Unable to update template',
                    'meta': get_standard_meta()
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _update_template(self, request, code):
        """Update template (creates new version) for the requesting organization"""
        
        org_id = request.headers.get('X-Organization-ID')
        if not org_id:
             logger.warning(f"X-Organization-ID header missing for UPDATE request")
             return Response({
                 'success': False,
                 'error': 'missing_organization',
                 'message': 'X-Organization-ID header is required',
                 'meta': get_standard_meta()
             }, status=status.HTTP_400_BAD_REQUEST)

        language = request.data.get('language', 'en')
        
        
        current_template = Template.objects.filter(
            code=code,
            organization_id=org_id, 
            language=language,
            status=Template.STATUS_CHOICES.active,
            is_default=True
        ).first()
        
        if not current_template:
            raise Template.DoesNotExist()
        
        
        with transaction.atomic():
            
            new_version = Template(
                code=current_template.code,
                name=request.data.get('name', current_template.name),
                description=request.data.get('description', current_template.description),
                template_type=current_template.template_type,
                subject=request.data.get('subject', current_template.subject),
                content=request.data.get('content', current_template.content),
                html_content=request.data.get('html_content', current_template.html_content),
                language=current_template.language,
                version=current_template.version + 1,
                status=Template.STATUS_CHOICES.draft, 
                variables=request.data.get('variables', current_template.variables),
                optional_variables=request.data.get('optional_variables', current_template.optional_variables),
                metadata=request.data.get('metadata', current_template.metadata),
                tags=request.data.get('tags', current_template.tags),
                
                organization_id=org_id, 
                parent_template=current_template,
                created_by=request.headers.get('X-User-ID', 'system'),
                updated_by=request.headers.get('X-User-ID', 'system')
            )
            
            
            new_version.clean()
            new_version.save()
        
        logger.info(f"Template updated: {code} (v{new_version.version}) for org {org_id}")
        
        return Response({
            'success': True,
            'data': self._serialize_template(new_version),
            'message': 'Template updated successfully (new version created)',
            'meta': get_standard_meta()
        })
        
        
    @extend_schema(
        operation_id='manage_template_lifecycle',
        summary='Publish or archive template',
        description='''
        Manage template lifecycle state.
        
        **Actions:**
        - `publish`: Activate a draft template (makes it available for use)
        - `archive`: Deactivate a template (removes from active use)
        
        **Important:** Only one version of a template can be active at a time.
        Publishing a new version automatically deactivates the previous version.
        ''',
        tags=['Templates'],
        request=TemplateLifecycleSerializer,
        responses={
            200: OpenApiResponse(
                response=StandardResponseSerializer,
                description='Template lifecycle action completed',
                examples=[
                    OpenApiExample(
                        'Published',
                        value={
                            'success': True,
                            'data': {
                                'code': 'welcome_email',
                                'version': 1,
                                'status': 'active',
                                'is_default': True
                            },
                            'message': 'Template published successfully',
                            'meta': {}
                        }
                    )
                ]
            ),
            400: OpenApiResponse(description='Bad request - invalid action or already in that state'),
            404: OpenApiResponse(description='Template not found'),
            500: OpenApiResponse(description='Internal server error'),
        },
        parameters=[
            OpenApiParameter(
                name='code',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                required=True,
                description='Template code'
            ),
            OpenApiParameter(
                name='X-Internal-Secret',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description='Internal service secret'
            ),
            OpenApiParameter(
                name='X-Organization-ID',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description='Organization ID'
            ),
        ]
    )    
    
    def patch(self, request, code):
        """
        PATCH /api/v1/templates/<code>/ - Publish or archive template for the requesting organization
        """
        with REQUEST_LATENCY.labels(endpoint='publish_template').time():
            try:
                return self._manage_template_lifecycle(request, code)
            except Template.DoesNotExist:
                return Response({
                    'success': False,
                    'error': 'template_not_found',
                    'message': f'Template "{code}" not found for your organization',
                    'meta': get_standard_meta()
                }, status=status.HTTP_404_NOT_FOUND)
            except ValidationError as e:
                return Response({
                    'success': False,
                    'error': 'validation_error',
                    'message': str(e),
                    'meta': get_standard_meta()
                }, status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                logger.error(f"Failed to manage template lifecycle: {str(e)}", exc_info=True)
                return Response({
                    'success': False,
                    'error': 'internal_error',
                    'message': 'Unable to manage template lifecycle',
                    'meta': get_standard_meta()
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _manage_template_lifecycle(self, request, code):
        """Manage template lifecycle (publish/archive) for the requesting organization"""
        action = request.data.get('action')
        language = request.data.get('language', 'en')
        
        org_id = request.headers.get('X-Organization-ID')
        if not org_id:
             logger.warning(f"X-Organization-ID header missing for LIFECYCLE request")
             return Response({
                 'success': False,
                 'error': 'missing_organization',
                 'message': 'X-Organization-ID header is required',
                 'meta': get_standard_meta()
             }, status=status.HTTP_400_BAD_REQUEST)

        updated_by = request.headers.get('X-User-ID', 'system')
        
        
        template = Template.objects.filter(
            code=code,
            organization_id=org_id, 
            language=language
        ).order_by('-version').first()
        
        if not template:
            raise Template.DoesNotExist()
        
        if action == 'publish':
            if template.status == Template.STATUS_CHOICES.active:
                raise ValidationError("Template is already published")
            
            
            template.activate(updated_by=updated_by) 
            
            
            self.cache_manager.invalidate_template(code, language, org_id) 
            
            logger.info(f"Template published: {code} v{template.version} for org {org_id}")
            
        elif action == 'archive':
            if template.status == Template.STATUS_CHOICES.archived:
                raise ValidationError("Template is already archived")
            
            
            template.archive(updated_by=updated_by) 
            
            
            self.cache_manager.invalidate_template(code, language, org_id) 
            
            logger.info(f"Template archived: {code} v{template.version} for org {org_id}")
        
        else:
            raise ValidationError(f"Invalid action: {action}. Must be 'publish' or 'archive'")
        
        return Response({
            'success': True,
            'data': self._serialize_template(template),
            'message': f'Template {action}ed successfully',
            'meta': get_standard_meta()
        })
    
    def _serialize_template(self, template):
        """Serialize template model to API response format"""
        return {
            'id': str(template.id),
            'code': template.code,
            'name': template.name,
            'description': template.description,
            'type': template.template_type,
            'subject': template.subject,
            'content': template.content,
            'html_content': template.html_content,
            'language': template.language,
            'version': template.version,
            'variables': template.variables,
            'optional_variables': template.optional_variables,
            'metadata': template.metadata,
            'tags': template.tags,
            'status': template.status,
            'is_default': template.is_default,
            'created_at': template.created_at.isoformat(),
            'updated_at': template.updated_at.isoformat(),
            'published_at': template.published_at.isoformat() if template.published_at else None,
            'usage_count': template.usage_count,
            'last_used_at': template.last_used_at.isoformat() if template.last_used_at else None,
            'average_render_time': template.average_render_time,
            
            'organization_id': str(template.organization.id) 
        }


class TemplateRenderView(APIView):
    """
    Internal API for rendering templates
    Used by notification gateway to render templates before sending
    """
    
    
    authentication_classes = [InternalAPIAuthentication]
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        operation_id='render_template',
        summary='Render template with variables (Internal)',
        description='''
        **Internal endpoint used by the notification gateway.**
        
        Renders a template by substituting variables into the template content.
        
        **Process:**
        1. Fetches active template from cache/database
        2. Validates all required variables are provided
        3. Substitutes variables into subject and content
        4. Returns rendered output with timing metrics
        
        **Caching:** Templates are cached for performance. Rendering is fast (typically <50ms).
        ''',
        tags=['Internal'],
        request=TemplateRenderRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=StandardResponseSerializer,
                description='Template rendered successfully',
                examples=[
                    OpenApiExample(
                        'Rendered Template',
                        value={
                            'success': True,
                            'data': {
                                'subject': 'Welcome to Acme Corp!',
                                'content': 'Hi John Doe,\n\nWelcome to Acme Corp!',
                                'html_content': '<p>Hi John Doe...</p>',
                                'template_id': 'template_123',
                                'template_version': 1,
                                'render_time': 0.0234
                            },
                            'message': 'Template rendered successfully',
                            'meta': {}
                        }
                    )
                ]
            ),
            400: OpenApiResponse(description='Bad request - missing required fields or variables'),
            404: OpenApiResponse(description='Template not found'),
            422: OpenApiResponse(description='Render error - template syntax error'),
            500: OpenApiResponse(description='Internal server error'),
        },
        parameters=[
            OpenApiParameter(
                name='X-Internal-Secret',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description='Internal service secret'
            ),
        ]
    )
    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=2),
        reraise=True
    )
    
    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=2),
        reraise=True
    )
    def post(self, request):
        """
        POST /internal/templates/render/ - Render template
        
        Request body:
        {
            "template_code": "welcome_email",
            "language": "en",
            "variables": {
                "name": "John Doe",
                "company": "Acme Inc"
            },
            "notification_id": "notif_123",
            "organization_id": "org_456"
        }
        """
        with REQUEST_LATENCY.labels(endpoint='render_template').time():
            try:
                template_code = request.data.get('template_code')
                language = request.data.get('language', 'en')
                variables = request.data.get('variables', {})
                notification_id = request.data.get('notification_id')
                
                organization_id = request.data.get('organization_id')

                if not all([template_code, notification_id, organization_id]):
                    return Response({
                        'success': False,
                        'error': 'missing_required_fields',
                        'message': 'template_code, notification_id, and organization_id are required',
                        'meta': get_standard_meta()
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                
                
                template = self._get_template_scoped(template_code, language, organization_id)
                if not template:
                    TEMPLATE_RENDERS.labels(template_code=template_code, success='false').inc()
                    return Response({
                        'success': False,
                        'error': 'template_not_found',
                        'message': f'Template "{template_code}" not found for the specified organization',
                        'meta': get_standard_meta()
                    }, status=status.HTTP_404_NOT_FOUND)
                
                
                validation_result = self._validate_variables(template, variables)
                if not validation_result['valid']:
                    TEMPLATE_RENDERS.labels(template_code=template_code, success='false').inc()
                    return Response({
                        'success': False,
                        'error': 'missing_variables',
                        'message': f'Missing required variables: {", ".join(validation_result["missing"])}',
                        'required_variables': validation_result['required'],
                        'missing_variables': validation_result['missing'],
                        'meta': get_standard_meta()
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                
                rendered_template, render_time, success = TemplateRenderer.render_template(
                    template=template,
                    variables=variables,
                    notification_id=notification_id,
                    organization_id=organization_id
                )
                
                TEMPLATE_RENDERS.labels(template_code=template_code, success='true' if success else 'false').inc()
                
                return Response({
                    'success': True,
                    'data': {
                        'subject': rendered_template['subject'],
                        'content': rendered_template['content'],
                        'html_content': rendered_template['html_content'],
                        'template_id': str(template.id),
                        'template_version': template.version,
                        'render_time': render_time
                    },
                    'message': 'Template rendered successfully',
                    'meta': get_standard_meta()
                })
            
            except RenderError as e:
                logger.error(f"Template render error: {str(e)}", exc_info=True)
                TEMPLATE_RENDERS.labels(template_code=template_code, success='false').inc()
                return Response({
                    'success': False,
                    'error': 'render_error',
                    'message': str(e),
                    'meta': get_standard_meta()
                }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
            
            except Exception as e:
                logger.error(f"Template render failed: {str(e)}", exc_info=True)
                TEMPLATE_RENDERS.labels(template_code=template_code, success='false').inc()
                return Response({
                    'success': False,
                    'error': 'internal_error',
                    'message': 'Template rendering failed',
                    'meta': get_standard_meta()
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    
    def _get_template_scoped(self, code, language, organization_id):
        """Get template from database/cache, scoped to a specific organization ID."""
        
        
        template_data = self.cache_manager.get_template(code, language, organization_id) 
        if template_data:
            try:
                
                template = Template()
                for key, value in template_data.items():
                    setattr(template, key, value)
                return template
            except Exception as e:
                logger.warning(f"Failed to reconstruct template from cache: {str(e)}")
                

        
        try:
            
            return Template.get_active_template(code, language, organization_id) 
        except Exception as e:
            logger.error(f"Database query failed for template {code} (org: {organization_id}, lang: {language}): {str(e)}", exc_info=True)
            return None

    def _validate_variables(self, template, provided_variables):
        """Validate that all required variables are provided"""
        required_vars = set(template.variables)
        provided_vars = set(provided_variables.keys())
        
        missing = required_vars - provided_vars
        extra = provided_vars - required_vars
        
        return {
            'valid': len(missing) == 0,
            'required': sorted(list(required_vars)),
            'missing': sorted(list(missing)),
            'extra': sorted(list(extra))
        }


class HealthCheckView(APIView):
    """
    Comprehensive health check endpoint
    Checks database, Redis, and other critical dependencies
    """
    
    
    authentication_classes = []
    permission_classes = []
    
    @extend_schema(
        operation_id='health_check_template',
        summary='Health check',
        description='''
        Check the health status of the template service and its dependencies.
        
        **Checks performed:**
        - Database connectivity
        - Redis cache availability
        - Template cache functionality
        - Active template count
        
        Returns 200 if healthy, 503 if any check fails.
        ''',
        tags=['System'],
        responses={
            200: OpenApiResponse(
                response=HealthCheckSerializer,
                description='Service is healthy',
                examples=[
                    OpenApiExample(
                        'Healthy',
                        value={
                            'status': 'healthy',
                            'timestamp': '2025-01-01T12:00:00Z',
                            'service': 'template-service',
                            'version': '1.0.0',
                            'checks': {
                                'database': 'healthy',
                                'redis': 'healthy',
                                'cache': 'healthy',
                                'templates': 'healthy (125 templates)'
                            }
                        }
                    )
                ]
            ),
            503: OpenApiResponse(
                description='Service is unhealthy',
                examples=[
                    OpenApiExample(
                        'Unhealthy',
                        value={
                            'status': 'unhealthy',
                            'timestamp': '2025-01-01T12:00:00Z',
                            'service': 'template-service',
                            'checks': {
                                'database': 'healthy',
                                'redis': 'unhealthy: Connection refused',
                                'cache': 'unhealthy',
                                'templates': 'healthy (125 templates)'
                            }
                        }
                    )
                ]
            ),
        }
    )
    
    def get(self, request):
        """Health check endpoint"""
        health_status = {
            'status': 'healthy',
            'timestamp': timezone.now().isoformat(),
            'service': 'template-service',
            'version': '1.0.0',
            'checks': {}
        }
        
        
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            health_status['checks']['database'] = 'healthy'
        except Exception as e:
            health_status['checks']['database'] = f'unhealthy: {str(e)}'
            health_status['status'] = 'unhealthy'
        
        
        try:
            redis_health = redis_client.health_check()
            health_status['checks']['redis'] = redis_health['status']
            if redis_health['status'] != 'healthy':
                health_status['status'] = 'unhealthy'
        except Exception as e:
            health_status['checks']['redis'] = f'unhealthy: {str(e)}'
            health_status['status'] = 'unhealthy'
        
        
        try:
            cache_stats = TemplateCacheManager().get_stats()
            health_status['checks']['cache'] = 'healthy'
            health_status['cache_stats'] = cache_stats
        except Exception as e:
            health_status['checks']['cache'] = f'unhealthy: {str(e)}'
            health_status['status'] = 'unhealthy'
        
        
        try:
            template_count = Template.objects.filter(status=Template.STATUS_CHOICES.active).count()
            health_status['checks']['templates'] = f'healthy ({template_count} templates)'
        except Exception as e:
            health_status['checks']['templates'] = f'unhealthy: {str(e)}'
            health_status['status'] = 'unhealthy'
        
        status_code = 200 if health_status['status'] == 'healthy' else 503
        return Response(health_status, status=status_code)


class MetricsView(APIView):
    """
    Metrics endpoint for Prometheus
    Exposes application metrics for monitoring
    """
    
    
    authentication_classes = []
    permission_classes = []
     
    @extend_schema(
        operation_id='get_metrics',
        summary='Prometheus metrics',
        description='Expose application metrics for Prometheus monitoring',
        tags=['System'],
        responses={
            200: OpenApiResponse(
                description='Metrics retrieved',
                examples=[
                    OpenApiExample(
                        'Metrics',
                        value={
                            'templates_cached': 125,
                            'uptime_seconds': 3600
                        }
                    )
                ]
            )
        }
    )
    
    def get(self, request):
        """Metrics endpoint"""
        
        
        metrics = {
            'templates_cached': len(TemplateCacheManager().get_all_templates()),
            'uptime_seconds': time.time() - getattr(TemplateCacheManager, '_initialization_time', time.time()) 
        }
        
        return Response(metrics)


class TemplateCacheManager:
    """
    Centralized cache manager for template data
    Handles caching, invalidation and cache-aside pattern
    """
    
    def __init__(self):
        self.cache_client = redis_client 
        self.default_ttl = getattr(settings, 'TEMPLATE_CACHE_TTL', 300) 
    
    
    def get_template(self, code, language='en', organization_id=None, use_cache=True):
        """
        Get template from cache or database, scoped to organization.
        Args:
            code (str): Template code
            language (str): Language code (default: en)
            organization_id (str): Organization ID for scoping
            use_cache (bool): Whether to use cache (default: True)
        """
        if not organization_id:
             logger.error("Organization ID is required to get a template from cache/DB")
             return None 

        
        cache_key = self._make_template_cache_key(code, language, organization_id)
        
        if use_cache:
            try:
                cached_data = self.cache_client.get(cache_key)
                if cached_data:
                    logger.debug(f"Cache hit for template: {code} (org: {organization_id}, lang: {language})")
                    
                    return json.loads(cached_data)
                else:
                    logger.debug(f"Cache miss for template: {code} (org: {organization_id}, lang: {language})")
            except Exception as e:
                logger.warning(f"Cache get failed for {cache_key}: {str(e)}")
                
        
        
        try:
            
            template = Template.get_active_template(code, language, organization_id) 
            if template:
                 template_data = self._serialize_template(template)

                 
                 if use_cache:
                     try:
                         
                         
                         self.cache_client.setex(cache_key, self.default_ttl, json.dumps(template_data))
                         logger.debug(f"Template cached: {code} (org: {organization_id}, lang: {language})")
                     except Exception as e:
                         logger.warning(f"Cache set failed for {cache_key}: {str(e)}")

                 return template_data
            else:
                 
                 logger.info(f"Active template '{code}' not found in DB for organization '{organization_id}' and language '{language}'")
                 return None

        except Exception as e:
            logger.error(f"Database query failed for template {code} (org: {organization_id}, lang: {language}): {str(e)}", exc_info=True)
            return None

    
    def _make_template_cache_key(self, code, language, organization_id):
        """Generate cache key including organization ID"""
        return f"template:{organization_id}:{code}:{language}"

    
    def _serialize_template(self, template):
        """Convert template model instance to dictionary for caching."""
        
        return {
            'id': str(template.id),
            'code': template.code,
            'name': template.name,
            'description': template.description,
            'type': template.template_type,
            'subject': template.subject,
            'content': template.content,
            'html_content': template.html_content,
            'language': template.language,
            'version': template.version,
            'variables': template.variables,
            'optional_variables': template.optional_variables,
            'metadata': template.metadata,
            'tags': template.tags,
            'status': template.status,
            'is_default': template.is_default,
            'created_at': template.created_at.isoformat(),
            'updated_at': template.updated_at.isoformat(),
            'published_at': template.published_at.isoformat() if template.published_at else None,
            'usage_count': template.usage_count,
            'last_used_at': template.last_used_at.isoformat() if template.last_used_at else None,
            'average_render_time': template.average_render_time,
            
            'organization_id': str(template.organization.id)
        }

    
    def invalidate_template(self, code, language='en', organization_id=None):
        """Invalidate specific template cache entry for an organization."""
        if not organization_id:
             logger.error("Organization ID is required to invalidate a template cache")
             return

        
        cache_key = self._make_template_cache_key(code, language, organization_id)
        try:
            
            self.cache_client.delete(cache_key)
            logger.info(f"Cache invalidated for template: {code} (org: {organization_id}, lang: {language})")
        except Exception as e:
            logger.error(f"Cache invalidate failed for {cache_key}: {str(e)}")

    
    def get_templates_by_type(self, template_type, organization_id=None):
        """Get all active templates of a specific type for an organization from cache or DB."""
        if not organization_id:
             logger.error("Organization ID is required to get templates by type")
             return []

        
        
        
        try:
            
            templates = Template.objects.filter(
                template_type=template_type,
                status=Template.STATUS_CHOICES.active,
                organization_id=organization_id 
            ).order_by('-updated_at')

            
            return [self._serialize_template(t) for t in templates]
        except Exception as e:
            logger.error(f"Database query failed for templates of type {template_type} (org: {organization_id}): {str(e)}", exc_info=True)
            return []

    
    def get_templates_by_tag(self, tag, organization_id=None):
        """Get all active templates with a specific tag for an organization from cache or DB."""
        if not organization_id:
             logger.error("Organization ID is required to get templates by tag")
             return []

        try:
            
            
            templates = Template.objects.filter(
                tags__contains=[tag], 
                status=Template.STATUS_CHOICES.active,
                organization_id=organization_id 
            ).order_by('-updated_at')

            
            return [self._serialize_template(t) for t in templates]
        except Exception as e:
            logger.error(f"Database query failed for templates with tag {tag} (org: {organization_id}): {str(e)}", exc_info=True)
            return []

    
    def get_all_templates(self, organization_id=None):
        """Get all active templates for an organization from DB."""
        if not organization_id:
             logger.error("Organization ID is required to get all templates")
             return []

        try:
            
            templates = Template.objects.filter(
                status=Template.STATUS_CHOICES.active,
                organization_id=organization_id 
            ).order_by('-updated_at')

            
            return [self._serialize_template(t) for t in templates]
        except Exception as e:
            logger.error(f"Database query failed for all templates (org: {organization_id}): {str(e)}", exc_info=True)
            return []

class TemplateVariablesValidationView(APIView):
    """
    Validate template variables
    Check if provided variables match template requirements
    """
    
    
    authentication_classes = [InternalAPIAuthentication]
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        operation_id='validate_template_variables',
        summary='Validate template variables',
        description='''
        Check if provided variables match template requirements.
        
        **Use case:** Call this before sending a notification to ensure you have all required variables.
        
        **Returns:**
        - valid: boolean indicating if all required variables are present
        - required_variables: list of all required variables
        - missing_variables: list of variables you need to provide
        - extra_variables: list of variables you provided that aren't required
        ''',
        tags=['Templates'],
        request=TemplateValidationRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=StandardResponseSerializer,
                description='Validation completed',
                examples=[
                    OpenApiExample(
                        'Valid Variables',
                        value={
                            'success': True,
                            'data': {
                                'valid': True,
                                'required_variables': ['name', 'company'],
                                'missing_variables': [],
                                'extra_variables': []
                            },
                            'message': 'Template variables validated',
                            'meta': {}
                        }
                    ),
                    OpenApiExample(
                        'Missing Variables',
                        value={
                            'success': True,
                            'data': {
                                'valid': False,
                                'required_variables': ['name', 'company', 'email'],
                                'missing_variables': ['email'],
                                'extra_variables': []
                            },
                            'message': 'Template variables validated',
                            'meta': {}
                        }
                    )
                ]
            ),
            400: OpenApiResponse(description='Bad request'),
            404: OpenApiResponse(description='Template not found'),
            500: OpenApiResponse(description='Internal server error'),
        },
        parameters=[
            OpenApiParameter(
                name='code',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                required=True,
                description='Template code'
            ),
            OpenApiParameter(
                name='X-Internal-Secret',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description='Internal service secret'
            ),
            OpenApiParameter(
                name='X-Organization-ID',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description='Organization ID'
            ),
            OpenApiParameter(
                name='language',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Template language',
                default='en'
            ),
        ]
    )
    
    def post(self, request, code):
        """Validate template variables"""
        try:
            
            org_id = request.headers.get('X-Organization-ID')
            if not org_id:
                 logger.warning(f"X-Organization-ID header missing for VARIABLE VALIDATION request")
                 return Response({
                     'success': False,
                     'error': 'missing_organization',
                     'message': 'X-Organization-ID header is required',
                     'meta': get_standard_meta()
                 }, status=status.HTTP_400_BAD_REQUEST)

            language = request.query_params.get('language', 'en')
            variables = request.data.get('variables', {})
            
            
            
            template = self.cache_manager.get_template(code, language, org_id) 

            if not template:
                return Response({
                    'success': False,
                    'error': 'template_not_found',
                    'message': f'Template "{code}" not found for your organization',
                    'meta': get_standard_meta()
                }, status=status.HTTP_404_NOT_FOUND)
            
            
            validation_result = self._validate_variables(template, variables)
            
            return Response({
                'success': True,
                'data': {
                    'required_variables': validation_result['required'],
                    'missing_variables': validation_result['missing'],
                    'extra_variables': validation_result['extra'],
                    'valid': validation_result['valid']
                },
                'message': 'Template variables validated',
                'meta': get_standard_meta()
            })
        
        except Exception as e:
            logger.error(f"Variable validation failed: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': 'internal_error',
                'message': 'Variable validation failed',
                'meta': get_standard_meta()
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _validate_variables(self, template, provided_variables):
        """Validate that all required variables are provided"""
        required_vars = set(getattr(template, 'variables', [])) 
        provided_vars = set(provided_variables.keys())
        
        missing = sorted(list(required_vars - provided_vars))
        extra = sorted(list(provided_vars - required_vars))
        
        return {
            'valid': len(missing) == 0,
            'required': sorted(list(required_vars)),
            'missing': missing,
            'extra': extra
        }



class InternalOrganizationSyncView(APIView):
    """
    Internal API endpoint for the notification gateway to sync
    organization data to the template service.
    Requires internal authentication.
    """
    
    authentication_classes = [InternalAPIAuthentication]
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        operation_id='sync_organization',
        summary='Sync organization to template service (Internal)',
        description='''
        **Internal endpoint called by the notification gateway.**
        
        When a new organization is created in the gateway, it syncs the organization
        data to the template service so templates can be scoped per organization.
        
        This ensures each organization has its own isolated set of templates.
        ''',
        tags=['Internal'],
        request=OrganizationSyncSerializer,
        responses={
            201: OpenApiResponse(
                description='Organization synced successfully',
                examples=[
                    OpenApiExample(
                        'Success',
                        value={
                            'success': True,
                            'data': {
                                'id': 'org_123',
                                'name': 'Acme Corp',
                                'plan': 'pro'
                            },
                            'message': 'Organization synced to template service database successfully',
                            'meta': {}
                        }
                    )
                ]
            ),
            400: OpenApiResponse(description='Bad request - missing required fields'),
            409: OpenApiResponse(description='Conflict - organization already exists'),
            500: OpenApiResponse(description='Internal server error'),
        },
        parameters=[
            OpenApiParameter(
                name='X-Internal-Secret',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description='Internal service secret'
            ),
        ]
    )

    def post(self, request):
        """Receive organization data from the gateway and create it in the template service's database."""
        try:
            org_data = request.data

            
            required_fields = ['id', 'name', 'api_key', 'plan', 'quota_limit', 'is_active', 'created_at']
            for field in required_fields:
                if field not in org_data:
                    logger.error(f"Missing required field '{field}' in organization sync data: {org_data}")
                    return Response({
                        'success': False,
                        'error': 'Missing required field',
                        'message': f'Missing: {field}',
                        'meta': get_standard_meta()
                    }, status=http_status.HTTP_400_BAD_REQUEST)

            
            org_id = org_data['id']
            try:
                existing_org = Organization.objects.get(id=org_id)
                logger.info(f"Org sync failed: Org {org_id} already exists in template service DB")
                return Response({
                    'success': False,
                    'error': 'Organization exists',
                    'message': 'Organization with this ID already exists in the template service',
                    'meta': get_standard_meta()
                }, status=http_status.HTTP_409_CONFLICT)
            except Organization.DoesNotExist:
                pass 

            
            
            from dateutil import parser
            new_org = Organization(
                id=org_data['id'],
                name=org_data['name'],
                api_key=org_data['api_key'],
                plan=org_data['plan'],
                quota_limit=org_data['quota_limit'],
                is_active=org_data['is_active'],
                
                created_at=parser.isoparse(org_data['created_at']) if isinstance(org_data['created_at'], str) else org_data['created_at']
            )
            new_org.save()

            logger.info(f"Organization synced to template service DB successfully: {org_data.get('id')}")
            return Response({
                'success': True,
                'data': org_data, 
                'message': 'Organization synced to template service database successfully',
                'meta': get_standard_meta()
            }, status=http_status.HTTP_201_CREATED)

        except ValidationError as e:
            logger.error(f"Validation error creating organization in template service: {str(e)}")
            return Response({
                'success': False,
                'error': 'Validation Error',
                'message': str(e),
                'meta': get_standard_meta()
            }, status=http_status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Failed to sync organization to template service DB: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': 'Internal Server Error',
                'message': 'An error occurred while syncing the organization',
                'meta': get_standard_meta()
            }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

