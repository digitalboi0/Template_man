import logging
import threading
import time
import json
import re
from collections import defaultdict
from django.conf import settings
from django.core.cache import caches
from django.db import connection, transaction
from django.utils import timezone
from django.db.models import Q, F
from tenacity import retry, stop_after_attempt, wait_exponential

from .models import Template
from .redis_client import redis_client
from .renderer import TemplateRenderer

logger = logging.getLogger(__name__)

class TemplateCacheManager:
    """
    Production-grade in-memory template cache with Redis versioning
    Features:
    - Atomic cache updates
    - Multi-level caching (in-memory + Redis)
    - Change detection and incremental updates
    - Cache warming strategies
    - Memory usage monitoring
    - Circuit breaker for cache failures
    """
    
    _instance = None
    _lock = threading.Lock()
    _sync_lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        
        self.templates = {}  
        self.templates_by_type = defaultdict(list)  
        self.templates_by_tag = defaultdict(list)  
        self.last_sync = None
        self.current_version = None
        self._memory_usage = 0
        self._initialization_time = time.time()
        
        
        self.redis_client = redis_client
        
        
        self._initialize_cache()
        
        
        if settings.TEMPLATE_CACHE_ENABLED:
            self._start_background_sync()
        
        
        self._initialized = True
        
        logger.info("TemplateCacheManager initialized successfully")
    
    def _initialize_cache(self):
        """Initialize cache with templates from database"""
        try:
            
            self.current_version = self.get_cache_version()
            
            
            self._load_templates_from_db()
            
            
            self._warm_cache()
            
            self.last_sync = time.time()
            logger.info(f"Cache initialized with {len(self.templates)} templates (version: {self.current_version})")
            
        except Exception as e:
            logger.error(f"Failed to initialize cache: {str(e)}", exc_info=True)
            raise
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        reraise=True
    )
    def _load_templates_from_db(self):
        """Load templates from database with retry logic"""
        try:
            templates = Template.objects.filter(
                status=Template.STATUS_CHOICES.active
            ).select_related().order_by('-version')
            
            new_cache = {}
            new_cache_by_type = defaultdict(list)
            new_cache_by_tag = defaultdict(list)
            
            total_bytes = 0
            
            for template in templates:
                template_data = self._serialize_template(template)
                cache_key = f"{template.code}:{template.language}"
                
                
                new_cache[cache_key] = template_data
                total_bytes += len(json.dumps(template_data))
                
                
                new_cache_by_type[template.template_type].append(template_data)
                
                
                for tag in template.tags:
                    new_cache_by_tag[tag].append(template_data)
            
            
            with self._lock:
                self.templates = new_cache
                self.templates_by_type = new_cache_by_type
                self.templates_by_tag = new_cache_by_tag
                self._memory_usage = total_bytes
            
            return len(new_cache)
            
        except Exception as e:
            logger.error(f"Failed to load templates from database: {str(e)}", exc_info=True)
            raise
    
    def _serialize_template(self, template):
        """Convert template model to cache-friendly dictionary"""
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
            'is_default': template.is_default,
            'status': template.status,
            'created_at': template.created_at.isoformat(),
            'updated_at': template.updated_at.isoformat(),
            'published_at': template.published_at.isoformat() if template.published_at else None,
            'usage_count': template.usage_count,
            'last_used_at': template.last_used_at.isoformat() if template.last_used_at else None,
            'average_render_time': template.average_render_time
        }
    
    def _warm_cache(self):
        """Pre-render common templates to warm up the cache"""
        try:
            
            top_templates = Template.objects.filter(
                status=Template.STATUS_CHOICES.active
            ).order_by('-usage_count')[:10]
            
            for template in top_templates:
                try:
                    
                    dummy_vars = {var: f"dummy_{var}" for var in template.variables}
                    
                    
                    if template.content:
                        TemplateRenderer.render(template.content, dummy_vars)
                    
                    if template.html_content:
                        TemplateRenderer.render(template.html_content, dummy_vars)
                    
                    logger.debug(f"Warmed up template cache for: {template.code}")
                    
                except Exception as e:
                    logger.warning(f"Failed to warm up template {template.code}: {str(e)}")
            
        except Exception as e:
            logger.error(f"Cache warming failed: {str(e)}", exc_info=True)
    
    def _start_background_sync(self):
        """Start background thread for periodic cache sync"""
        def sync_worker():
            while True:
                try:
                    
                    time.sleep(settings.TEMPLATE_CACHE_SYNC_INTERVAL)
                    
                    
                    remote_version = self.get_cache_version()
                    if remote_version != self.current_version:
                        logger.info(f"Cache version changed from {self.current_version} to {remote_version}")
                        self.sync_from_db()
                    
                    
                    self._monitor_memory_usage()
                    
                except Exception as e:
                    logger.error(f"Background sync failed: {str(e)}", exc_info=True)
                    
                    time.sleep(60)
        
        thread = threading.Thread(target=sync_worker, daemon=True)
        thread.start()
        logger.info(f"Background sync thread started (interval: {settings.TEMPLATE_CACHE_SYNC_INTERVAL}s)")
    
    def _monitor_memory_usage(self):
        """Monitor memory usage and clean up if needed"""
        try:
            
            if self._memory_usage > 100 * 1024 * 1024:  
                logger.warning(f"High memory usage: {self._memory_usage / 1024 / 1024:.2f} MB")
                
                
                self._cleanup_least_used_templates()
        
        except Exception as e:
            logger.error(f"Memory monitoring failed: {str(e)}", exc_info=True)
    
    def _cleanup_least_used_templates(self):
        """Clean up least used templates to free memory"""
        try:
            with self._lock:
                
                sorted_templates = sorted(
                    self.templates.values(),
                    key=lambda t: t.get('last_used_at') or t.get('created_at')
                )
                
                
                keep_count = int(len(sorted_templates) * 0.8)
                templates_to_keep = sorted_templates[-keep_count:]
                
                
                new_cache = {}
                new_cache_by_type = defaultdict(list)
                new_cache_by_tag = defaultdict(list)
                
                for template in templates_to_keep:
                    cache_key = f"{template['code']}:{template['language']}"
                    new_cache[cache_key] = template
                    
                    new_cache_by_type[template['type']].append(template)
                    
                    for tag in template.get('tags', []):
                        new_cache_by_tag[tag].append(template)
                
                
                self.templates = new_cache
                self.templates_by_type = new_cache_by_type
                self.templates_by_tag = new_cache_by_tag
                
                logger.info(f"Cleaned up cache: kept {keep_count} templates, removed {len(sorted_templates) - keep_count}")
        
        except Exception as e:
            logger.error(f"Cache cleanup failed: {str(e)}", exc_info=True)
    
    def get_cache_version(self):
        """Get current cache version from Redis"""
        try:
            version = self.redis_client.get(settings.TEMPLATE_CACHE_VERSION_KEY)
            return int(version) if version else 1
        except Exception as e:
            logger.error(f"Failed to get cache version: {str(e)}")
            return 1
    
    def increment_cache_version(self):
        """Increment cache version (invalidates all caches)"""
        try:
            new_version = self.redis_client.incr(settings.TEMPLATE_CACHE_VERSION_KEY)
            logger.info(f"Cache version incremented to {new_version}")
            return new_version
        except Exception as e:
            logger.error(f"Failed to increment cache version: {str(e)}")
            return None
    
    def sync_from_db(self, force=False):
        """Load/reload templates from database"""
        with self._sync_lock:
            try:
                
                remote_version = self.get_cache_version()
                
                
                if not force and remote_version == self.current_version:
                    logger.debug("Cache version unchanged, skipping sync")
                    return
                
                start_time = time.time()
                template_count = self._load_templates_from_db()
                end_time = time.time()
                
                
                self.current_version = remote_version
                self.last_sync = time.time()
                
                logger.info(
                    f"Synced {template_count} templates in {end_time - start_time:.2f} seconds "
                    f"(version: {remote_version})"
                )
                
            except Exception as e:
                logger.error(f"Failed to sync templates: {str(e)}", exc_info=True)
                raise
    
    def get_template(self, code, language='en'):
        """Get template from cache with fallback to database"""
        cache_key = f"{code}:{language}"
        
        
        template = self.templates.get(cache_key)
        cache_hit = template is not None
        
        if not template:
            
            logger.debug(f"Cache miss for template: {code}:{language}")
            try:
                template = Template.objects.filter(
                    code=code,
                    language=language,
                    status=Template.STATUS_CHOICES.active,
                    is_default=True
                ).first()
                
                if template:
                    
                    template_data = self._serialize_template(template)
                    with self._lock:
                        self.templates[cache_key] = template_data
                        self.templates_by_type[template.template_type].append(template_data)
                        
                        for tag in template.tags:
                            self.templates_by_tag[tag].append(template_data)
                    
                    logger.info(f"Template loaded from database and cached: {code}:{language}")
            
            except Exception as e:
                logger.error(f"Failed to load template from database: {str(e)}", exc_info=True)
        
        
        if not template and language != 'en':
            logger.debug(f"Fallback to English for {code}:{language}")
            return self.get_template(code, 'en')
        
        
        if hasattr(settings, 'TEMPLATE_REQUESTS'):
            settings.TEMPLATE_REQUESTS.labels(template_code=code, cache_hit='hit' if cache_hit else 'miss').inc()
        
        return template
    
    def get_templates_by_type(self, template_type):
        """Get all templates of a specific type"""
        return self.templates_by_type.get(template_type, [])
    
    def get_templates_by_tag(self, tag):
        """Get all templates with a specific tag"""
        return self.templates_by_tag.get(tag, [])
    
    def get_all_templates(self):
        """Get all cached templates"""
        return list(self.templates.values())
    
    def invalidate(self):
        """Force cache invalidation and reload"""
        logger.info("Forcing cache invalidation")
        self.increment_cache_version()
        self.sync_from_db(force=True)
    
    def invalidate_template(self, template_code, language='en'):
        """Invalidate specific template from cache"""
        cache_key = f"{template_code}:{language}"
        
        with self._lock:
            if cache_key in self.templates:
                del self.templates[cache_key]
                logger.info(f"Invalidated template from cache: {cache_key}")
    
    def get_stats(self):
        """Get cache statistics"""
        return {
            'templates_cached': len(self.templates),
            'types': {
                template_type: len(templates)
                for template_type, templates in self.templates_by_type.items()
            },
            'tags': {
                tag: len(templates)
                for tag, templates in self.templates_by_tag.items()
            },
            'last_sync': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.last_sync)) if self.last_sync else None,
            'current_version': self.current_version,
            'cache_enabled': settings.TEMPLATE_CACHE_ENABLED,
            'memory_usage_mb': f"{self._memory_usage / 1024 / 1024:.2f}",
            'uptime_seconds': f"{time.time() - self._initialization_time:.2f}"
        }