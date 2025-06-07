# Distributed Rate Limiting System

## Features
This system provides distributed rate limiting capabilities to control the rate of requests to a service. Key features include:
*   Scalable rate limiting across multiple instances.
*   Configurable rate limits.
*   Integration with a main webserver.

## Technologies Used
*   Python 3.9+
*   FastAPI: Modern, fast (high-performance) web framework for building APIs with Python 3.7+ based on standard Python type hints.
*   Redis: An open-source, in-memory data structure store, used as a database, cache, and message broker.
*   Docker: Platform for developing, shipping, and running applications in containers.
*   `httpx`: A fully featured HTTP client for Python 3, `async`/`await` ready.
*   `aioredis`: Asynchronous Redis client for Python.

## Setup & Installation
To set up and install the project, follow these steps:
1.  **Clone the repository**:
    ```bash
    git clone https://github.com/your-repo/distributed-rate-limiting.git
    cd distributed-rate-limiting
    ```
2.  **Build and run with Docker Compose**:
    ```bash
    docker-compose up --build --scale rate_limiting_service=N
    ```
    Replace `N` with the desired number of rate limiter instances (e.g., `docker-compose up --build --scale rate_limiting_service=3`). This will build the Docker images for both the main webserver and the rate limiting service, and then start the services along with a Redis container.

## Usage
Once the Docker containers are running, the services will be accessible:
*   **Main Webserver**: `http://localhost:8000`
*   **Rate Limiter Service**: `http://localhost:8001` (internal to Docker network, not directly accessible from host unless port mapped)

You can access the webserver endpoints (e.g., `/compute_hash`, `/health`) to observe rate limiting in action. For example, to test the hash computation:
```bash
curl -X POST http://localhost:8000/compute_hash -H "Content-Type: application/json" -d '{"data": "test_data"}'
```

### Testing Rate Limiting
To test rate limiting, you can send multiple requests to the `/compute_hash` endpoint rapidly. For example, using `curl` in a loop:
```bash
for i in $(seq 1 20); do
  curl -s -X POST http://localhost:8000/compute_hash -H "Content-Type: application/json" -d '{"data": "test_data"}' &
done
wait
```
This will send 20 concurrent requests. Observe the responses to see how rate limiting is applied.

### Registering and Deregistering Rate Limiters
The Rate Limiter Services automatically register with the Main Webserver on startup and deregister on shutdown.

To manually register a rate limiter (e.g., if you are running it outside Docker Compose or need to re-register):
```bash
curl -X POST http://localhost:8000/register_rate_limiter -H "Content-Type: application/json" -d '{"host": "rate_limiter_host", "port": 8001}'
```
Replace `rate_limiter_host` with the actual host/IP of the rate limiter service.

To manually deregister a rate limiter:
```bash
curl -X POST http://localhost:8000/deregister_rate_limiter -H "Content-Type: application/json" -d '{"host": "rate_limiter_host", "port": 8001}'
```
Ensure the host and port match the registered rate limiter.

## Configuration
Configuration parameters are managed in `config.py`. Key configurable aspects include:
*   Rate limit thresholds.
*   Redis connection details.
*   Service ports.

## Architecture
The system is designed with a microservices architecture, composed of two main services that communicate with each other:

*   **Main Webserver**:
    *   Handles all incoming client requests.
    *   Implements an in-memory cache (backed by Redis) for "expensive" hash computations to improve response times.
    *   Acts as a central registry for available Rate Limiter Services.
    *   Uses a middleware to intercept requests and delegate rate limiting checks to an available Rate Limiter Service. Currently, it randomly selects a registered rate limiter.

*   **Rate Limiter Service**:
    *   A standalone service responsible for tracking request counts and enforcing rate limits for client IPs.
    *   Utilizes Redis as a distributed store to maintain the state of the sliding window for each client.
    *   Registers itself with the Main Webserver on startup and deregisters on shutdown, ensuring the Main Webserver has an up-to-date list of available rate limiters.

This separation of concerns allows for independent scaling and deployment of each component, enhancing the system's overall scalability and resilience.