
import redis
import os
import logging
import time  
from threading import Lock
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from django.conf import settings

logger = logging.getLogger(__name__)

if settings.DEBUG == True:

        # Use mock Redis for development
    class MockRedis:
        def __init__(self):
            self.data = {}
        
        def get(self, key):
            return self.data.get(key)
        
        def setex(self, key, expiry, value):
            self.data[key] = value
        
        def incr(self, key):
            self.data[key] = self.data.get(key, 0) + 1
            return self.data[key]
        
        def expire(self, key, seconds):
            pass
    
    def redis_client():
        return MockRedis()
else:
    

    

    class RedisConnectionError(Exception):
        """Custom exception for Redis connection failures"""
        pass


    class RedisClient:
        """
        Thread-safe, production-ready Redis client with connection pooling,
        retry logic, and circuit breaker pattern
        """
        
        _instance = None
        _lock = Lock()
        
        def __new__(cls):
            if cls._instance is None:
                with cls._lock:
                    if cls._instance is None:
                        cls._instance = super(RedisClient, cls).__new__(cls)
            return cls._instance
        
        def __init__(self):
            
            if hasattr(self, '_initialized') and self._initialized:
                return

            
            self._is_connected = False 
            self._connection_errors = 0
            self._last_error_time = None
            
            
            

            self._initialize()
            self._initialized = True 
            
        
        def _initialize(self):
            """Initialize Redis connection with production settings"""
            
            self._initializing = True
            try:
                
                redis_url = os.getenv('REDIS_URL', 'redis://redis:6379/2')
                self._redis_url = redis_url
                
                
                pool_kwargs = {
                    'max_connections': int(os.getenv('REDIS_MAX_CONNECTIONS', 50)),
                    'health_check_interval': 30,
                    'socket_timeout': float(os.getenv('REDIS_SOCKET_TIMEOUT', 5)),
                    'socket_connect_timeout': float(os.getenv('REDIS_SOCKET_CONNECT_TIMEOUT', 5)),
                    'socket_keepalive': True,
                    'retry_on_timeout': True,
                    'decode_responses': True,
                    'retry': redis.retry.Retry(redis.backoff.ExponentialBackoff(cap=10), 3)
                }
                
                
                self._pool = redis.ConnectionPool.from_url(
                    redis_url,
                    **pool_kwargs
                )
                
                
                
                logger.debug("Testing Redis connection pool directly during initialization...")
                try:
                    
                    temp_conn = redis.Redis(connection_pool=self._pool)
                    
                    temp_conn.ping()
                    
                    temp_conn.close()
                    logger.debug("Direct Redis connection pool test successful.")
                except (redis.ConnectionError, redis.TimeoutError, redis.ResponseError) as pool_test_error:
                    logger.error(f"Direct pool test failed: {pool_test_error}")
                    
                    raise pool_test_error
                
                
                self._is_connected = True 
                
                
                logger.info("Redis client initialized successfully")
                
            except Exception as e:
                logger.error(f"Failed to initialize Redis client: {str(e)}", exc_info=True)
                
                
                self._is_connected = False
                
                raise RedisConnectionError(f"Redis initialization failed: {str(e)}") from e
            finally:
                
                if hasattr(self, '_initializing'):
                    delattr(self, '_initializing')

        
        
        def _test_connection(self):
            """Test Redis connection using get_connection."""
            
            
            
            logger.debug("Testing Redis connection via get_connection...")
            try:
                
                with self.get_connection() as conn:
                    conn.ping()
                    logger.debug("Connection test via get_connection successful.")
            except Exception as e:
                logger.error(f"Connection test via get_connection failed: {str(e)}")
                raise RedisConnectionError(f"Redis connection test failed: {str(e)}") from e

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception_type((redis.ConnectionError, redis.TimeoutError))
        )
        def get_connection(self):
            """Get a Redis connection from the pool with retry logic"""
            try:
                
                
                
                
                
                if getattr(self, '_initializing', False):
                    logger.debug("RedisClient.get_connection called while _initialize is running, waiting...")
                    
                    
                    
                    raise RedisConnectionError("Redis client is initializing, connection not yet available.")

                
                if not getattr(self, '_is_connected', False):
                    logger.warning("Redis client marked as disconnected or attribute missing, attempting re-initialization...")
                    
                    if not getattr(self, '_initializing', False):
                        self._initialize() 
                    else:
                        logger.debug("RedisClient.get_connection: Re-initialization already in progress, waiting...")

                
                if not hasattr(self, '_pool'):
                    raise RedisConnectionError("Redis pool not initialized")

                conn = redis.Redis(connection_pool=self._pool)
                
                
                try:
                    conn.ping()
                except (redis.ConnectionError, redis.TimeoutError):
                    
                    self._is_connected = False
                    conn.close() 
                    raise 

                return conn
            except (redis.ConnectionError, redis.TimeoutError) as e:
                self._handle_connection_error(e)
                raise
            except Exception as e:
                logger.error(f"Unexpected Redis error in get_connection: {str(e)}", exc_info=True)
                raise

        def _handle_connection_error(self, error):
            """Handle connection errors with circuit breaker pattern"""
            self._connection_errors += 1
            self._last_error_time = time.time() 
            
            
            if self._connection_errors >= 5:
                self._is_connected = False
                logger.error(f"Circuit breaker triggered for Redis after {self._connection_errors} errors")
            
            logger.warning(f"Redis connection error (count: {self._connection_errors}): {str(error)}")

        def close(self):
            """Close all connections in the pool"""
            if hasattr(self, '_pool'):
                self._pool.disconnect()
                logger.info("Redis connections closed")

        def __del__(self):
            """Ensure connections are closed on object destruction"""
            self.close()

        
        def get(self, name):
            with self.get_connection() as conn:
                return conn.get(name)

        def set(self, name, value, ex=None, px=None, nx=False, xx=False):
            with self.get_connection() as conn:
                return conn.set(name, value, ex=ex, px=px, nx=nx, xx=xx)

        def setex(self, name, time, value):
            with self.get_connection() as conn:
                return conn.setex(name, time, value)

        def incr(self, name, amount=1):
            with self.get_connection() as conn:
                return conn.incr(name, amount)

        def expire(self, name, time):
            with self.get_connection() as conn:
                return conn.expire(name, time)

        def delete(self, *names):
            with self.get_connection() as conn:
                return conn.delete(*names)

        def keys(self, pattern='*'):
            with self.get_connection() as conn:
                return conn.keys(pattern)

        def pipeline(self):
            """Create a Redis pipeline"""
            
            
            
            
            
            raw_conn = self.get_connection()
            pipe = raw_conn.pipeline()
            
            
            return pipe 

        def health_check(self):
            """Check Redis health status"""
            try:
                
                with self.get_connection() as conn:
                    conn.ping()
                    info = conn.info('memory')
                    return {
                        'status': 'healthy',
                        'used_memory_human': info.get('used_memory_human', 'unknown'),
                        'connected_clients': info.get('connected_clients', 'unknown'),
                        'uptime_in_days': info.get('uptime_in_days', 'unknown')
                    }
            except Exception as e:
                logger.error(f"Redis health check failed: {str(e)}", exc_info=True)
                return {
                    'status': 'unhealthy',
                    'error': str(e)
                }



    redis_client = RedisClient()