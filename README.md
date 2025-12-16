# MCP Observer Library

A simple decorator library that handles your loggings and connects to our platform to provide you telemetry and insights. Current works by logging per call to our durable storage solution and having short term session data streamed via opentelemetry using console output. We handle all forms of mcp tool function signatures through our wrapper so all you have to do is add the decorator to your existing mcp tools.

## Installation

### Using pip

```bash
pip install -r requirements.txt
```

### Using uv (recommended)

```bash
uv pip install -e .
```

### For development

```bash
# Using pip
pip install -r requirements.txt

# Using uv
uv pip install -e ".[dev]"
```

## Quick Start

```python
from mcp_observer import MCPObserver
from fastmcp import FastMCP

# Initialize your MCP server
mcp = FastMCP("MyServer")

# Initialize the observer
observer = MCPObserver(
    name="MyServer",
    version="1.0.0",
    project_id="your-project-uuid",
    api_key="your-generated-api-key"
)

# Decorate your tools
@mcp.tool()
@observer.track(track_io=True)
async def my_tool(data: dict) -> dict:
    # Your tool logic here
    return {"result": "success"}
```

## Examples

### Example 1: Simple Tool (No Context)

```python
from mcp_observer import MCPObserver
from fastmcp import FastMCP

mcp = FastMCP("MathServer")
observer = MCPObserver(
    name="MathServer",
    version="1.0.0",
    project_id="your-project-uuid",
    api_key="your-api-key"
)

@mcp.tool(name="adder", description="Add two numbers")
@observer.track(track_io=True)
async def add(a: int, b: int) -> int:
    """Add two numbers together"""
    return a + b
```

### Example 2: Tool with Session Context

```python
from mcp_observer import MCPObserver
from fastmcp import FastMCP, Context
from typing import Optional

mcp = FastMCP("EchoServer")
observer = MCPObserver(
    name="EchoServer",
    version="1.0.0",
    project_id="your-project-uuid",
    api_key="your-api-key"
)

@mcp.tool(name="echo", description="Echo the input with session tracking")
@observer.track(track_io=True)
async def echo(message: str, ctx: Optional[Context] = None) -> str:
    """Echo input string with session ID"""
    if ctx:
        return f"{message} (Session: {ctx.session_id})"
    return message
```

### Example 3: Fingerprint-Only Tracking (Default)

For tools that handle sensitive data, omit `track_io=True` to only store fingerprints:

```python
@mcp.tool(name="process_sensitive_data")
@observer.track()  # Only fingerprints stored, no full I/O
async def process_sensitive_data(user_data: dict) -> dict:
    """Process sensitive user data"""
    # Only metadata is logged, not the actual data
    return {"status": "processed"}
```

## Parameters on MCP Observer:

### Constructor
- `name`: Name of your server or application (This is what appears in logging)
- `version`: Version of your server/application
- `project_id`: Your project ID
- `api_key`: Your API key for authentication

### Decorator: `@observer.track()`

- `track_io` (bool): If True, enables full input/output tracking (requires project consent)
    - Default: False (only fingerprints are stored)

## Advanced Features

### OpenTelemetry Integration

The SDK automatically integrates with OpenTelemetry for distributed tracing and metrics:

```python
observer = MCPObserver(
    name="MyServer",
    version="1.0.0",
    project_id="your-project-uuid",
    api_key="your-api-key",
    otlp_endpoint="http://localhost:4317",  # Optional: Send to OTLP collector
    enable_console_export=True  # Optional: Enable console debugging
)
```

**Environment Variables:**

- `OTEL_EXPORTER_OTLP_ENDPOINT`: Configure OTLP endpoint for production tracing
- `OTEL_CONSOLE_EXPORT`: Set to `"true"` to enable console span/metric export

**Automatic Metrics:**

- `mcp.tool.calls`: Counter for tool invocations
- `mcp.tool.duration`: Histogram of tool execution times (ms)
- `mcp.tool.errors`: Counter for tool errors

**Automatic Spans:**

- Each tool call creates a span named `mcp.tool.{function_name}`
- Spans include: call_id, session_id, latency, status, error details

### Tracking Policy System

The SDK uses a dual-consent system for full I/O tracking:

1. **Developer declares safety**: Use `@observer.track(track_io=True)`
2. **Project admin enables**: Backend API controls per-tool policy
3. **Results are cached**: Policy responses cached for 1 hour (configurable)

This ensures sensitive data is only stored when both developer and admin consent.

### Custom Logging

Pass your own logger for integration with existing logging infrastructure:

```python
import logging

my_logger = logging.getLogger("MyApp")
my_logger.setLevel(logging.DEBUG)

observer = MCPObserver(
    name="MyServer",
    version="1.0.0",
    project_id="your-project-uuid",
    api_key="your-api-key",
    logger=my_logger
)
```

## Running the Example

```bash
# Install dependencies
uv pip install -e ".[dev]"

# Run the example server
python tests/simple_example.py
```


