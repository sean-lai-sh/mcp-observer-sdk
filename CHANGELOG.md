# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial release preparation
- PyPI packaging configuration

## [0.1.0] - 2024-12-17

### Added
- Initial release of MCP Observer SDK
- `MCPObserver` class for initializing observability
- `@observer.track()` decorator for adding telemetry to MCP tools
- OpenTelemetry integration with automatic tracing and metrics
- Session and request tracking
- Privacy-first dual-consent system for I/O tracking
- Support for all MCP tool function signatures
- Custom logger integration
- Environment variable configuration support
- Comprehensive examples and documentation

### Features
- **Zero-friction Integration**: Simple decorator pattern
- **OpenTelemetry Support**: Built-in spans, metrics, and distributed tracing
- **Privacy Controls**: Configurable fingerprint-only vs full I/O tracking
- **FastMCP Compatible**: Seamless integration with FastMCP servers
- **Durable Storage**: Backend API for long-term analytics
- **Real-time Streaming**: OTLP export for live monitoring

[unreleased]: https://github.com/yourusername/mcp-observer-sdk/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/yourusername/mcp-observer-sdk/releases/tag/v0.1.0
