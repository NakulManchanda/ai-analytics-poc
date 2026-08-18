from fastmcp import FastMCP


mcp = FastMCP("analytics-mcp")


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001)
