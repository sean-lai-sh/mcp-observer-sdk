"""
MCP Observer - Core observability functionality for MCP tools.
Built on OpenTelemetry for distributed tracing and metrics.
"""

import functools
import json
import asyncio
import logging
import os
import inspect
import uuid
import httpx
from typing import Any, Callable, Dict, Optional, List
from fastmcp import Context
from datetime import datetime

# OpenTelemetry imports
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.trace import Status, StatusCode
from opentelemetry.semconv.trace import SpanAttributes

from .wrapper import (
    create_async_wrapper,
    create_sync_wrapper,
    create_async_noauth_wrapper,
    create_sync_noauth_wrapper
)

class MCPObserver:
    """
    A class to observe and track MCP tool calls with detailed logging.
    Built on OpenTelemetry for distributed tracing and metrics.
    """

    def __init__(
        self,
        name: str = "MCPObserver",
        version: str = "1.0.0",
        api_key: str = None,
        project_id: str = None,  # DEPRECATED: Will be derived from API key
        logger: Optional[logging.Logger] = None,
        otlp_endpoint: str = None,
        enable_console_export: bool = False
    ):
        self.name = name
        self.version = version

        # Instantiate a logger
        if logger is None:
            self.logger = logging.getLogger(self.name + " Logger")
            self.logger.setLevel(logging.INFO)
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
            self.logger.addHandler(handler)
        else:
            self.logger = logger

        # Warn if project_id is provided (deprecated)
        if project_id or os.getenv("MCP_PROJECT_ID"):
            self.logger.warning(
                "DEPRECATION WARNING: project_id parameter is deprecated and will be ignored. "
                "The project is now automatically determined from your API key."
            )

        # API endpoints configuration (must be set before authentication)
        self.trace_api_url = os.getenv("TRACE_API_URL", "http://127.0.0.1:8000")
        self.tracking_policy_url = os.getenv("TRACKING_POLICY_URL", "http://127.0.0.1:8000")

        # API Key for authentication
        if api_key:
            self.api_key = api_key
        else:
            self.api_key = os.getenv("MCP_OBSERVER_API_KEY", None)

        # Authenticate API key and get project_id
        if not self.api_key:
            raise ValueError("Missing MCP Observer API key.")

        auth_result = self._authenticate_api_key()
        if not auth_result:
            raise ValueError("Invalid or missing MCP Observer API key.")

        # project_id is now derived from API key
        self.project_id = auth_result.get("project_id")
        if not self.project_id:
            raise ValueError("Failed to retrieve project_id from API key.")

        # Cache for tracking policy to avoid repeated API calls
        self._policy_cache = {}  # tool_name -> (can_store_full, expires_at)

        # Initialize OpenTelemetry
        self._init_opentelemetry(otlp_endpoint, enable_console_export)


    def _init_opentelemetry(self, otlp_endpoint: Optional[str] = None, enable_console: bool = False):
        """Initialize OpenTelemetry tracing and metrics."""
        try:
            # Create resource with service information
            resource = Resource(attributes={
                SERVICE_NAME: self.name,
                SERVICE_VERSION: self.version,
                "mcp.project_id": self.project_id,
            })
            
            # Initialize Tracer Provider
            tracer_provider = TracerProvider(resource=resource)
            
            # Add exporters
            if otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
                # OTLP exporter for production (e.g., to Jaeger, Tempo, etc.)
                endpoint = otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
                otlp_exporter = OTLPSpanExporter(endpoint=endpoint)
                tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
                self.logger.info(f"OpenTelemetry OTLP exporter configured: {endpoint}")
            
            if enable_console or os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() == "true":
                # Console exporter for development/debugging
                console_exporter = ConsoleSpanExporter()
                tracer_provider.add_span_processor(BatchSpanProcessor(console_exporter))
                self.logger.info("OpenTelemetry console exporter enabled")
            
            # Set global tracer provider
            trace.set_tracer_provider(tracer_provider)
            self.tracer = trace.get_tracer(__name__, self.version)
            
            # Initialize Metrics Provider
            metric_reader = None
            if otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
                endpoint = otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
                metric_exporter = OTLPMetricExporter(endpoint=endpoint)
                metric_reader = PeriodicExportingMetricReader(metric_exporter)
            elif enable_console:
                metric_exporter = ConsoleMetricExporter()
                metric_reader = PeriodicExportingMetricReader(metric_exporter)
            
            if metric_reader:
                meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
                metrics.set_meter_provider(meter_provider)
                self.meter = metrics.get_meter(__name__, self.version)
                
                # Create metrics
                self.call_counter = self.meter.create_counter(
                    name="mcp.tool.calls",
                    description="Number of tool calls",
                    unit="1"
                )
                self.call_duration = self.meter.create_histogram(
                    name="mcp.tool.duration",
                    description="Duration of tool calls",
                    unit="ms"
                )
                self.error_counter = self.meter.create_counter(
                    name="mcp.tool.errors",
                    description="Number of tool errors",
                    unit="1"
                )
                self.logger.info("OpenTelemetry metrics configured")
            else:
                self.meter = None
                self.call_counter = None
                self.call_duration = None
                self.error_counter = None
                
        except Exception as e:
            self.logger.warning(f"Failed to initialize OpenTelemetry: {e}")
            self.logger.warning("Continuing without OpenTelemetry instrumentation")
            self.tracer = None
            self.meter = None
            self.call_counter = None
            self.call_duration = None
            self.error_counter = None
    

    def _authenticate_api_key(self) -> Optional[Dict[str, Any]]:
        """
        Authenticate API key via API endpoint.

        Returns:
            Dict with 'valid', 'api_key_id', and 'project_id' if successful, None otherwise.
        """
        try:
            # Use the base API URL to construct the verify endpoint
            verify_url = f"{self.trace_api_url}/verify"

            with httpx.Client(timeout=5.0) as client:
                response = client.get(
                    verify_url,
                    headers={"Authorization": f"Bearer {self.api_key}"}
                )

                if response.status_code == 200:
                    data = response.json()
                    self.logger.info("API key authentication successful")
                    return data
                elif response.status_code == 401:
                    self.logger.error("API key authentication failed: Unauthorized (401)")
                    return None
                elif response.status_code == 403:
                    self.logger.error("API key authentication failed: Forbidden (403)")
                    return None
                else:
                    self.logger.error(f"API key authentication failed: Unexpected status ({response.status_code})")
                    return None

        except httpx.TimeoutException:
            self.logger.error("API key authentication timeout - server not reachable")
            return None
        except httpx.RequestError as e:
            self.logger.error(f"API key authentication request error: {e}")
            return None
        except Exception as e:
            self.logger.error(f"API key authentication error: {e}")
            return None

    def _check_tracking_policy(
        self, 
        tool_name: str, 
        full_tracking_allowed: bool
    ) -> bool:
        """
        Check if full I/O tracking is allowed for this tool via API.
        Results are cached for the duration specified by the backend (default 1 hour).
        
        Args:
            tool_name: Name of the tool to check
            full_tracking_allowed: Whether the developer declares this tool safe for full tracking
            
        Returns:
            bool: True if full I/O can be stored, False otherwise
        """
        # Check cache first
        if tool_name in self._policy_cache:
            can_store, expires_at = self._policy_cache[tool_name]
            if datetime.now().timestamp() < expires_at:
                self.logger.debug(f"Using cached tracking policy for {tool_name}: {can_store}")
                return can_store
        
        try:
            # Build URL with query params (project_id now derived from API key on backend)
            params = {
                "server_name": self.name,
                "server_version": self.version,
                "full_tracking_allowed": str(full_tracking_allowed).lower()
            }

            url = f"{self.tracking_policy_url}/tracking-policy/{tool_name}"

            with httpx.Client(timeout=3.0) as client:
                response = client.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {self.api_key}"}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    can_store = data.get("can_store_full", False)
                    cache_ttl = data.get("cache_ttl", 3600)
                    
                    # Cache result
                    expires_at = datetime.now().timestamp() + cache_ttl
                    self._policy_cache[tool_name] = (can_store, expires_at)
                    
                    self.logger.info(f"Tracking policy for {tool_name}: can_store_full={can_store}")
                    return can_store
                else:
                    self.logger.warning(
                        f"Failed to get tracking policy for {tool_name}: "
                        f"status={response.status_code}, response={response.text}"
                    )
                    return False
                    
        except httpx.TimeoutException:
            self.logger.error(f"Timeout checking tracking policy for {tool_name}, defaulting to False")
            return False
        except httpx.RequestError as e:
            self.logger.error(f"Request error checking tracking policy for {tool_name}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error checking tracking policy for {tool_name}: {e}")
            return False
    



    async def record_call(self, call_id: str, tool_name: str, input_data: Dict[str, Any], 
                   output_data: Any = None, context_data: Dict[str, Any] = None, 
                   started_at: datetime = None, completed_at: datetime = None,
                   error: Optional[Exception] = None, latency_ms: int = None,
                   session_id: str = None, client_name: str = None, model_name: str = None,
                   can_store_full: bool = False, json_in: Dict[str, Any] = None, 
                   json_out: Any = None, full_tracking_allowed: bool = False):
        """Record a function call by sending trace data to the API endpoint asynchronously."""
        
        try:
            # Prepare trace payload (project_id now derived from API key on backend)
            trace_payload = {
                "call_id": call_id,
                "server_name": self.name,
                "server_version": self.version,
                "tool_name": tool_name,
                "input_data": input_data,
                "output_data": output_data,
                "context_data": context_data,
                "started_at": started_at.isoformat() if started_at else None,
                "completed_at": completed_at.isoformat() if completed_at else None,
                "error": str(error) if error else None,
                "latency_ms": latency_ms,
                "session_id": session_id,
                "client_name": client_name,
                "model_name": model_name,
                "can_store_full": can_store_full,
                "json_in": json_in,
                "json_out": json_out,
                "full_tracking_allowed": full_tracking_allowed
            }
            
            # Log the trace payload
            self.logger.debug(f"Sending trace payload: {json.dumps(trace_payload, indent=2, default=str)}")
            
            # Send trace data to API endpoint asynchronously
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self.trace_api_url + "/trace",
                    json=trace_payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    }
                )
                
                # Check response status
                if response.status_code == 200 or response.status_code == 201:
                    self.logger.info(f"Successfully recorded trace for call {call_id} (status: {response.status_code})")
                    if error:
                        self.logger.error(f"Function {tool_name} failed with error: {error}")
                    else:
                        self.logger.info(f"Function {tool_name} executed successfully.")
                    return response.json()
                elif response.status_code == 401:
                    self.logger.error(f"Failed to record trace: Unauthorized (401) - Check API key")
                    return None
                elif response.status_code == 403:
                    self.logger.error(f"Failed to record trace: Forbidden (403) - Insufficient permissions")
                    return None
                elif response.status_code == 422:
                    self.logger.error(f"Failed to record trace: Validation error (422) - {response.text}")
                    return None
                elif response.status_code >= 500:
                    self.logger.error(f"Failed to record trace: Server error ({response.status_code}) - {response.text}")
                    return None
                else:
                    self.logger.error(f"Failed to record trace: Unexpected status ({response.status_code}) - {response.text}")
                    return None
                    
        except httpx.TimeoutException:
            self.logger.error(f"Timeout while recording trace for call {call_id}")
            return None
        except httpx.RequestError as e:
            self.logger.error(f"Request error while recording trace for call {call_id}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error while recording trace for call {call_id}: {e}")
            self.logger.debug(f"Trace payload (failed to send): {json.dumps(trace_payload, indent=2, default=str)}")
            return None
    

    def track(self, func: Callable = None, *, track_io: bool = False) -> Callable:
        """
        Decorator to track inputs and outputs of MCP tool functions.
        
        This decorator logs all function calls with their arguments, return values,
        and execution time. It handles both:
        1. Functions with Context parameter (ctx: Context)
        2. Functions without Context parameter
        
        Args:
            func: The function to decorate (passed automatically when used as @observer.track)
            track_io: If True, declares that this tool may store full JSON I/O when both:
                     - Developer marks tool with track_io=True (declaring no PII)
                     - Project admin enables full_tracking_enabled for this tool
        
        Usage:
            observer = MCPObserver()
            
            # Basic tracking (fingerprints only)
            @observer.track
            async def my_function(a: int, b: int, ctx: Context = None) -> int:
                return a + b
            
            # Full tracking enabled (requires project consent)
            @observer.track(track_io=True)
            async def compliant_function(data: dict, ctx: Context = None) -> dict:
                return {"result": data}
        """
        def decorator(f: Callable) -> Callable:
            # Inspect the function signature to understand its parameters
            sig = inspect.signature(f)
            has_context = any(
                param.annotation == Context or param.name == 'ctx' or param.name == 'context'
                for param in sig.parameters.values()
            )
            
            # Return the appropriate wrapper based on whether the function is async
            if asyncio.iscoroutinefunction(f):
                return create_async_wrapper(self, f, sig, has_context, track_io)
            else:
                return create_sync_wrapper(self, f, sig, has_context, track_io)
        
        # Support both @observer.track and @observer.track(track_io=True)
        if func is None:
            return decorator
        else:
            return decorator(func)
    
    def track_noauth(self, func: Callable) -> Callable:
        """
        Decorator to track functions that don't require authentication/context.
        
        This is a simpler version of track() that doesn't look for Context parameters
        and is optimized for public endpoints.
        
        Usage:
            observer = MCPObserver()
            
            @observer.track_noauth
            async def health_check() -> str:
                return "OK"
        """
        sig = inspect.signature(func)
        
        if asyncio.iscoroutinefunction(func):
            return create_async_noauth_wrapper(self, func, sig)
        else:
            return create_sync_noauth_wrapper(self, func, sig)


# Backwards compatibility: keep the old track_inputs function
def track_inputs(observer: 'MCPObserver'):
    """
    Backwards compatible decorator factory.
    
    Usage:
        observer = MCPObserver()
        
        @track_inputs(observer)
        async def my_function(a: int, b: int) -> int:
            return a + b
    
    New usage (preferred):
        @observer.track
        async def my_function(a: int, b: int) -> int:
            return a + b
    """
    def decorator(func: Callable) -> Callable:
        return observer.track(func)
    return decorator
