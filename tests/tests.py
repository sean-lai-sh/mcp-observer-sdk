
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from mcp_observer.observer import MCPObserver
from dotenv import load_dotenv
load_dotenv()

mcp_observer = MCPObserver(
    name="TestObserver",
    version="1.0.0",
    api_key=os.getenv("MCP_OBSERVER_API_KEY", "")
)


if __name__ == "__main__":
    print(mcp_observer._authenticate_api_key())
    print(mcp_observer._check_tracking_policy(tool_name="echo", full_tracking_allowed=True))
    print("Running MCPObserver tests...")