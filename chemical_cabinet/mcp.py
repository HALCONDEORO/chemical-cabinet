"""Model Context Protocol server over stdio, so an AI assistant can read the cabinet.

Local only: it reads the same SQLite file as the web app, opens no network port and
needs no packages. It is read-only unless started with --allow-write. It reports what
is recorded and which review flags apply. It gives no advice on mixing or storing
chemicals."""
import argparse
import json
import os
import sys

from . import __version__, db

PROTOCOL = "2025-06-18"
MAX_TEXT = 500
MAX_ITEMS = 500

READ_TOOLS = {
    "list_items": {
        "description": "List active products with location, quantity, dates and review flags.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "review": {
        "description": "List only active products that have at least one review flag "
                       "(expired, expires within 30 days, no expiry date, no safety data sheet, no photo).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "disposal_log": {
        "description": "List retired products with date, reason and disposal method, newest first.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}
TEXT = {"type": "string", "maxLength": MAX_TEXT}
DATE = {"type": "string", "description": "YYYY-MM-DD", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
WRITE_TOOLS = {
    "add_item": {
        "description": "Add a product record. Photo and safety data sheet files are added in the web app.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": TEXT, "location": TEXT, "quantity": TEXT, "notes": TEXT,
                           "opened_on": DATE, "expires_on": DATE},
            "required": ["name"], "additionalProperties": False},
    },
    "retire_item": {
        "description": "Mark a product as retired and log the reason and disposal method.",
        "inputSchema": {
            "type": "object",
            "properties": {"item_id": {"type": "integer", "minimum": 1}, "reason": TEXT, "method": TEXT},
            "required": ["item_id"], "additionalProperties": False},
    },
}


class ToolError(Exception):
    pass


def text_arg(args, key, required=False):
    value = args.get(key, "")
    if not isinstance(value, str):
        raise ToolError("%s must be a string" % key)
    if len(value) > MAX_TEXT:
        raise ToolError("%s is longer than %d characters" % (key, MAX_TEXT))
    if required and not value.strip():
        raise ToolError("%s is required" % key)
    return value


def item_dict(row):
    return {"id": row["id"], "name": row["name"], "location": row["location"],
            "quantity": row["quantity"], "notes": row["notes"], "opened_on": row["opened_on"],
            "expires_on": row["expires_on"], "has_photo": bool(row["photo"]),
            "has_safety_data_sheet": bool(row["sds"]), "flags": db.flags(row)}


def call_tool(con, name, args, allow_write):
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    if not isinstance(name, str):
        raise ToolError("tool name must be a string")
    tools = dict(READ_TOOLS)
    if allow_write:
        tools.update(WRITE_TOOLS)
    if name not in tools:
        raise ToolError("unknown tool (write tools need --allow-write)" if name in WRITE_TOOLS
                        else "unknown tool")
    unknown = set(args) - set(tools[name]["inputSchema"]["properties"])
    if unknown:
        raise ToolError("unexpected arguments: " + ", ".join(sorted(unknown)))
    if name == "list_items":
        return [item_dict(r) for r in db.active_items(con)[:MAX_ITEMS]]
    if name == "review":
        return [d for d in (item_dict(r) for r in db.active_items(con)) if d["flags"]][:MAX_ITEMS]
    if name == "disposal_log":
        return [{"item_id": r["item_id"], "name": r["name"], "retired_on": r["retired_on"],
                 "reason": r["reason"], "method": r["method"]} for r in db.retirement_log(con)[:MAX_ITEMS]]
    if name == "add_item":
        try:
            new_id = db.add_item(con, text_arg(args, "name", True), text_arg(args, "location"),
                                 text_arg(args, "quantity"), text_arg(args, "notes"),
                                 text_arg(args, "opened_on") or None, text_arg(args, "expires_on") or None)
        except ValueError as exc:
            raise ToolError(str(exc))
        return {"id": new_id}
    if name == "retire_item":
        item_id = args.get("item_id")
        if not isinstance(item_id, int) or isinstance(item_id, bool) or item_id < 1:
            raise ToolError("item_id must be a positive integer")
        try:
            db.retire_item(con, item_id, text_arg(args, "reason"), text_arg(args, "method"))
        except KeyError:
            raise ToolError("no such item")
        except ValueError as exc:
            raise ToolError(str(exc))
        return {"retired": item_id}


def handle(msg, db_path, allow_write):
    """Return a response dict for a request, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    mid, method = msg.get("id"), msg["method"]
    if "id" not in msg:
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "chemical-cabinet", "version": __version__},
                  "instructions": "Read-only inventory records. Does not advise on mixing or storing chemicals."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        tools = dict(READ_TOOLS)
        if allow_write:
            tools.update(WRITE_TOOLS)
        result = {"tools": [dict(name=n, **t) for n, t in tools.items()]}
    elif method == "tools/call":
        params = msg.get("params", {})
        if (not isinstance(params, dict) or not isinstance(params.get("name"), str)
                or not isinstance(params.get("arguments", {}), dict)):
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": "expected tool name and object arguments"}}
        con = db.connect(db_path)
        try:
            data = call_tool(con, params["name"], params.get("arguments", {}), allow_write)
            result = {"content": [{"type": "text", "text": json.dumps(data, indent=2)}], "isError": False}
        except ToolError as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        finally:
            con.close()
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(db_path, allow_write, stdin=sys.stdin, stdout=sys.stdout):
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(msg, db_path, allow_write)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(description="chemical-cabinet MCP server (stdio)")
    ap.add_argument("--data", default="data", help="the same folder the web app uses")
    ap.add_argument("--allow-write", action="store_true",
                    help="also offer add_item and retire_item (default: read-only)")
    args = ap.parse_args(argv)
    path = os.path.join(args.data, "cabinet.sqlite3")
    if not os.path.isfile(path):
        sys.exit("no database at %s: run the web app once first" % path)
    serve(path, args.allow_write)


if __name__ == "__main__":
    main()
