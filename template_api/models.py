
import uuid
import re
from django.db import models, transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db.models import F, Q, Count, Avg, Max
from model_utils import Choices


class Organization(models.Model):
    """Organization model for multi-tenancy"""
    
    PLAN_CHOICES = [
        ('pro', 'Pro'),
        ('enterprise', 'Enterprise'),
        ('bronze', 'Bronze'),
        ('platinum', 'Platinum'),
        ('industry', 'Industry'),
    ]

    id = models.CharField(max_length=36, primary_key=True)
    name = models.CharField(max_length=255)
    api_key = models.CharField(max_length=255, unique=True, db_index=True)
    plan = models.CharField(max_length=50, choices=PLAN_CHOICES)
    quota_limit = models.IntegerField(default=10000)
    quota_used = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'organizations'
        indexes = [
            models.Index(fields=['api_key', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.plan})"


class Template(models.Model):
    """Production-ready template model with versioning, metadata, and organization scoping"""
    
    TYPE_CHOICES = Choices(
        ('email', 'Email'),
        ('push', 'Push'),
        ('sms', 'SMS'),
    )
    
    STATUS_CHOICES = Choices(
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('archived', 'Archived'),
    )
    
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=255, db_index=True,
        help_text="Unique identifier for the template (e.g., welcome_email, password_reset)")
    name = models.CharField(max_length=255, help_text="Human-readable name")
    description = models.TextField(blank=True, help_text="Template description")
    template_type = models.CharField(max_length=20, choices=TYPE_CHOICES, db_index=True)
    subject = models.CharField(max_length=500, blank=True, help_text="Email subject line")
    content = models.TextField(help_text="Template content with {{variables}}")
    html_content = models.TextField(blank=True, help_text="HTML version for email templates")
    
    
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='templates',
        help_text="The organization this template belongs to",
        null=True,  
        blank=True,
    )
    
    
    language = models.CharField(max_length=10, default='en', db_index=True,
        help_text="Language code (e.g., en, fr, es)")
    
    
    version = models.IntegerField(default=1, db_index=True)
    is_default = models.BooleanField(default=False, db_index=True,
        help_text="Default version for this template/code/language combination within the organization")
    parent_template = models.ForeignKey('self', null=True, blank=True, 
        on_delete=models.SET_NULL, related_name='versions',
        help_text="Parent template for version tracking")
    
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_CHOICES.draft, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    
    
    variables = models.JSONField(default=list, help_text="List of required variables")
    optional_variables = models.JSONField(default=list, help_text="List of optional variables")
    metadata = models.JSONField(default=dict, blank=True,
        help_text="Additional template metadata (categories, tags, etc.)")
    tags = models.JSONField(default=list, blank=True,
        help_text="Searchable tags for template organization")
    
    
    created_by = models.CharField(max_length=255, blank=True)
    updated_by = models.CharField(max_length=255, blank=True)
    
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    
    
    usage_count = models.IntegerField(default=0, db_index=True)
    last_used_at = models.DateTimeField(null=True, blank=True, db_index=True)
    average_render_time = models.FloatField(default=0.0, help_text="Average render time in seconds")
    
    class Meta:
        db_table = 'templates'
        indexes = [
            models.Index(fields=['organization', 'code', 'status', 'language']),
            models.Index(fields=['organization', 'template_type', 'status']),
            models.Index(fields=['organization', 'status', '-created_at']),
            models.Index(fields=['organization', '-usage_count']),
            models.Index(fields=['organization', 'language', 'is_default']),
            models.Index(fields=['organization', '-last_used_at']),
            models.Index(fields=['organization', 'created_at']),
            models.Index(fields=['organization', 'tags']),
        ]
        unique_together = [
            ['code', 'version', 'language', 'organization'],
            ['code', 'language', 'is_default', 'organization']
        ]
        ordering = ['-version']
    
    def __str__(self):
        org_name = self.organization.name if self.organization else "No Org"
        return f"{org_name} - {self.code} v{self.version} ({self.language}) [{self.status}]"
    
    def clean(self):
        """Validate template content and extract variables"""
        if self.template_type == 'email' and not self.subject:
            raise ValidationError("Email templates must have a subject")
        
        self._extract_variables()
        
        if self.template_type == 'email' and self.html_content:
            self._validate_html_content()
    
    def _extract_variables(self):
        """Extract variables from template content"""
        pattern = r'\{\{(\w+)\}\}'
        content_vars = set(re.findall(pattern, self.content))
        
        if self.html_content:
            content_vars.update(re.findall(pattern, self.html_content))
        
        if self.subject:
            content_vars.update(re.findall(pattern, self.subject))
        
        required_vars = [var for var in content_vars if not var.startswith('_')]
        optional_vars = [var for var in content_vars if var.startswith('_')]
        
        self.variables = sorted(required_vars)
        self.optional_variables = sorted(optional_vars)
    
    def _validate_html_content(self):
        """Basic HTML validation for email templates"""
        if '<html' not in self.html_content.lower():
            raise ValidationError("HTML content should contain <html> tag")
    
    def activate(self, updated_by=None):
        """Activate this template version within its organization"""
        if self.status == Template.STATUS_CHOICES.active:
            return False

        if not self.organization:
            raise ValidationError("Template must be associated with an organization to activate.")

        with transaction.atomic():
            Template.objects.filter(
                code=self.code,
                language=self.language,
                is_default=True,
                organization=self.organization
            ).exclude(id=self.id).update(
                is_default=False,
                updated_by=updated_by
            )

            self.status = Template.STATUS_CHOICES.active
            self.published_at = timezone.now()
            self.is_default = True
            self.updated_by = updated_by
            self.save()

            return True
    
    def archive(self, updated_by=None):
        """Archive this template version within its organization"""
        if self.status == Template.STATUS_CHOICES.archived:
            return False

        if not self.organization:
            raise ValidationError("Template must be associated with an organization to archive.")

        if self.is_default:
            other_active = Template.objects.filter(
                code=self.code,
                language=self.language,
                status=Template.STATUS_CHOICES.active,
                organization=self.organization
            ).exclude(id=self.id).exists()
            
            if not other_active:
                raise ValidationError("Cannot archive the default template version. Activate another version first.")
        
        self.status = Template.STATUS_CHOICES.archived
        self.deactivated_at = timezone.now()
        self.is_default = False
        self.updated_by = updated_by
        self.save()
        return True
    
    def increment_usage(self, render_time):
        """Increment usage counter and update average render time"""
        Template.objects.filter(id=self.id).update(
            usage_count=F('usage_count') + 1,
            last_used_at=timezone.now(),
            average_render_time=(
                (F('average_render_time') * F('usage_count') + render_time) /
                (F('usage_count') + 1)
            )
        )
    
    @classmethod
    def get_active_template(cls, code, language='en', organization_id=None):
        """Get the active template for a given code, language, and organization."""
        if not organization_id:
            raise ValueError("Organization ID is required to fetch a template.")

        
        template = cls.objects.filter(
            code=code,
            language=language,
            status=cls.STATUS_CHOICES.active,
            is_default=True,
            organization_id=organization_id
        ).first()

        if template:
            return template

        
        if language != 'en':
            template = cls.objects.filter(
                code=code,
                language='en',
                status=cls.STATUS_CHOICES.active,
                is_default=True,
                organization_id=organization_id
            ).first()

            if template:
                return template

        
        return cls.objects.filter(
            code=code,
            status=cls.STATUS_CHOICES.active,
            organization_id=organization_id
        ).order_by('-version').first()
    
    def to_dict(self):
        """Convert template to dictionary for caching"""
        return {
            'id': str(self.id),
            'code': self.code,
            'name': self.name,
            'description': self.description,
            'type': self.template_type,
            'subject': self.subject,
            'content': self.content,
            'html_content': self.html_content,
            'language': self.language,
            'version': self.version,
            'variables': self.variables,
            'optional_variables': self.optional_variables,
            'metadata': self.metadata,
            'tags': self.tags,
            'status': self.status,
            'is_default': self.is_default,
            'organization_id': str(self.organization.id) if self.organization else None,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'usage_count': self.usage_count,
            'last_used_at': self.last_used_at.isoformat() if self.last_used_at else None,
            'average_render_time': self.average_render_time,
        }


class TemplateUsageLog(models.Model):
    """Track template usage for analytics, monitoring and debugging"""
    
    RESULT_CHOICES = Choices(
        ('success', 'Success'),
        ('variable_missing', 'Variable Missing'),
        ('render_error', 'Render Error'),
        ('template_not_found', 'Template Not Found'),
        ('timeout', 'Timeout'),
        ('rate_limited', 'Rate Limited'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(Template, on_delete=models.SET_NULL, null=True, 
                               blank=True, related_name='usage_logs',
                               help_text="Template used for this render")
    template_code = models.CharField(max_length=255, db_index=True,
                                   help_text="Template code (cached for analytics)")
    template_version = models.IntegerField(null=True, blank=True,
                                         help_text="Template version at time of render")
    notification_id = models.CharField(max_length=255, db_index=True,
                                      help_text="Correlation ID from notification system")
    organization_id = models.CharField(max_length=255, db_index=True,
                                      help_text="Organization ID from gateway")
    rendered_at = models.DateTimeField(auto_now_add=True, db_index=True)
    render_time = models.FloatField(help_text="Time taken to render template in seconds")
    result = models.CharField(max_length=20, choices=RESULT_CHOICES, db_index=True)
    error_message = models.TextField(blank=True, help_text="Error details if render failed")
    variables_used = models.JSONField(default=dict, blank=True,
                                    help_text="Variables actually used in render")
    variables_missing = models.JSONField(default=list, blank=True,
                                       help_text="Variables that were missing")
    template_type = models.CharField(max_length=20, choices=Template.TYPE_CHOICES, 
                                   db_index=True, help_text="Template type at time of render")
    language = models.CharField(max_length=10, default='en', db_index=True,
                              help_text="Language used for render")
    
    class Meta:
        db_table = 'template_usage_logs'
        indexes = [
            models.Index(fields=['-rendered_at']),
            models.Index(fields=['organization_id', '-rendered_at']),
            models.Index(fields=['template_code', '-rendered_at']),
            models.Index(fields=['result', '-rendered_at']),
            models.Index(fields=['notification_id']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(render_time__gte=0),
                name='render_time_positive'
            ),
            models.CheckConstraint(
                check=models.Q(rendered_at__lte=timezone.now()),
                name='rendered_at_in_past'
            ),
        ]
    
    def __str__(self):
        return f"{self.template_code} {self.result} {self.rendered_at}"
    
    @classmethod
    def cleanup_old_logs(cls, days=90):
        """Clean up old logs to prevent table bloat"""
        cutoff_date = timezone.now() - timezone.timedelta(days=days)
        deleted_count, _ = cls.objects.filter(rendered_at__lt=cutoff_date).delete()
        return deleted_count
    
    @classmethod
    def get_usage_stats(cls, template_code=None, organization_id=None, days=30):
        """Get usage statistics for templates"""
        cutoff_date = timezone.now() - timezone.timedelta(days=days)
        
        queryset = cls.objects.filter(rendered_at__gte=cutoff_date)
        
        if template_code:
            queryset = queryset.filter(template_code=template_code)
        
        if organization_id:
            queryset = queryset.filter(organization_id=organization_id)
        
        
        total_count = queryset.count()
        if total_count == 0:
            return {
                'total_renders': 0,
                'success_rate': 0,
                'avg_render_time': 0,
                'error_count': 0,
                'error_breakdown': [],
                'period_days': days
            }
        
        success_count = queryset.filter(result=cls.RESULT_CHOICES.success).count()
        avg_time = queryset.aggregate(avg=Avg('render_time'))['avg'] or 0
        error_count = queryset.exclude(result=cls.RESULT_CHOICES.success).count()
        
        
        error_breakdown = queryset.exclude(
            result=cls.RESULT_CHOICES.success
        ).values('result').annotate(count=Count('id')).order_by('-count')
        
        return {
            'total_renders': total_count,
            'success_rate': round((success_count / total_count) * 100, 2) if total_count > 0 else 0,
            'avg_render_time': round(avg_time, 4),
            'error_count': error_count,
            'error_breakdown': list(error_breakdown),
            'period_days': days
        }