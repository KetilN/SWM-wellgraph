"""Run the wellgraph MCP server.

    python -m wellgraph.mcp_server                      # stdio (Claude Desktop / Code)
    python -m wellgraph.mcp_server --http --port 7860   # streamable HTTP at /mcp

The graph folder comes from --kg-dir or WELLGRAPH_KG_DIR (default: data/kg).
"""

from __future__ import annotations

import argparse

from .server import build_server


def main() -> None:
    parser = argparse.ArgumentParser(prog="wellgraph.mcp_server", description=__doc__)
    parser.add_argument("--kg-dir", help="Folder with the graph Parquet files.")
    parser.add_argument("--http", action="store_true", help="Serve streamable HTTP.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()

    server = build_server(args.kg_dir)
    if args.http:
        server.run(transport="http", host=args.host, port=args.port)
    else:
        server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
