

import datetime
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('template_api', '0001_initial'),
    ]

    operations = [
        
        migrations.CreateModel(
            name='Organization',
            fields=[
                ('id', models.CharField(max_length=36, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('api_key', models.CharField(db_index=True, max_length=255, unique=True)),
                ('plan', models.CharField(choices=[('pro', 'Pro'), ('enterprise', 'Enterprise'), ('bronze', 'Bronze'), ('platinum', 'Platinum'), ('industry', 'Industry')], max_length=50)),
                ('quota_limit', models.IntegerField(default=10000)),
                ('quota_used', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'organizations',
            },
        ),
        
        
        migrations.AddIndex(
            model_name='organization',
            index=models.Index(fields=['api_key', 'is_active'], name='organizatio_api_key_7e525b_idx'),
        ),
        
        
        migrations.RemoveConstraint(
            model_name='templateusagelog',
            name='rendered_at_in_past',
        ),
        migrations.RemoveIndex(
            model_name='template',
            name='templates_code_45787c_idx',
        ),
        migrations.RemoveIndex(
            model_name='template',
            name='templates_templat_3f0ace_idx',
        ),
        migrations.RemoveIndex(
            model_name='template',
            name='templates_status_c9fdcd_idx',
        ),
        migrations.RemoveIndex(
            model_name='template',
            name='templates_usage_c_47fafd_idx',
        ),
        migrations.RemoveIndex(
            model_name='template',
            name='templates_languag_5c13a4_idx',
        ),
        migrations.RemoveIndex(
            model_name='template',
            name='templates_last_us_d867bb_idx',
        ),
        migrations.RemoveIndex(
            model_name='template',
            name='templates_created_2dfe96_idx',
        ),
        migrations.RemoveIndex(
            model_name='template',
            name='templates_tags_54e72a_idx',
        ),
        
        
        migrations.AlterField(
            model_name='template',
            name='is_default',
            field=models.BooleanField(db_index=True, default=False, help_text='Default version for this template/code/language combination within the organization'),
        ),
        
        
        migrations.AlterUniqueTogether(
            name='template',
            unique_together=set(),
        ),
        
        
        migrations.AddField(
            model_name='template',
            name='organization',
            field=models.ForeignKey(blank=True, help_text='The organization this template belongs to', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='templates', to='template_api.organization'),
        ),
        
        
        migrations.AddIndex(
            model_name='template',
            index=models.Index(fields=['organization', 'code', 'status', 'language'], name='templates_organiz_5162c6_idx'),
        ),
        migrations.AddIndex(
            model_name='template',
            index=models.Index(fields=['organization', 'template_type', 'status'], name='templates_organiz_d66a9e_idx'),
        ),
        migrations.AddIndex(
            model_name='template',
            index=models.Index(fields=['organization', 'status', '-created_at'], name='templates_organiz_443214_idx'),
        ),
        migrations.AddIndex(
            model_name='template',
            index=models.Index(fields=['organization', '-usage_count'], name='templates_organiz_0b2353_idx'),
        ),
        migrations.AddIndex(
            model_name='template',
            index=models.Index(fields=['organization', 'language', 'is_default'], name='templates_organiz_79c2a1_idx'),
        ),
        migrations.AddIndex(
            model_name='template',
            index=models.Index(fields=['organization', '-last_used_at'], name='templates_organiz_860a04_idx'),
        ),
        migrations.AddIndex(
            model_name='template',
            index=models.Index(fields=['organization', 'created_at'], name='templates_organiz_8e3f01_idx'),
        ),
        migrations.AddIndex(
            model_name='template',
            index=models.Index(fields=['organization', 'tags'], name='templates_organiz_a36498_idx'),
        ),
        
        
        migrations.AlterUniqueTogether(
            name='template',
            unique_together={('code', 'language', 'is_default', 'organization'), ('code', 'version', 'language', 'organization')},
        ),
        
        
        migrations.AddConstraint(
            model_name='templateusagelog',
            constraint=models.CheckConstraint(check=models.Q(('rendered_at__lte', datetime.datetime(2025, 11, 12, 23, 29, 31, 181876, tzinfo=datetime.timezone.utc))), name='rendered_at_in_past'),
        ),
    ]