"""Tiny mobile-friendly web app. Standard library only, no accounts: run it on a
trusted network or behind your own reverse proxy with authentication."""
import argparse
import csv
import errno
import io
import hmac
import html
import ipaddress
import os
import re
import secrets
import stat
from http.cookies import SimpleCookie
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from . import db

MAX_BODY = 25 * 1024 * 1024
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
SDS_TYPES = {"application/pdf": ".pdf"}
SAFE_NAME = re.compile(r"^[0-9a-f]{16}\.(jpg|png|webp|pdf)$")

CSS = ("body{font:16px/1.65 system-ui,-apple-system,'Segoe UI',sans-serif;margin:0;padding:1rem;max-width:42rem;margin:auto;background:#fbfaf6;color:#40575b}"
       "h1{font:400 clamp(34px,8vw,48px)/1.1 Georgia,'Times New Roman',serif;letter-spacing:-.04em;color:#183b40;margin:.6rem 0 1rem}"
       "a{color:#16644f;text-underline-offset:4px}a:focus-visible,summary:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{outline:3px solid #a7442c;outline-offset:3px}"
       "nav{display:flex;gap:1.4rem;border-bottom:1px solid #d8e0dc;padding-bottom:.6rem}nav a{text-decoration:none;color:#183b40;font-weight:600;font-size:14px;min-height:44px;display:flex;align-items:center}"
       ".edit{display:inline-block;min-height:44px;line-height:44px;font-weight:600}input[type=checkbox]{width:auto;margin:0 .5rem 0 0}.filters label{font-weight:400}.filters label:first-child{font-weight:600}.filters label:not(:first-child){display:flex;align-items:center;min-height:44px}.filters a{display:inline-block;min-height:44px;line-height:44px}.pages{display:flex;align-items:center;gap:1rem;flex-wrap:wrap;margin:1rem 0}.pager{display:inline-block;min-height:44px;line-height:44px;font-weight:600}select{font:inherit;box-sizing:border-box;margin:.2rem 0 .8rem;padding:.55rem .7rem;border:1px solid #6f8580;border-radius:6px;background:#fff;color:#183b40;min-height:44px;max-width:100%}input,textarea,button{font:inherit;width:100%;box-sizing:border-box;margin:.2rem 0 .8rem;padding:.55rem .7rem;border:1px solid #6f8580;border-radius:6px;background:#fff;color:#183b40}"
       "button{background:#183b40;color:#fff;border:0;font-weight:600;min-height:48px;cursor:pointer}button:hover{background:#16644f}"
       ".card h2,.card h3{overflow-wrap:anywhere}.card h2{font-size:1.1rem;line-height:1.4;margin:0 0 .5rem}.card{background:#fff;border:1px solid #d8e0dc;border-radius:14px;padding:1rem;margin:.9rem 0}"
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
            "<main id=main tabindex=-1 aria-label='Main content'><h1>%s</h1>%s</main>"
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
    """Prefix potentially executable cells without discarding their original text.

    Spreadsheet importers can ignore leading whitespace or a BOM before a formula.
    This is a conservative export defence, not a guarantee for every spreadsheet.
    """
    text = "" if value is None else str(value)
    probe = text.lstrip(" \t\r\n\v\f\ufeff")
    if text[:1] in ("\t", "\r", "\n") or probe[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text


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


def review_sheet(items, q="", only_flagged=False, sort="", location=""):
    import datetime
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class=chk></td></tr>" % (
            e(it["name"]), e(it["location"]), e(it["quantity"]), e(it["expires_on"] or "?"),
            e("; ".join(db.flags(it)) or "-")) for it in items)
    return ("<p>Printed %s. %sTick each product once it has been checked on the shelf.</p>"
            "<table class=sheet><tr><th>Product</th><th>Where</th><th>Qty</th><th>Expires</th>"
            "<th>Flags</th><th>Checked</th></tr>%s</table>"
            "<p class=noprint><a href='/export.csv'>Download all active products (CSV)</a>. To print, use your browser's Print command.</p>"
            % (datetime.date.today().isoformat(), e(filter_summary(q, only_flagged, sort, location)),
               rows or "<tr><td colspan=6>No items.</td></tr>"))


SORTS = ("", "expiry", "location", "newest")


def sort_key(text):
    """A sort the list knows about; anything else falls back to the default order."""
    return text if text in SORTS else ""


def filter_items(items, q="", only_flagged=False, sort="", location=""):
    q = (q or "").strip().lower()[:100]
    location = (location or "")[:200]
    out = []
    for it in items:
        if q and q not in " ".join((it["name"], it["location"], it["notes"])).lower():
            continue
        if only_flagged and not db.flags(it):
            continue
        if location and it["location"] != location:
            continue
        out.append(it)
    if sort == "expiry":
        out.sort(key=db.expiry_sort_key)
    elif sort == "location":
        out.sort(key=lambda it: (it["location"].lower(), it["name"].lower()))
    elif sort == "newest":
        out.sort(key=lambda it: (it["created_on"], it["id"]), reverse=True)
    return out


def known_locations(items):
    """Distinct typed locations, for the filter dropdown. Exact values: the match is exact."""
    return sorted({it["location"] for it in items if it["location"]}, key=str.lower)


def filter_summary(q, only_flagged, sort, location):
    """One line naming the active filters, for the printed sheet."""
    parts = []
    if q:
        parts.append('search "%s"' % q)
    if only_flagged:
        parts.append("needs attention")
    if location:
        parts.append('location "%s"' % location)
    if sort == "expiry":
        parts.append("soonest expiry first")
    elif sort == "location":
        parts.append("grouped by location")
    elif sort == "newest":
        parts.append("newest first")
    return ("Filtered: " + "; ".join(parts) + ". ") if parts else ""


def filter_form(q, only_flagged, sort, location="", locations=()):
    sort_options = [("", "Name A-Z"), ("expiry", "Soonest expiry first"),
                    ("location", "Grouped by location"), ("newest", "Newest first")]
    sort_field = ("<label>Order <select name=sort>" + "".join(
        "<option value='%s'%s>%s</option>" % (e(value), " selected" if value == sort else "", e(label))
        for value, label in sort_options) + "</select></label>")
    location_field = ""
    if locations:
        location_field = ("<label>Only at location <select name=loc>"
                          "<option value=''>Every location</option>" + "".join(
            "<option value='%s'%s>%s</option>" % (e(loc), " selected" if loc == location else "", e(loc))
            for loc in locations) + "</select></label>")
    return ("<form method=get action='/' class=filters role=search>"
            "<label for=f-q>Search name, location or notes</label>"
            "<input id=f-q type=search name=q maxlength=100 value='" + e(q) + "'>"
            "<label><input type=checkbox name=flagged value=1" + (" checked" if only_flagged else "") +
            "> Only products that need attention</label>"
            + sort_field + location_field +
            "<button>Apply</button> <a href='/'>Clear</a></form>")


PAGE_SIZE = 100


def page_number(text):
    """A page number from ?page=. Only ASCII digits count: str.isdigit() also accepts
    characters such as superscript two that int() rejects."""
    return int(text) if text.isascii() and text.isdigit() and len(text) < 9 else 1


def list_args(q, only_flagged, sort, n=1, location=""):
    args = [("q", q)] if q else []
    if only_flagged:
        args.append(("flagged", "1"))
    if sort:
        args.append(("sort", sort))
    if location:
        args.append(("loc", location))
    if n > 1:
        args.append(("page", str(n)))
    return args


def list_state(values):
    """Search, filter, sort and page from request values, re-validated."""
    def one(key):
        got = values.get(key) or [""]
        return got[0] if isinstance(got, list) else got
    return (one("q")[:100], one("flagged") == "1", sort_key(one("sort")),
            page_number(one("page")), one("loc")[:200])


def page_links(q, only_flagged, sort, page, pages, location=""):
    """Previous/next links that keep the search and filters."""
    def href(n):
        args = list_args(q, only_flagged, sort, n, location)
        return "/?" + urlencode(args) if args else "/"
    parts = []
    if page > 1:
        parts.append("<a class=pager href='%s'>Previous</a>" % e(href(page - 1)))
    parts.append("<span>Page %d of %d</span>" % (page, pages))
    if page < pages:
        parts.append("<a class=pager href='%s'>Next</a>" % e(href(page + 1)))
    return "<div class=pages role=navigation aria-label='Pages of products'>" + " ".join(parts) + "</div>"


def render_list(items, token='', view=None):
    if not items:
        return "<p>No items yet. <a href='/add'>Add the first one.</a></p>"
    out = []
    back = "?" + urlencode(list_args(*view[:4], location=view[4])) if view and list_args(*view[:4], location=view[4]) else ""
    for it in items:
        fl = db.flags(it)
        chips = "".join("<span class='flag%s'>%s</span>" % (
            " bad" if f == "expired" else "", e(f)) for f in fl)
        photo = "<img src='/files/%s' alt=''>" % e(it["photo"]) if it["photo"] else ""
        sds = " <a id='sds-%d' href='/files/%s' aria-labelledby='sds-%d product-%d'>Safety data sheet</a>" % (it["id"], e(it["sds"]), it["id"], it["id"]) if it["sds"] else ""
        out.append(
            "<article class=card aria-labelledby='product-%d'><h2 id='product-%d'>%s</h2> %s<br>%s%s<br>Where: %s | Qty: %s | Expires: %s%s"
            "<br><a class=edit href='/edit/%d%s' id='edit-%d' aria-labelledby='edit-%d product-%d'>Edit</a>"
            "<details><summary id='retire-summary-%d' aria-labelledby='retire-summary-%d product-%d'>Retire this product</summary>"
            "<form method=post action='/retire/%d' aria-labelledby='retire-summary-%d product-%d'><input type=hidden name=csrf value='%s'>"
            "<label for='retire-reason-%d'>Reason for retiring (optional)</label><input id='retire-reason-%d' name=reason>"
            "<label for='retire-method-%d'>How it was disposed of (optional)</label><input id='retire-method-%d' name=method>"
            "<button id='retire-button-%d' aria-labelledby='retire-button-%d product-%d'>Retire</button></form></details></article>"
            % (it["id"], it["id"], e(it["name"]), chips, photo, e(it["notes"]), e(it["location"]), e(it["quantity"]),
               e(it["expires_on"] or "?"), sds, it["id"], e(back), it["id"], it["id"], it["id"],
               it["id"], it["id"], it["id"], it["id"], it["id"], it["id"], token,
               it["id"], it["id"], it["id"], it["id"], it["id"], it["id"], it["id"]))
    return "".join(out)


def add_form(token, item=None, view=None, values=None, error=None):
    v = (lambda k: e(item[k] or "")) if item else (lambda k: "")
    if values:
        v = (lambda k: e(values.get(k) or ""))
    action = "/edit/%d" % item["id"] if item else "/add"
    keep = (" (leave empty to keep the current file)" if item else "")
    args = list_args(*view) if view else []
    hidden = "".join("<input type=hidden name='%s' value='%s'>" % (k, e(val)) for k, val in args)
    back = ("<p><a href='/?%s'>Back to the list</a></p>" % e(urlencode(args)) if args
            else "<p><a href='/'>Back to the list</a></p>") if item else ""
    err = ("<p id=form-error role=alert>" + e(error) + "</p>") if error else ""
    return (back + err + "<form method=post action='" + action + "' enctype='multipart/form-data'>"
            "<input type=hidden name=csrf value='" + token + "'>" + hidden +
            "<label for=f-name>Name</label><input id=f-name name=name required autocomplete=off value='" + v("name") + "'>"
            "<label for=f-loc>Location</label><input id=f-loc name=location value='" + v("location") + "'>"
            "<label for=f-qty>Quantity</label><input id=f-qty name=quantity value='" + v("quantity") + "'>"
            "<label for=f-notes>Notes</label><textarea id=f-notes name=notes>" + v("notes") + "</textarea>"
            "<label for=f-open>Opened on</label><input id=f-open type=date name=opened_on value='" + v("opened_on") + "'>"
            "<label for=f-exp>Expires on</label><input id=f-exp type=date name=expires_on value='" + v("expires_on") + "'>"
            "<label for=f-photo>Photo" + keep + "</label><input id=f-photo type=file name=photo accept='image/*' capture=environment>"
            "<label for=f-sds>Safety data sheet (PDF)" + keep + "</label><input id=f-sds type=file name=sds accept='application/pdf'>"
            "<button>Save</button></form>")


def form_errors(fields):
    """Field-named form errors, from the shared model validation."""
    errs = []
    for key, label in (("opened_on", "Opened on"), ("expires_on", "Expires on")):
        raw = (fields.get(key) or "").strip()
        if raw:
            try:
                db.parse_date(raw)
            except ValueError:
                errs.append("%s: use the date format YYYY-MM-DD." % label)
    try:
        db.validate_item(fields.get("name", ""), fields.get("location", ""),
                         fields.get("quantity", ""), fields.get("notes", ""),
                         fields.get("opened_on"), fields.get("expires_on"))
    except ValueError as exc:
        msg = str(exc)
        if msg == "name is required" or not errs:
            errs.append(msg)
    return errs


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
    if os.path.lexists(os.path.join(data_dir, "RESTORE_INCOMPLETE")):
        raise ValueError("data directory has a RESTORE_INCOMPLETE marker: an earlier restore did not finish; restore again into a new directory")
    files_dir = os.path.join(data_dir, "files")
    if os.path.islink(files_dir):
        raise ValueError("data files directory must not be a symlink")
    os.makedirs(files_dir, exist_ok=True)
    files_root = os.path.realpath(files_dir)
    db_path = os.path.join(data_dir, "cabinet.sqlite3")
    db.connect(db_path).close()

    def save_file(upload, allowed):
        if upload is None:
            return None
        kind, data = upload
        if kind not in allowed or not sniff_ok(kind, data):
            raise ValueError("unsupported file type")
        for _ in range(5):
            name = secrets.token_hex(8) + allowed[kind]
            path = os.path.join(files_dir, name)
            try:
                fh = open(path, "xb")  # exclusive: never touches a file that already exists
            except FileExistsError:
                continue
            try:
                with fh:
                    fh.write(data)
            except BaseException:
                try:
                    os.remove(path)  # only the file this call created
                except OSError:
                    pass
                raise
            return name
        raise OSError("could not pick a free file name")

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
                    qs = parse_qs(urlparse(self.path).query)
                    q = (qs.get("q") or [""])[0][:100]
                    flagged = (qs.get("flagged") or [""])[0] == "1"
                    sort = sort_key((qs.get("sort") or [""])[0])
                    loc = (qs.get("loc") or [""])[0][:200]
                    total_active = con.execute(
                        "SELECT COUNT(*) FROM items WHERE retired_on IS NULL").fetchone()[0]
                    wanted = max(page_number((qs.get("page") or ["1"])[0]), 1)
                    count, rows = db.query_active_items(con, q, flagged, sort, limit=PAGE_SIZE,
                                                        offset=(wanted - 1) * PAGE_SIZE, location=loc)
                    pages = max(1, -(-count // PAGE_SIZE))
                    number = min(wanted, pages)
                    if number != wanted:
                        count, rows = db.query_active_items(con, q, flagged, sort, limit=PAGE_SIZE,
                                                            offset=(number - 1) * PAGE_SIZE, location=loc)
                    locations = known_locations([r for r in con.execute(
                        "SELECT DISTINCT location FROM items WHERE retired_on IS NULL")])
                    body = filter_form(q, flagged, sort, loc, locations) if total_active else ""
                    if total_active and not count:
                        body += "<p>No products match. <a href='/'>Clear the filters.</a></p>"
                    else:
                        if total_active and (q or flagged or loc):
                            body += ("<p>%d of %d products match. <a href='/review?%s'>Print this view</a></p>"
                                     % (count, total_active,
                                        e(urlencode(list_args(q, flagged, sort, location=loc)))))
                        first = (number - 1) * PAGE_SIZE
                        if pages > 1:
                            body += "<p>Showing %d-%d of %d products.</p>" % (
                                first + 1, min(first + PAGE_SIZE, count), count)
                        body += render_list(rows, token, (q, flagged, sort, number, loc))
                        if pages > 1:
                            body += page_links(q, flagged, sort, number, pages, loc)
                    self.send(200, page("Chemical cabinet", body), extra=cookie)
                elif path == "/add":
                    self.send(200, page("Add item", add_form(token)), extra=cookie)
                elif path.startswith("/edit/") and path[6:].isdigit():
                    it = db.get_item(con, int(path[6:]))
                    if it is None or it["retired_on"]:
                        return self.send(404, b"not found", "text/plain")
                    view = list_state(parse_qs(urlparse(self.path).query))
                    self.send(200, page("Edit item", add_form(token, it, view)), extra=cookie)
                elif path == "/export.csv":
                    self.send(200, items_csv(db.active_items(con)), "text/csv; charset=utf-8",
                              extra=[("Content-Disposition", "attachment; filename=chemical-cabinet.csv")])
                elif path == "/review":
                    qs = parse_qs(urlparse(self.path).query)
                    rq = (qs.get("q") or [""])[0][:100]
                    rflag = (qs.get("flagged") or [""])[0] == "1"
                    rsort = sort_key((qs.get("sort") or [""])[0])
                    rloc = (qs.get("loc") or [""])[0][:200]
                    self.send(200, page("Review sheet",
                                        review_sheet(filter_items(db.active_items(con), rq, rflag, rsort, rloc),
                                                     rq, rflag, rsort, rloc)))
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
                    if os.path.dirname(os.path.realpath(full)) != files_root:
                        return self.send(404, b"not found", "text/plain")
                    try:
                        if not stat.S_ISREG(os.lstat(full).st_mode):
                            return self.send(404, b"not found", "text/plain")
                        fd = os.open(full, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    except OSError as exc:
                        if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                            return self.send(404, b"not found", "text/plain")
                        raise
                    with os.fdopen(fd, "rb") as fh:
                        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                            return self.send(404, b"not found", "text/plain")
                        ctype = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp",
                                 "pdf": "application/pdf"}[name.rsplit(".", 1)[1]]
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
                    again = (" A file you chose was not stored; choose the photo or sheet again."
                             if files.get("photo") or files.get("sds") else "")
                    photo = sds = None
                    try:
                        photo = save_file(files.get("photo"), PHOTO_TYPES)
                        sds = save_file(files.get("sds"), SDS_TYPES)
                    except ValueError as exc:
                        remove_files(photo, sds)
                        return self.send(400, page("Add item", add_form(self.cookie_token(), values=fields, error=str(exc))))
                    except Exception:
                        remove_files(photo, sds)
                        raise
                    errs = form_errors(fields)
                    if errs:
                        remove_files(photo, sds)
                        return self.send(400, page("Add item", add_form(self.cookie_token(), values=fields, error=" ".join(errs) + again)))
                    try:
                        db.add_item(con, fields.get("name", ""), fields.get("location", ""),
                                    fields.get("quantity", ""), fields.get("notes", ""),
                                    fields.get("opened_on"), fields.get("expires_on"), photo, sds)
                    except ValueError as exc:
                        remove_files(photo, sds)
                        return self.send(400, page("Add item", add_form(self.cookie_token(), values=fields, error=str(exc) + again)))
                    except Exception:
                        remove_files(photo, sds)
                        raise
                    self.redirect("/")
                elif path.startswith("/edit/") and path[6:].isdigit():
                    fields, files = parse_multipart(self.headers.get("Content-Type", ""), body)
                    if not self.csrf_ok(fields.get("csrf")):
                        return self.send(403, b"missing or wrong form token", "text/plain")
                    again = (" A file you chose was not stored; choose the photo or sheet again."
                             if files.get("photo") or files.get("sds") else "")
                    photo = sds = None
                    old = db.get_item(con, int(path[6:]))
                    try:
                        photo = save_file(files.get("photo"), PHOTO_TYPES)
                        sds = save_file(files.get("sds"), SDS_TYPES)
                    except ValueError as exc:
                        remove_files(photo, sds)
                        return self.send(400, page("Edit item", add_form(self.cookie_token(), item=old, view=list_state(fields), values=fields, error=str(exc))))
                    except Exception:
                        remove_files(photo, sds)
                        raise
                    errs = form_errors(fields)
                    if errs:
                        remove_files(photo, sds)
                        return self.send(400, page("Edit item", add_form(self.cookie_token(), item=old, view=list_state(fields), values=fields, error=" ".join(errs) + again)))
                    try:
                        db.update_item(con, int(path[6:]), fields.get("name", ""), fields.get("location", ""),
                                       fields.get("quantity", ""), fields.get("notes", ""),
                                       fields.get("opened_on"), fields.get("expires_on"), photo, sds)
                    except KeyError:
                        remove_files(photo, sds)
                        return self.send(404, b"not found", "text/plain")
                    except ValueError as exc:
                        remove_files(photo, sds)
                        return self.send(400, page("Edit item", add_form(self.cookie_token(), item=old, view=list_state(fields), values=fields, error=str(exc) + again)))
                    except Exception:
                        remove_files(photo, sds)
                        raise
                    if old is not None:
                        remove_files(old["photo"] if photo else None, old["sds"] if sds else None)
                    args = list_args(*list_state(fields))
                    self.redirect("/?" + urlencode(args) if args else "/")
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
