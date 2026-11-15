"""Model Context Protocol server over stdio, so an AI assistant can read the cabinet.

Local only: it reads the same SQLite file as the web app, opens no network port and
needs no packages. It is read-only unless started with --allow-write. It reports what
is recorded and which review flags apply. It gives no advice on mixing or storing
chemicals."""
import argparse
import json
from itertools import islice
import os
from pathlib import Path
import sqlite3
import sys

from . import __version__, db

PROTOCOL = "2025-06-18"
MAX_TEXT = 500
MAX_ITEMS = 500
MAX_ITEM_ID = 2**63 - 1

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
# Page data inside read tools, not the tools/list discovery cursor. The first
# content block remains the JSON array used by existing clients.
for _tool in READ_TOOLS.values():
    _tool["inputSchema"]["properties"].update({
        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ITEMS},
        "offset": {"type": "integer", "minimum": 0, "maximum": 2**63 - 1}})
    _tool["description"] += (" Optional limit/offset page records (default 500/0). "
                             "A second text block warns when more remain. "
                             "Pages are not a snapshot across concurrent edits.")

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
            "properties": {"item_id": {"type": "integer", "minimum": 1, "maximum": MAX_ITEM_ID}, "reason": TEXT, "method": TEXT},
            "required": ["item_id"], "additionalProperties": False},
    },
}


class ToolError(Exception):
    pass


def require_complete_restore(db_path):
    """Refuse a marked cabinet before opening SQLite, including linked DB targets.

    This is a restore sentinel check, not a lock against a concurrent restore.
    Stop web and MCP processes before restoring or replacing a data directory.
    """
    path = Path(db_path)
    for parent in {path.parent, path.resolve().parent}:
        if os.path.lexists(parent / "RESTORE_INCOMPLETE"):
            raise ToolError("data directory has a RESTORE_INCOMPLETE marker: an earlier restore "
                            "did not finish; restore again into a new directory")


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


def read_page(con, name, args, page_info):
    limit, offset = args.get("limit", MAX_ITEMS), args.get("offset", 0)
    if type(limit) is not int or not 1 <= limit <= MAX_ITEMS:
        raise ToolError("limit must be an integer from 1 to %d" % MAX_ITEMS)
    if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
        raise ToolError("offset must be an integer from 0 to 9223372036854775807")
    if name == "disposal_log":
        rows = con.execute(
            "SELECT r.*, i.name FROM retirements r JOIN items i ON i.id=r.item_id "
            "ORDER BY r.id DESC LIMIT ? OFFSET ?", (limit + 1, offset))
        data = [{"item_id": r["item_id"], "name": r["name"], "retired_on": r["retired_on"],
                 "reason": r["reason"], "method": r["method"]} for r in rows]
    elif name == "list_items":
        rows = con.execute("SELECT * FROM items WHERE retired_on IS NULL "
                           "ORDER BY name, id LIMIT ? OFFSET ?", (limit + 1, offset))
        data = [item_dict(r) for r in rows]
    else:
        rows = con.execute("SELECT * FROM items WHERE retired_on IS NULL ORDER BY name, id")
        flagged = (item_dict(r) for r in rows)
        matches = (r for r in flagged if r["flags"])
        data = list(islice(islice(matches, offset, None), limit + 1))
        rows.close()
    if page_info is not None:
        page_info.update(has_more=len(data) > limit, next_offset=offset + limit, limit=limit)
    return data[:limit]


def call_tool(con, name, args, allow_write, page_info=None):
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
    if name in READ_TOOLS:
        return read_page(con, name, args, page_info)
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
        if type(item_id) is not int or not 1 <= item_id <= MAX_ITEM_ID:
            raise ToolError("item_id must be an integer from 1 to %d" % MAX_ITEM_ID)
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
        con = None
        try:
            require_complete_restore(db_path)
            # MCP consumes an existing web-app database; it must never bootstrap
            # a missing file/schema or acquire write access for a read tool.
            mode = "rw" if allow_write and params["name"] in WRITE_TOOLS else "ro"
            uri = Path(db_path).resolve().as_uri() + "?mode=" + mode
            con = sqlite3.connect(uri, uri=True)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            page_info = {}
            data = call_tool(con, params["name"], params.get("arguments", {}), allow_write, page_info)
            result = {"content": [{"type": "text", "text": json.dumps(data, indent=2)}], "isError": False}
            if page_info.get("has_more"):
                result["content"].append({"type": "text", "text": (
                    "More records remain. Call %s with offset=%d and limit=%d. "
                    "Concurrent edits can move records between pages; this is not a snapshot."
                    % (params["name"], page_info["next_offset"], page_info["limit"]))})
        except ToolError as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except (sqlite3.Error, OSError):
            result = {"content": [{"type": "text", "text": (
                "inventory database unavailable; check the data folder and database integrity "
                "before retrying")}], "isError": True}
        finally:
            if con is not None:
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
    try:
        require_complete_restore(path)
    except ToolError as exc:
        sys.exit(str(exc))
    if not os.path.isfile(path):
        sys.exit("no database at %s: run the web app once first" % path)
    serve(path, args.allow_write)


if __name__ == "__main__":
    main()
