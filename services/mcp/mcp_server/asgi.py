from mcp_server.server import build_mcp
from mcp_server.telemetry import MCPTelemetrySettings, build_mcp_tracing

tracing_runtime = build_mcp_tracing(MCPTelemetrySettings.from_environment())
mcp = build_mcp(tracer=tracing_runtime.tracer)
app = mcp.http_app(path="/mcp")
