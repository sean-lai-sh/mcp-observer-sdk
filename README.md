# MCP Observer SDK

[![PyPI version](https://badge.fury.io/py/mcp-observer.svg)](https://badge.fury.io/py/mcp-observer)
[![Python Versions](https://img.shields.io/pypi/pyversions/mcp-observer.svg)](https://pypi.org/project/mcp-observer/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A lightweight, decorator-based observability SDK for [Model Context Protocol (MCP)](https://modelcontextprotocol.io) tools. Add comprehensive telemetry and insights to your MCP servers with a single line of code.

## Features

- **Zero-friction Integration**: Add observability with a simple decorator
- **OpenTelemetry Support**: Built-in tracing, metrics, and distributed context propagation
- **Privacy-First**: Configurable I/O tracking with dual-consent system
- **Universal Compatibility**: Works with all MCP tool function signatures
- **Dual Storage**: Durable storage for analytics + real-time streaming via OpenTelemetry
- **Session Tracking**: Automatic session and request correlation

## Installation

### From PyPI

```bash
pip install mcp-observer
```

### Using uv (recommended)

```bash
uv pip install mcp-observer
```

### For development

```bash
# Clone the repository
git clone https://github.com/yourusername/mcp-observer-sdk.git
cd mcp-observer-sdk

# Install in development mode
pip install -e ".[dev]"
```

## Quick Start

```python
from mcp_observer import MCPObserver
from fastmcp import FastMCP

# Initialize your MCP server
mcp = FastMCP("MyServer")

# Initialize the observer (project is automatically determined from your API key)
observer = MCPObserver(
    name="MyServer",
    version="1.0.0",
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
- `api_key`: Your API key for authentication (project is automatically determined from this key)

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

## API Reference

### `MCPObserver(name, version, api_key, **kwargs)`

Initialize the observer for your MCP server.

**Parameters:**

- `name` (str): Your server/application name
- `version` (str): Your server/application version
- `api_key` (str): Authentication key (project is auto-determined)
- `otlp_endpoint` (str, optional): OTLP collector endpoint
- `enable_console_export` (bool, optional): Enable console span/metric output
- `logger` (logging.Logger, optional): Custom logger instance

### `@observer.track(track_io=False)`

Decorator to add observability to MCP tools.

**Parameters:**

- `track_io` (bool, optional): Enable full I/O tracking (requires project consent). Default: False

**Returns:**

- Decorated async function with telemetry

## Development

### Running Tests

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=mcp_observer --cov-report=html
```

### Code Quality

```bash
# Format code
black src tests

# Type checking
mypy src

# Linting
ruff check src tests
```

## Project Structure

```text
mcp-observer-sdk/
├── src/
│   └── mcp_observer/
│       ├── __init__.py          # Package exports
│       ├── observer.py          # Main MCPObserver class
│       └── wrapper.py           # Decorator and telemetry logic
├── tests/
│   └── simple_example.py        # Example MCP server
├── pyproject.toml               # Package configuration
├── README.md                    # This file
└── LICENSE                      # MIT License
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/yourusername/mcp-observer-sdk/issues)
- **Documentation**: [Full Documentation](https://docs.yourdomain.com)
- **Email**: <support@yourdomain.com>

## Acknowledgments

- Built for the [Model Context Protocol](https://modelcontextprotocol.io)
- Powered by [OpenTelemetry](https://opentelemetry.io)
- Inspired by the need for better MCP tool observability


