from fastmcp import FastMCP, Context
import sys
import os
from typing import Optional

# Add the parent src directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from mcp_observer import MCPObserver
from dotenv import load_dotenv
load_dotenv()
global mcp
mcp = FastMCP("TrackerServer")
import logging
#logger that writes to a logging.txt
logging.basicConfig(filename='logging.txt', level=logging.INFO)


# Create a single observer instance for this server
observer = MCPObserver(
    name="TrackerServerObserver",
    project_id=os.getenv("MCP_PROJECT_ID", ""),
    api_key=os.getenv("MCP_OBSERVER_API_KEY", ""),
    logger=logging.getLogger("TrackerServerObserver")
)

@mcp.tool(name="adder", description="Add two numbers")
@observer.track(track_io=True)
async def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

@mcp.tool(name="echo", description="Echo the input string")
@observer.track(track_io=True)
async def echo(input_string: str, ctx: Optional[Context] = None) -> str:
    """Echo the input string"""
    res = input_string
    # Print all context information
    return res + str(ctx.session_id) if ctx else res

if __name__ == "__main__":
    mcp.run()
