from django.core.management.base import BaseCommand
from django.core.exceptions import ValidationError
from template_api.models import Template
import json
import argparse

class Command(BaseCommand):
    help = 'Add a new notification template'
    
    def add_arguments(self, parser):
        parser.add_argument('code', type=str, help='Template code (e.g., welcome_email)')
        parser.add_argument('name', type=str, help='Template name')
        parser.add_argument('content', type=str, help='Template content')
        parser.add_argument('--type', type=str, default='email', 
                           choices=['email', 'push', 'sms'], help='Template type')
        parser.add_argument('--subject', type=str, default='', help='Email subject (for email templates)')
        parser.add_argument('--html-content', type=str, default='', help='HTML content (for email templates)')
        parser.add_argument('--variables', type=str, default='[]', 
                           help='JSON array of variable names (e.g., ["name", "company"])')
        parser.add_argument('--org-id', type=str, required=True, help='Organization ID')
        parser.add_argument('--version', type=int, default=1, help='Template version')
        parser.add_argument('--language', type=str, default='en', help='Template language')
        parser.add_argument('--description', type=str, default='', help='Template description')
        parser.add_argument('--tags', type=str, default='[]', help='JSON array of tags (e.g., ["marketing", "welcome"])')
        parser.add_argument('--status', type=str, default='draft', 
                           choices=['draft', 'active', 'archived'], help='Template status')
        parser.add_argument('--is-default', action='store_true', help='Set as default version')
        parser.add_argument('--created-by', type=str, default='system', help='Created by user/identifier')

    def handle(self, *args, **options):
        try:
            
            try:
                variables = json.loads(options['variables'])
                tags = json.loads(options['tags'])
            except json.JSONDecodeError as e:
                raise ValidationError(f'Invalid JSON format: {e}')
            
            
            template = Template(
                code=options['code'],
                name=options['name'],
                description=options['description'],
                template_type=options['type'],
                subject=options['subject'],
                content=options['content'],
                html_content=options['html_content'],
                variables=variables,
                optional_variables=[],
                metadata={},
                tags=tags,
                organization_id=options['org_id'],
                version=options['version'],
                language=options['language'],
                status=options['status'],
                is_default=options['is_default'],
                created_by=options['created_by'],
                updated_by=options['created_by']
            )
            
            
            template.clean()
            template.save()
            
            if options['status'] == 'active':
                template.activate(updated_by=options['created_by'])
            
            self.stdout.write(self.style.SUCCESS(
                f'Successfully created template: {template.name} (v{template.version})\n'
                f'ID: {template.id}\n'
                f'Type: {template.template_type}\n'
                f'Status: {template.status}\n'
                f'Variables: {", ".join(template.variables) if template.variables else "None"}'
            ))
            
        except ValidationError as e:
            self.stdout.write(self.style.ERROR(f'Validation error: {e}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {str(e)}'))