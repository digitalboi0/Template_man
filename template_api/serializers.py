

from rest_framework import serializers


class TemplateCreateSerializer(serializers.Serializer):
    """Serializer for creating templates"""
    code = serializers.CharField(
        required=True,
        max_length=100,
        help_text="Unique template code identifier"
    )
    name = serializers.CharField(
        required=True,
        max_length=200,
        help_text="Human-readable template name"
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Template description"
    )
    type = serializers.ChoiceField(
        choices=['email', 'push', 'sms'],
        required=True,
        help_text="Type of notification template"
    )
    subject = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Email subject line (supports {{variables}})"
    )
    content = serializers.CharField(
        required=True,
        help_text="Template content with {{variable}} placeholders"
    )
    html_content = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="HTML version of template content"
    )
    language = serializers.CharField(
        required=False,
        default='en',
        max_length=10,
        help_text="Template language code (e.g., en, es, fr)"
    )
    variables = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
        help_text="List of required variable names"
    )
    optional_variables = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
        help_text="List of optional variable names"
    )
    metadata = serializers.DictField(
        required=False,
        default=dict,
        help_text="Additional metadata for the template"
    )
    tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
        help_text="Tags for organizing templates"
    )


class TemplateUpdateSerializer(serializers.Serializer):
    """Serializer for updating templates (creates new version)"""
    name = serializers.CharField(required=False, max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    subject = serializers.CharField(required=False, allow_blank=True)
    content = serializers.CharField(required=False)
    html_content = serializers.CharField(required=False, allow_blank=True)
    language = serializers.CharField(required=False, max_length=10)
    variables = serializers.ListField(child=serializers.CharField(), required=False)
    optional_variables = serializers.ListField(child=serializers.CharField(), required=False)
    metadata = serializers.DictField(required=False)
    tags = serializers.ListField(child=serializers.CharField(), required=False)


class TemplateLifecycleSerializer(serializers.Serializer):
    """Serializer for template lifecycle management"""
    action = serializers.ChoiceField(
        choices=['publish', 'archive'],
        required=True,
        help_text="Action to perform (publish or archive)"
    )
    language = serializers.CharField(
        required=False,
        default='en',
        max_length=10,
        help_text="Template language"
    )


class TemplateResponseSerializer(serializers.Serializer):
    """Serializer for template response"""
    id = serializers.CharField()
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    type = serializers.CharField()
    subject = serializers.CharField()
    content = serializers.CharField()
    html_content = serializers.CharField()
    language = serializers.CharField()
    version = serializers.IntegerField()
    variables = serializers.ListField(child=serializers.CharField())
    optional_variables = serializers.ListField(child=serializers.CharField())
    metadata = serializers.DictField()
    tags = serializers.ListField(child=serializers.CharField())
    status = serializers.CharField()
    is_default = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    published_at = serializers.DateTimeField(allow_null=True)
    usage_count = serializers.IntegerField()
    last_used_at = serializers.DateTimeField(allow_null=True)
    average_render_time = serializers.FloatField(allow_null=True)
    organization_id = serializers.CharField()


class TemplateRenderRequestSerializer(serializers.Serializer):
    """Serializer for template render requests"""
    template_code = serializers.CharField(
        required=True,
        help_text="Template code to render"
    )
    language = serializers.CharField(
        required=False,
        default='en',
        help_text="Template language"
    )
    variables = serializers.DictField(
        required=True,
        help_text="Variables to substitute in template"
    )
    notification_id = serializers.CharField(
        required=True,
        help_text="Notification ID for tracking"
    )
    organization_id = serializers.CharField(
        required=True,
        help_text="Organization ID"
    )


class TemplateRenderResponseSerializer(serializers.Serializer):
    """Serializer for template render response"""
    subject = serializers.CharField()
    content = serializers.CharField()
    html_content = serializers.CharField()
    template_id = serializers.CharField()
    template_version = serializers.IntegerField()
    render_time = serializers.FloatField()


class TemplateValidationRequestSerializer(serializers.Serializer):
    """Serializer for template variable validation"""
    variables = serializers.DictField(
        required=True,
        help_text="Variables to validate against template"
    )


class TemplateValidationResponseSerializer(serializers.Serializer):
    """Serializer for template validation response"""
    valid = serializers.BooleanField()
    required_variables = serializers.ListField(child=serializers.CharField())
    missing_variables = serializers.ListField(child=serializers.CharField())
    extra_variables = serializers.ListField(child=serializers.CharField())


class OrganizationSyncSerializer(serializers.Serializer):
    """Serializer for organization sync data"""
    id = serializers.CharField(required=True)
    name = serializers.CharField(required=True)
    api_key = serializers.CharField(required=True)
    plan = serializers.CharField(required=True)
    quota_limit = serializers.IntegerField(required=True)
    is_active = serializers.BooleanField(required=True)
    created_at = serializers.DateTimeField(required=True)


class StandardResponseSerializer(serializers.Serializer):
    """Standard API response wrapper"""
    success = serializers.BooleanField()
    data = serializers.DictField(required=False)
    message = serializers.CharField()
    error = serializers.CharField(required=False)
    meta = serializers.DictField()


class HealthCheckSerializer(serializers.Serializer):
    """Serializer for health check response"""
    status = serializers.CharField()
    timestamp = serializers.DateTimeField()
    service = serializers.CharField()
    version = serializers.CharField()
    checks = serializers.DictField()
    cache_stats = serializers.DictField(required=False)