import time
import asyncio
import os
import httpx
import aioredis
from typing import Deque, Dict, List

from fastapi import FastAPI, Request, HTTPException, status
from pydantic import BaseModel
from starlette.responses import JSONResponse

from config import (
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_PERIOD_SECONDS,
    MAIN_SERVER_REGISTER_URL,
    MAIN_SERVER_DEREGISTER_URL,
    THIS_SERVICE_BASE_URL,
    logger,
    REDIS_HOST,
    REDIS_PORT,
    REDIS_DB
)

class SlidingWindowRateLimiter:
    def __init__(self, redis_client: aioredis.Redis, requests_limit: int, period_seconds: int):
        self.redis = redis_client
        self.requests_limit = requests_limit
        self.period_seconds = period_seconds
        self.key_prefix = "rate_limit:"

    async def check_and_update(self, client_ip: str) -> bool:
        key = f"{self.key_prefix}{client_ip}"
        current_time = time.time()

        # Step 1: Retrieve all timestamps from Redis
        # This part is not in a pipeline with the write operations because we need its result
        # to make a decision before proceeding with modifications.
        timestamps_bytes = await self.redis.lrange(key, 0, -1)
        timestamps = [float(ts.decode('utf-8')) for ts in timestamps_bytes] if timestamps_bytes else []

        # Step 2: Filter out old timestamps in application logic
        # This effectively "trims" old requests from our view
        valid_timestamps = [ts for ts in timestamps if (current_time - ts) < self.period_seconds]

        # Step 3: Check if the current count exceeds the limit
        if len(valid_timestamps) >= self.requests_limit:
            if valid_timestamps:
                time_to_wait = self.period_seconds - (current_time - valid_timestamps[0])
            else:
                time_to_wait = 0

            logger.warning(f"Rate limit exceeded for IP: {client_ip} by {THIS_SERVICE_BASE_URL}. Remaining: {max(0, int(time_to_wait) + 1)}s")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {max(0, int(time_to_wait) + 1)} seconds.",
                headers={"Retry-After": str(max(0, int(time_to_wait) + 1))}
            )

        # Step 4: If allowed, update the list in Redis atomically
        async with self.redis.pipeline() as pipe:
            # Delete the old list and then re-add the valid timestamps + new timestamp
            pipe.delete(key) # Remove the old list
            
            # Add all valid timestamps back
            if valid_timestamps:
                pipe.rpush(key, *valid_timestamps)
            
            # Add the new current timestamp
            pipe.rpush(key, current_time)
            
            # Set expiration for the key to prevent it from living forever if no more requests come
            pipe.expire(key, self.period_seconds)

            await pipe.execute()
            
            # Re-fetch the length after modification for accurate logging
            current_count = await self.redis.llen(key)
            logger.info(f"Request allowed for IP: {client_ip} by {THIS_SERVICE_BASE_URL}. Current count: {current_count}")
        return True

app = FastAPI(
    title="Rate Limiter Service",
    description="A dedicated service for rate limiting checks.",
    version="1.0.0",
)

rate_limiter: SlidingWindowRateLimiter # Declared but not initialized here

@app.on_event("startup")
async def startup_event():
    logger.info(f"Rate Limiter Service starting up. Attempting to register with Main Server at {MAIN_SERVER_REGISTER_URL}...")
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

    global rate_limiter
    rate_limiter = SlidingWindowRateLimiter(app.state.redis, RATE_LIMIT_REQUESTS, RATE_LIMIT_PERIOD_SECONDS)

    # Register with Main Server
    try:
        response = await http_client.post(
            MAIN_SERVER_REGISTER_URL,
            json={"url": THIS_SERVICE_BASE_URL}
        )
        response.raise_for_status()
        logger.info(f"Successfully registered with Main Server: {THIS_SERVICE_BASE_URL}")
    except httpx.RequestError as exc:
        logger.error(f"Failed to register with Main Server due to network error: {exc}")
    except httpx.HTTPStatusError as exc:
        logger.error(f"Failed to register with Main Server, status code: {exc.response.status_code}, detail: {exc.response.text}")
    except Exception as exc:
        logger.error(f"An unexpected error occurred during registration: {exc}")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info(f"Rate Limiter Service shutting down. Attempting to deregister from Main Server at {MAIN_SERVER_DEREGISTER_URL}...")
    # Deregister from Main Server
    try:
        response = await http_client.post(
            MAIN_SERVER_DEREGISTER_URL,
            json={"url": THIS_SERVICE_BASE_URL}
        )
        response.raise_for_status()
        logger.info(f"Successfully deregistered from Main Server: {THIS_SERVICE_BASE_URL}")
    except httpx.RequestError as exc:
        logger.error(f"Failed to deregister from Main Server due to network error: {exc}")
    except httpx.HTTPStatusError as exc:
        logger.error(f"Failed to deregister from Main Server, status code: {exc.response.status_code}, detail: {exc.response.text}")
    except Exception as exc:
        logger.error(f"An unexpected error occurred during deregistration: {exc}")
    finally:
        if hasattr(app.state, 'redis') and app.state.redis:
            await app.state.redis.close()
            logger.info("Redis connection closed.")
        await http_client.aclose()

class ClientIP(BaseModel):
    client_ip: str

http_client = httpx.AsyncClient()


@app.post("/rateLimit")
async def rate_limit_check(client_info: ClientIP):
    """
    Checks if a given client IP is within the rate limit.
    Returns 200 OK if allowed, 429 Too Many Requests if limited.
    """
    try:
        await rate_limiter.check_and_update(client_info.client_ip)
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "allowed"})
    except HTTPException as e:
        raise e

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "Rate Limiter Service is running."}