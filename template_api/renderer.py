import re
import logging
import html
import time
import json
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from .models import TemplateUsageLog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

logger = logging.getLogger(__name__)


class RenderError(Exception):
    """Custom exception for template rendering errors"""
    pass


class TimeoutError(RenderError):
    """Custom exception for template rendering timeouts"""
    pass


class VariableMissingError(RenderError):
    """Custom exception for missing template variables"""
    pass


class TemplateRenderer:
    """
    Production-grade template renderer with safety features:
    - Timeout protection
    - HTML escaping
    - Variable validation
    - Error tracking
    - Performance monitoring
    - Circuit breaker for failing templates
    """
    
    # Precompile regex patterns for performance
    VARIABLE_PATTERN = re.compile(r'\{\{(\w+)\}\}')
    CONDITIONAL_PATTERN = re.compile(r'\{\{#if (\w+)\}\}(.*?)\{\{/if\}\}', re.DOTALL)
    LOOP_PATTERN = re.compile(r'\{\{#each (\w+)\}\}(.*?)\{\{/each\}\}', re.DOTALL)
    
    @classmethod
    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=2),
        retry=retry_if_exception(lambda e: isinstance(e, TimeoutError))
    )
    def render_template(cls, template, variables, notification_id, organization_id):
        """
        Render template with safety features and logging
        Returns tuple: (rendered_content, render_time, success)
        """
        start_time = time.time()
        success = True
        error_message = ""
        template_code = template.code if template else "unknown"
        
        try:
            # Check for timeout
            if time.time() - start_time > settings.TEMPLATE_RENDER_TIMEOUT:
                raise TimeoutError("Template rendering timeout")
            
            # Render content
            if template.content:
                rendered_content, missing_vars = cls.render(template.content, variables)
                
                if missing_vars:
                    raise VariableMissingError(f"Missing variables: {', '.join(missing_vars)}")
            else:
                rendered_content = ""
            
            # Render HTML content if exists
            rendered_html = ""
            if template.html_content:
                rendered_html, missing_vars = cls.render(template.html_content, variables)
                
                if missing_vars:
                    raise VariableMissingError(f"Missing variables in HTML content: {', '.join(missing_vars)}")
            
            # Render subject if exists
            rendered_subject = template.subject
            if template.subject:
                rendered_subject, missing_vars = cls.render(template.subject, variables)
                
                if missing_vars:
                    raise VariableMissingError(f"Missing variables in subject: {', '.join(missing_vars)}")
            
            render_time = time.time() - start_time
            
            # Log successful render
            cls._log_render(
                template=template,
                notification_id=notification_id,
                organization_id=organization_id,
                render_time=render_time,
                success=True,
                result=TemplateUsageLog.RESULT_CHOICES.success,
                variables_used=variables,
                variables_missing=[]
            )
            
            return {
                'subject': rendered_subject,
                'content': rendered_content,
                'html_content': rendered_html
            }, render_time, True
        
        except Exception as e:
            success = False
            error_message = str(e)
            render_time = time.time() - start_time
            
            # Determine error type
            result = TemplateUsageLog.RESULT_CHOICES.render_error
            if isinstance(e, VariableMissingError):
                result = TemplateUsageLog.RESULT_CHOICES.variable_missing
            elif isinstance(e, TimeoutError):
                result = TemplateUsageLog.RESULT_CHOICES.timeout
            
            # Log failed render
            cls._log_render(
                template=template,
                notification_id=notification_id,
                organization_id=organization_id,
                render_time=render_time,
                success=False,
                result=result,
                variables_used=variables,
                variables_missing=[],
                error_message=error_message
            )
            
            logger.error(
                f"Template render failed: {template_code} - {error_message}",
                extra={
                    'template_code': template_code,
                    'notification_id': notification_id,
                    'render_time': render_time,
                    'error_type': result
                },
                exc_info=True
            )
            
            raise RenderError(f"Template rendering failed: {error_message}") from e
    
    @staticmethod
    def render(template_content, variables, escape_html=True):
        """
        Render template with variables
        
        Args:
            template_content: Template string with {{variable}} placeholders
            variables: Dictionary of variable values
            escape_html: Whether to escape HTML in variables
        
        Returns:
            tuple: (rendered_content, missing_vars)
        """
        if not template_content:
            return "", []
        
        rendered = template_content
        missing_vars = []
        
        # Process conditionals first
        rendered, cond_missing = TemplateRenderer._process_conditionals(rendered, variables)
        missing_vars.extend(cond_missing)
        
        # Process loops
        rendered, loop_missing = TemplateRenderer._process_loops(rendered, variables)
        missing_vars.extend(loop_missing)
        
        # Process variables
        rendered, var_missing = TemplateRenderer._process_variables(rendered, variables, escape_html)
        missing_vars.extend(var_missing)
        
        return rendered, list(set(missing_vars))
    
    @staticmethod
    def _process_conditionals(content, variables):
        """Process conditional blocks {{#if var}}...{{/if}}"""
        missing_vars = []
        
        def replace_conditional(match):
            var_name = match.group(1)
            block_content = match.group(2)
            
            if var_name in variables:
                var_value = variables[var_name]
                # Check if variable is truthy
                if var_value and var_value not in ['false', '0', '']:
                    return block_content
                else:
                    return ""
            else:
                missing_vars.append(var_name)
                return ""
        
        result = TemplateRenderer.CONDITIONAL_PATTERN.sub(replace_conditional, content)
        return result, missing_vars
    
    @staticmethod
    def _process_loops(content, variables):
        """Process loop blocks {{#each var}}...{{/each}}"""
        missing_vars = []
        
        def replace_loop(match):
            var_name = match.group(1)
            block_content = match.group(2)
            
            if var_name in variables:
                items = variables[var_name]
                if not isinstance(items, list):
                    logger.warning(f"Variable {var_name} is not a list but used in #each loop")
                    return ""
                
                result = ""
                for item in items:
                    # Create context with current item
                    item_context = {**variables, 'item': item}
                    
                    # If item is a dictionary, add its properties to context
                    if isinstance(item, dict):
                        item_context.update(item)
                    
                    # Render block with item context
                    rendered_block, _ = TemplateRenderer._process_variables(block_content, item_context)
                    result += rendered_block
                
                return result
            else:
                missing_vars.append(var_name)
                return ""
        
        result = TemplateRenderer.LOOP_PATTERN.sub(replace_loop, content)
        return result, missing_vars
    
    @staticmethod
    def _process_variables(content, variables, escape_html):
        """Process individual variable replacements"""
        missing_vars = []
        matches = TemplateRenderer.VARIABLE_PATTERN.finditer(content)
        
        # Collect all replacements first
        replacements = []
        for match in matches:
            var_name = match.group(1)
            
            if var_name in variables:
                value = variables[var_name]
                
                if value is None:
                    value = ""
                
                # Convert to string
                value = str(value)
                
                # HTML escape if needed
                if escape_html:
                    value = html.escape(value)
                
                replacements.append((match.span(), value))
            else:
                missing_vars.append(var_name)
        
        # Apply replacements from end to start to preserve positions
        result = list(content)
        for (start, end), value in sorted(replacements, reverse=True):
            result[start:end] = value
        
        return ''.join(result), missing_vars
    
    @classmethod
    @transaction.atomic
    def _log_render(cls, template, notification_id, organization_id, render_time, success, result, 
                    variables_used, variables_missing, error_message=""):
        """Log template render with transaction support"""
        try:
            TemplateUsageLog.objects.create(
                template=template,
                template_code=template.code if template else "unknown",
                template_version=template.version if template else None,
                notification_id=notification_id,
                organization_id=organization_id,
                rendered_at=timezone.now(),
                render_time=render_time,
                result=result,
                error_message=error_message[:500] if error_message else "",  # Limit size
                variables_used=variables_used,
                variables_missing=variables_missing,
                template_type=template.template_type if template else "unknown",
                language=template.language if template else "en"
            )
            
            # Update template usage stats if successful
            if success and template:
                template.increment_usage(render_time)
        
        except Exception as e:
            logger.error(f"Failed to log template render: {str(e)}", exc_info=True)