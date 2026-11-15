"""Tiny mobile-friendly web app. Standard library only, no accounts: run it on a
trusted network or behind your own reverse proxy with authentication."""
import argparse
import csv
import io
import hmac
import html
import ipaddress
import os
import re
import secrets
from http.cookies import SimpleCookie
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db

MAX_BODY = 25 * 1024 * 1024
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
SDS_TYPES = {"application/pdf": ".pdf"}
SAFE_NAME = re.compile(r"^[0-9a-f]{16}\.(jpg|png|webp|pdf)$")

CSS = ("body{font:16px/1.65 system-ui,-apple-system,'Segoe UI',sans-serif;margin:0;padding:1rem;max-width:42rem;margin:auto;background:#fbfaf6;color:#40575b}"
       "h1{font:400 clamp(34px,8vw,48px)/1.1 Georgia,'Times New Roman',serif;letter-spacing:-.04em;color:#183b40;margin:.6rem 0 1rem}"
       "a{color:#16644f;text-underline-offset:4px}a:focus-visible,summary:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #a7442c;outline-offset:3px}"
       "nav{display:flex;gap:1.4rem;border-bottom:1px solid #d8e0dc;padding-bottom:.6rem}nav a{text-decoration:none;color:#183b40;font-weight:600;font-size:14px;min-height:44px;display:flex;align-items:center}"
       "input,textarea,button{font:inherit;width:100%;box-sizing:border-box;margin:.2rem 0 .8rem;padding:.55rem .7rem;border:1px solid #d8e0dc;border-radius:6px;background:#fff;color:#183b40}"
       "button{background:#183b40;color:#fff;border:0;font-weight:600;min-height:48px;cursor:pointer}button:hover{background:#16644f}"
       ".card h2,.card h3{overflow-wrap:anywhere}.card{background:#fff;border:1px solid #d8e0dc;border-radius:14px;padding:1rem;margin:.9rem 0}"
       ".flag{display:inline-block;margin-bottom:.3rem;background:#f3e7c6;color:#4a3a10;border-radius:999px;padding:0 .6rem;margin-right:.3rem;font-size:.8rem;font-weight:600;}"
       ".bad{background:#f2d3c9;color:#6b2412}img{max-width:100%;border-radius:8px}"
       "summary{cursor:pointer;color:#16644f;margin-top:.6rem;min-height:44px;font-weight:600}.note{color:#657579;font-size:.85rem;border-left:3px solid #16644f;padding-left:.8rem}"
       "label{display:block;font-weight:600;color:#183b40;font-size:.9rem}.skip{position:absolute;left:-999px}.skip:focus{left:8px;top:8px;background:#fff;padding:.5rem;z-index:5}.sheet{border-collapse:collapse;width:100%}.sheet th,.sheet td{border:1px solid #d8e0dc;padding:.4rem;text-align:left;font-size:.85rem;vertical-align:top}.chk{width:4rem}"
       "@media print{nav,.noprint,.note{display:none}body{max-width:none;background:#fff}}"
       "@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}")


def page(title, body):
    return ("<!doctype html><html lang=en><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>%s</title><style>%s</style>"
            "<a class=skip href='#main'>Skip to content</a><nav aria-label=Main><a href='/'>Cabinet</a><a href='/add'>Add</a><a href='/log'>Disposal log</a><a href='/review'>Print sheet</a></nav>"
            "<main id=main><h1>%s</h1>%s</main>"
            "<p class=note>A tracking aid only. It does not check legal compliance or "
            "chemical compatibility. Follow the safety data sheet and your local rules.</p>"
            % (html.escape(title), CSS, html.escape(title), body)).encode()


TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
COOKIE = "cc_csrf"


def host_ok(host_header, allowed=()):
    """Accept localhost, IP literals and explicitly allowed names. A browser reaching
    this server through a rebound DNS name sends that name in Host, so it is refused."""
    host = (host_header or "").strip().lower()
    if not host:
        return False
    if host.startswith("["):
        name = host[1:].split("]", 1)[0]
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if name == "localhost" or name in {a.lower() for a in allowed}:
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def origin_ok(headers):
    """For POST: refuse requests a browser marks as cross-site or whose Origin is not us."""
    site = headers.get("Sec-Fetch-Site")
    if site not in (None, "same-origin", "none"):
        return False
    origin = headers.get("Origin")
    if origin is None:
        return True
    return urlparse(origin).netloc.lower() == (headers.get("Host") or "").lower()


def e(value):
    return html.escape(str(value or ""))


def csv_cell(value):
    """Neutralise spreadsheet formulas in text that people typed."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def items_csv(items):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["id", "name", "location", "quantity", "opened_on", "expires_on",
                "has_photo", "has_safety_data_sheet", "flags", "notes"])
    for it in items:
        w.writerow([it["id"], csv_cell(it["name"]), csv_cell(it["location"]), csv_cell(it["quantity"]),
                    it["opened_on"] or "", it["expires_on"] or "", "yes" if it["photo"] else "no",
                    "yes" if it["sds"] else "no", csv_cell("; ".join(db.flags(it))), csv_cell(it["notes"])])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def review_sheet(items):
    import datetime
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class=chk></td></tr>" % (
            e(it["name"]), e(it["location"]), e(it["quantity"]), e(it["expires_on"] or "?"),
            e("; ".join(db.flags(it)) or "-")) for it in items)
    return ("<p>Printed %s. Tick each product once it has been checked on the shelf.</p>"
            "<table class=sheet><tr><th>Product</th><th>Where</th><th>Qty</th><th>Expires</th>"
            "<th>Flags</th><th>Checked</th></tr>%s</table>"
            "<p class=noprint><a href='/export.csv'>Download CSV</a>. To print, use your browser's Print command.</p>"
            % (datetime.date.today().isoformat(), rows or "<tr><td colspan=6>No items.</td></tr>"))


def render_list(items, token=''):
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
            "<a href='/edit/%d'>Edit</a><details><summary>Retire this product</summary>"
            "<form method=post action='/retire/%d'><input type=hidden name=csrf value='%s'><input name=reason placeholder='Reason' aria-label='Reason for retiring'>"
            "<input name=method placeholder='How it was disposed of' aria-label='How it was disposed of'><button>Retire</button></form></details></div>"
            % (e(it["name"]), chips, photo, e(it["notes"]), e(it["location"]), e(it["quantity"]),
               e(it["expires_on"] or "?"), sds, it["id"], it["id"], token))
    return "".join(out)


def add_form(token, item=None):
    v = (lambda k: e(item[k] or "")) if item else (lambda k: "")
    action = "/edit/%d" % item["id"] if item else "/add"
    keep = (" (leave empty to keep the current file)" if item else "")
    return ("<form method=post action='" + action + "' enctype='multipart/form-data'>"
            "<input type=hidden name=csrf value='" + token + "'>"
            "<label for=f-name>Name</label><input id=f-name name=name required autocomplete=off value='" + v("name") + "'>"
            "<label for=f-loc>Location</label><input id=f-loc name=location value='" + v("location") + "'>"
            "<label for=f-qty>Quantity</label><input id=f-qty name=quantity value='" + v("quantity") + "'>"
            "<label for=f-notes>Notes</label><textarea id=f-notes name=notes>" + v("notes") + "</textarea>"
            "<label for=f-open>Opened on</label><input id=f-open type=date name=opened_on value='" + v("opened_on") + "'>"
            "<label for=f-exp>Expires on</label><input id=f-exp type=date name=expires_on value='" + v("expires_on") + "'>"
            "<label for=f-photo>Photo" + keep + "</label><input id=f-photo type=file name=photo accept='image/*' capture=environment>"
            "<label for=f-sds>Safety data sheet (PDF)" + keep + "</label><input id=f-sds type=file name=sds accept='application/pdf'>"
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


def make_handler(data_dir, allowed_hosts=()):
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

    def remove_files(*names):
        for n in names:
            if n and SAFE_NAME.match(n):
                try:
                    os.remove(os.path.join(files_dir, n))
                except OSError:
                    pass

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

        def guard(self, post):
            """Host check on every request, Origin and CSRF checks on POST."""
            if not host_ok(self.headers.get("Host"), allowed_hosts):
                self.send(403, b"host not allowed", "text/plain")
                return False
            if post and not origin_ok(self.headers):
                self.send(403, b"cross-site request refused", "text/plain")
                return False
            return True

        def cookie_token(self):
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie", ""))
            except Exception:
                return None
            morsel = jar.get(COOKIE)
            return morsel.value if morsel and TOKEN_RE.match(morsel.value) else None

        def csrf_ok(self, submitted):
            token = self.cookie_token()
            return bool(token) and hmac.compare_digest(token, submitted or "")

        def redirect(self, to):
            self.send(303, b"", extra=[("Location", to)])

        def do_GET(self):
            if not self.guard(False):
                return
            path = urlparse(self.path).path
            token = self.cookie_token()
            cookie = []
            if token is None:
                token = secrets.token_hex(16)
                cookie = [("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Strict" % (COOKIE, token))]
            con = db.connect(db_path)
            try:
                if path == "/":
                    self.send(200, page("Chemical cabinet", render_list(db.active_items(con), token)), extra=cookie)
                elif path == "/add":
                    self.send(200, page("Add item", add_form(token)), extra=cookie)
                elif path.startswith("/edit/") and path[6:].isdigit():
                    it = db.get_item(con, int(path[6:]))
                    if it is None or it["retired_on"]:
                        return self.send(404, b"not found", "text/plain")
                    self.send(200, page("Edit item", add_form(token, it)), extra=cookie)
                elif path == "/export.csv":
                    self.send(200, items_csv(db.active_items(con)), "text/csv; charset=utf-8",
                              extra=[("Content-Disposition", "attachment; filename=chemical-cabinet.csv")])
                elif path == "/review":
                    self.send(200, page("Review sheet", review_sheet(db.active_items(con))))
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
            if not self.guard(True):
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self.send(400, b"bad content length", "text/plain")
            if length < 0:
                return self.send(400, b"bad content length", "text/plain")
            if length > MAX_BODY:
                return self.send(413, b"too large", "text/plain")
            body = self.rfile.read(length)
            path = urlparse(self.path).path
            con = db.connect(db_path)
            try:
                if path == "/add":
                    fields, files = parse_multipart(self.headers.get("Content-Type", ""), body)
                    if not self.csrf_ok(fields.get("csrf")):
                        return self.send(403, b"missing or wrong form token", "text/plain")
                    photo = sds = None
                    try:
                        photo = save_file(files.get("photo"), PHOTO_TYPES)
                        sds = save_file(files.get("sds"), SDS_TYPES)
                        db.add_item(con, fields.get("name", ""), fields.get("location", ""),
                                    fields.get("quantity", ""), fields.get("notes", ""),
                                    fields.get("opened_on"), fields.get("expires_on"), photo, sds)
                    except ValueError as exc:
                        remove_files(photo, sds)
                        return self.send(400, page("Error", "<p>%s</p>" % e(exc)))
                    except Exception:
                        remove_files(photo, sds)
                        raise
                    self.redirect("/")
                elif path.startswith("/edit/") and path[6:].isdigit():
                    fields, files = parse_multipart(self.headers.get("Content-Type", ""), body)
                    if not self.csrf_ok(fields.get("csrf")):
                        return self.send(403, b"missing or wrong form token", "text/plain")
                    photo = sds = None
                    old = db.get_item(con, int(path[6:]))
                    try:
                        photo = save_file(files.get("photo"), PHOTO_TYPES)
                        sds = save_file(files.get("sds"), SDS_TYPES)
                        db.update_item(con, int(path[6:]), fields.get("name", ""), fields.get("location", ""),
                                       fields.get("quantity", ""), fields.get("notes", ""),
                                       fields.get("opened_on"), fields.get("expires_on"), photo, sds)
                    except KeyError:
                        remove_files(photo, sds)
                        return self.send(404, b"not found", "text/plain")
                    except ValueError as exc:
                        remove_files(photo, sds)
                        return self.send(400, page("Error", "<p>%s</p>" % e(exc)))
                    except Exception:
                        remove_files(photo, sds)
                        raise
                    if old is not None:
                        remove_files(old["photo"] if photo else None, old["sds"] if sds else None)
                    self.redirect("/")
                elif path.startswith("/retire/"):
                    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
                    if not self.csrf_ok(form.get("csrf")):
                        return self.send(403, b"missing or wrong form token", "text/plain")
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
    ap.add_argument("--allowed-host", action="append", default=[],
                    help="extra host name the app may be reached by, for example cabinet.local")
    args = ap.parse_args(argv)
    ThreadingHTTPServer((args.host, args.port),
                        make_handler(args.data, args.allowed_host)).serve_forever()


if __name__ == "__main__":
    main()
