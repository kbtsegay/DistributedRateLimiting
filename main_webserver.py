import hashlib
import time
import asyncio
import httpx
import aioredis
import random
from typing import Dict, Any, Deque, List

from fastapi import FastAPI, Request, HTTPException, status
from pydantic import BaseModel
from starlette.responses import JSONResponse

from config import CACHE_EXPIRATION_SECONDS, THIS_SERVER_BASE_URL, logger, REDIS_HOST, REDIS_PORT, REDIS_DB

class CacheManager:
    def __init__(self, redis_client: aioredis.Redis, expiration_seconds: int):
        self.redis = redis_client
        self.expiration_seconds = expiration_seconds
        self.cache_prefix = "cache:"

    async def get_cached_hash(self, key: str) -> str | None:
        full_key = f"{self.cache_prefix}{key}"
        cached_value = await self.redis.get(full_key)
        if cached_value:
            logger.info(f"Cache hit for key: '{key}'")
            return cached_value.decode('utf-8')
        logger.info(f"Cache miss for key: '{key}'")
        return None

    async def set_cached_hash(self, key: str, value: str):
        full_key = f"{self.cache_prefix}{key}"
        await self.redis.setex(full_key, self.expiration_seconds, value)
        logger.info(f"Stored in Redis cache: '{key}' -> '{value}' with expiration {self.expiration_seconds}s")

class RateLimiterRegistry:
    def __init__(self):
        self.registered_rate_limiters: List[str] = []
        self.lock = asyncio.Lock()

    async def register(self, url: str):
        async with self.lock:
            if url not in self.registered_rate_limiters:
                self.registered_rate_limiters.append(url)
                self.registered_rate_limiters.sort()
                logger.info(f"Registered Rate Limiter: {url}. Total: {len(self.registered_rate_limiters)}")
            else:
                logger.warning(f"Rate Limiter {url} already registered.")

    async def deregister(self, url: str):
        async with self.lock:
            try:
                self.registered_rate_limiters.remove(url)
                self.registered_rate_limiters.sort()
                logger.info(f"Deregistered Rate Limiter: {url}. Total: {len(self.registered_rate_limiters)}")
            except ValueError:
                logger.error(f"Rate Limiter {url} not found for deregistration.")
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rate Limiter not found.")

    async def get_target_rate_limiter_url(self, client_ip: str) -> str:
        async with self.lock:
            if not self.registered_rate_limiters:
                logger.error("No Rate Limiter services registered. Returning 503 Service Unavailable.")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Rate Limiter services are currently unavailable."
                )

            return random.choice(self.registered_rate_limiters)

app = FastAPI(
    title="Main Webserver with Robust Distributed Rate Limiting",
    description="Handles hash computation with caching, and delegates rate limiting to external services using consistent hashing.",
    version="1.0.0",
)

rate_limiter_registry = RateLimiterRegistry()
cache_manager: CacheManager # Declared but not initialized here

@app.on_event("startup")
async def startup_event():
    logger.info("Main Webserver starting up...")
    # Initialize Redis connection
    try:
        redis_url = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
        app.state.redis = await aioredis.from_url(redis_url, encoding="utf-8", decode_responses=False)
        await app.state.redis.ping()
        logger.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}")
    except Exception as e:
        logger.error(f"Could not connect to Redis: {e}")
        # Depending on criticality, you might want to exit or degrade gracefully
        raise

    global cache_manager
    cache_manager = CacheManager(app.state.redis, CACHE_EXPIRATION_SECONDS)

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Main Webserver shutting down...")
    if hasattr(app.state, 'redis') and app.state.redis:
        await app.state.redis.close()
        logger.info("Redis connection closed.")

class DataInput(BaseModel):
    data: str

class RateLimiterRegistration(BaseModel):
    url: str

http_client = httpx.AsyncClient(timeout=5.0)

def expensive_hash(data: str) -> str:
    time.sleep(0.05)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()

@app.post("/register")
async def register_rate_limiter(registration: RateLimiterRegistration):
    await rate_limiter_registry.register(registration.url)
    return {"status": "success", "message": "Rate Limiter registered."}

@app.post("/deregister")
async def deregister_rate_limiter(registration: RateLimiterRegistration):
    await rate_limiter_registry.deregister(registration.url)
    return {"status": "success", "message": "Rate Limiter deregistered."}

@app.middleware("http")
async def distributed_rate_limit_middleware(request: Request, call_next):
    if request.url.path in ["/register", "/deregister", "/health"]:
        response = await call_next(request)
        return response

    client_ip = request.headers.get("x-forwarded-for") or request.client.host
    
    logger.debug(f"Client IP for hashing: {client_ip}")

    try:
        rate_limiter_url = await rate_limiter_registry.get_target_rate_limiter_url(client_ip)
        logger.info(f"Calling Rate Limiter: {rate_limiter_url} (selected randomly for IP: {client_ip})")
        
        response = await http_client.post(
            f"{rate_limiter_url}/rateLimit",
            json={"client_ip": client_ip}
        )

        if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            logger.warning(f"Rate Limiter ({rate_limiter_url}) denied request for IP: {client_ip}")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content=response.json(),
                headers=response.headers
            )

        response.raise_for_status()

        logger.info(f"Rate Limiter ({rate_limiter_url}) allowed request for IP: {client_ip}")
        response = await call_next(request)
        return response

    except HTTPException as e:
        raise e
    except httpx.RequestError as exc:
        logger.error(f"Rate Limiter ({rate_limiter_url}) unreachable or network error for IP: {client_ip}: {exc}.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Assigned Rate Limiter ({rate_limiter_url}) is unreachable."
        )
    except httpx.HTTPStatusError as exc:
        logger.error(f"Rate Limiter ({rate_limiter_url}) returned unexpected error status: {exc.response.status_code}, detail: {exc.response.text}.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Assigned Rate Limiter ({rate_limiter_url}) returned an unexpected error: {exc.response.status_code}"
        )

@app.post("/compute_hash")
async def compute_hash_endpoint(input_data: DataInput):
    data_to_hash = input_data.data
    cache_key = data_to_hash

    cached_result = await cache_manager.get_cached_hash(cache_key)
    if cached_result:
        return {"data": data_to_hash, "hash": cached_result, "source": "cache"}

    logger.info(f"Cache miss for data: '{data_to_hash}', computing hash...")
    computed_hash = expensive_hash(data_to_hash)

    await cache_manager.set_cached_hash(cache_key, computed_hash)

    return {"data": data_to_hash, "hash": computed_hash, "source": "computed"}

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "Main Server is running."}