"""Tiny mobile-friendly web app. Standard library only, no accounts: run it on a
trusted network or behind your own reverse proxy with authentication."""
import argparse
import html
import os
import re
import secrets
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db

MAX_BODY = 25 * 1024 * 1024
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
SDS_TYPES = {"application/pdf": ".pdf"}
SAFE_NAME = re.compile(r"^[0-9a-f]{16}\.(jpg|png|webp|pdf)$")

CSS = ("body{font-family:system-ui,sans-serif;margin:0;padding:1rem;max-width:42rem;margin:auto}"
       "input,textarea,button{font:inherit;width:100%;box-sizing:border-box;margin:.2rem 0 .7rem;padding:.5rem}"
       "button{background:#1d4ed8;color:#fff;border:0;border-radius:.4rem}"
       ".card{border:1px solid #ccc;border-radius:.5rem;padding:.7rem;margin:.7rem 0}"
       ".flag{background:#fde68a;border-radius:.3rem;padding:0 .4rem;margin-right:.3rem;font-size:.85rem}"
       ".bad{background:#fecaca}img{max-width:100%;border-radius:.4rem}"
       "nav a{margin-right:1rem}.note{color:#555;font-size:.9rem}")


def page(title, body):
    return ("<!doctype html><html lang=en><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>%s</title><style>%s</style>"
            "<nav><a href='/'>Cabinet</a><a href='/add'>Add</a><a href='/log'>Disposal log</a></nav>"
            "<h1>%s</h1>%s"
            "<p class=note>A tracking aid only. It does not check legal compliance or "
            "chemical compatibility. Follow the safety data sheet and your local rules.</p>"
            % (html.escape(title), CSS, html.escape(title), body)).encode()


def e(value):
    return html.escape(str(value or ""))


def render_list(items):
    if not items:
        return "<p>No items yet. <a href='/add'>Add the first one.</a></p>"
    out = []
    for it in items:
        fl = db.flags(it)
        chips = "".join("<span class='flag%s'>%s</span>" % (
            " bad" if f == "expired" else "", e(f)) for f in fl)
        photo = "<img src='/files/%s' alt=''>" % e(it["photo"]) if it["photo"] else ""
        sds = " <a href='/files/%s'>Safety data sheet</a>" % e(it["sds"]) if it["sds"] else ""
        out.append(
            "<div class=card><b>%s</b> %s<br>%s%s<br>Where: %s | Qty: %s | Expires: %s%s"
            "<form method=post action='/retire/%d'><input name=reason placeholder='Reason'>"
            "<input name=method placeholder='How it was disposed of'><button>Retire</button></form></div>"
            % (e(it["name"]), chips, photo, e(it["notes"]), e(it["location"]), e(it["quantity"]),
               e(it["expires_on"] or "?"), sds, it["id"]))
    return "".join(out)


ADD_FORM = ("<form method=post action='/add' enctype='multipart/form-data'>"
            "Name<input name=name required>Location<input name=location>Quantity<input name=quantity>"
            "Notes<textarea name=notes></textarea>Opened on<input type=date name=opened_on>"
            "Expires on<input type=date name=expires_on>"
            "Photo<input type=file name=photo accept='image/*' capture=environment>"
            "Safety data sheet (PDF)<input type=file name=sds accept='application/pdf'>"
            "<button>Save</button></form>")


def parse_multipart(content_type, body):
    """Return (fields, files) from a multipart body. files maps name -> (type, bytes)."""
    msg = BytesParser(policy=policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    fields, files = {}, {}
    if not msg.is_multipart():
        return fields, files
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        data = part.get_payload(decode=True) or b""
        if part.get_filename() is not None:
            if data:
                files[name] = (part.get_content_type(), data)
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


def sniff_ok(kind, data):
    if kind == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if kind == "image/png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if kind == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if kind == "application/pdf":
        return data[:5] == b"%PDF-"
    return False


def make_handler(data_dir):
    files_dir = os.path.join(data_dir, "files")
    os.makedirs(files_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "cabinet.sqlite3")
    db.connect(db_path).close()

    def save_file(upload, allowed):
        if upload is None:
            return None
        kind, data = upload
        if kind not in allowed or not sniff_ok(kind, data):
            raise ValueError("unsupported file type")
        name = secrets.token_hex(8) + allowed[kind]
        with open(os.path.join(files_dir, name), "wb") as fh:
            fh.write(data)
        return name

    class Handler(BaseHTTPRequestHandler):
        server_version = "chemical-cabinet"

        def send(self, status, body, ctype="text/html; charset=utf-8", extra=()):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def redirect(self, to):
            self.send(303, b"", extra=[("Location", to)])

        def do_GET(self):
            path = urlparse(self.path).path
            con = db.connect(db_path)
            try:
                if path == "/":
                    self.send(200, page("Chemical cabinet", render_list(db.active_items(con))))
                elif path == "/add":
                    self.send(200, page("Add item", ADD_FORM))
                elif path == "/log":
                    rows = db.retirement_log(con)
                    body = "".join("<div class=card><b>%s</b> - %s<br>%s<br>%s</div>" % (
                        e(r["name"]), e(r["retired_on"]), e(r["reason"]), e(r["method"])) for r in rows)
                    self.send(200, page("Disposal log", body or "<p>Nothing retired yet.</p>"))
                elif path.startswith("/files/"):
                    name = path[len("/files/"):]
                    if not SAFE_NAME.match(name):
                        return self.send(404, b"not found", "text/plain")
                    full = os.path.join(files_dir, name)
                    if not os.path.isfile(full):
                        return self.send(404, b"not found", "text/plain")
                    ctype = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp",
                             "pdf": "application/pdf"}[name.rsplit(".", 1)[1]]
                    with open(full, "rb") as fh:
                        self.send(200, fh.read(), ctype)
                else:
                    self.send(404, b"not found", "text/plain")
            finally:
                con.close()

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self.send(413, b"too large", "text/plain")
            body = self.rfile.read(length)
            path = urlparse(self.path).path
            con = db.connect(db_path)
            try:
                if path == "/add":
                    fields, files = parse_multipart(self.headers.get("Content-Type", ""), body)
                    try:
                        photo = save_file(files.get("photo"), PHOTO_TYPES)
                        sds = save_file(files.get("sds"), SDS_TYPES)
                        db.add_item(con, fields.get("name", ""), fields.get("location", ""),
                                    fields.get("quantity", ""), fields.get("notes", ""),
                                    fields.get("opened_on"), fields.get("expires_on"), photo, sds)
                    except ValueError as exc:
                        return self.send(400, page("Error", "<p>%s</p>" % e(exc)))
                    self.redirect("/")
                elif path.startswith("/retire/"):
                    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
                    try:
                        db.retire_item(con, int(path.rsplit("/", 1)[1]),
                                       form.get("reason", ""), form.get("method", ""))
                    except (ValueError, KeyError):
                        return self.send(400, page("Error", "<p>Cannot retire this item.</p>"))
                    self.redirect("/log")
                else:
                    self.send(404, b"not found", "text/plain")
            finally:
                con.close()

        def log_message(self, fmt, *args):
            pass

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chemical cabinet inventory")
    ap.add_argument("--data", default="data", help="folder for the database and uploads")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args(argv)
    ThreadingHTTPServer((args.host, args.port), make_handler(args.data)).serve_forever()


if __name__ == "__main__":
    main()
