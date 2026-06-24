import os
import base64
import binascii
import io
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dotenv import load_dotenv
from flask import Flask, send_from_directory, request, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
import qrcode
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

# Guarded import of pywin32's win32print. On hosts where pywin32 is not
# installed, the server still imports cleanly and reports a clear error if
# normal print mode is used.
try:
    import win32print
    WIN32PRINT_AVAILABLE = True
except ImportError:
    win32print = None
    WIN32PRINT_AVAILABLE = False

try:
    import wmi
    WMI_AVAILABLE = True
except ImportError:
    wmi = None
    WMI_AVAILABLE = False

# Load non-secret printer configuration from printer_config.json.
# Secrets must come from environment variables so they are not exposed by
# configuration files or browser JavaScript.
try:
    with open('printer_config.json', 'r') as config_file:
        config = json.load(config_file)
        PRINTER_QUEUE = config.get('PRINTER_QUEUE', 'ZDesigner ZD230-203dpi ZPL')
        USE_TEST_MODE = config.get('USE_TEST_MODE', False)
        ZEBRA_USB_SERIAL = str(config.get('ZEBRA_USB_SERIAL', '')).strip()
        ZEBRA_USB_SERIALS = config.get('ZEBRA_USB_SERIALS', [])
        if isinstance(ZEBRA_USB_SERIALS, str):
            ZEBRA_USB_SERIALS = [x.strip() for x in ZEBRA_USB_SERIALS.split(',') if x.strip()]
        elif not isinstance(ZEBRA_USB_SERIALS, list):
            ZEBRA_USB_SERIALS = []
        if ZEBRA_USB_SERIAL and ZEBRA_USB_SERIAL not in ZEBRA_USB_SERIALS:
            ZEBRA_USB_SERIALS.insert(0, ZEBRA_USB_SERIAL)
        ZEBRA_PRINTERS = config.get('ZEBRA_PRINTERS', [])
        if not isinstance(ZEBRA_PRINTERS, list):
            ZEBRA_PRINTERS = []
        ZEBRA_MODEL_KEYWORDS = config.get('ZEBRA_MODEL_KEYWORDS', ['ZDesigner', 'Zebra', 'ZD230', 'ZD421', 'ZD621', 'ZT', 'GK', 'GX'])
        ALLOW_PRINTER_QUEUE_FALLBACK = bool(config.get('ALLOW_PRINTER_QUEUE_FALLBACK', False))
except FileNotFoundError:
    print("Warning: printer_config.json not found. Using default configuration.")
    PRINTER_QUEUE = "ZDesigner ZD230-203dpi ZPL"
    USE_TEST_MODE = False
    ZEBRA_USB_SERIAL = ""
    ZEBRA_USB_SERIALS = []
    ZEBRA_PRINTERS = []
    ZEBRA_MODEL_KEYWORDS = ['ZDesigner', 'Zebra', 'ZD230', 'ZD421', 'ZD621', 'ZT', 'GK', 'GX']
    ALLOW_PRINTER_QUEUE_FALLBACK = False
except json.JSONDecodeError:
    print("Warning: printer_config.json is not valid JSON. Using default configuration.")
    PRINTER_QUEUE = "ZDesigner ZD230-203dpi ZPL"
    USE_TEST_MODE = False
    ZEBRA_USB_SERIAL = ""
    ZEBRA_USB_SERIALS = []
    ZEBRA_PRINTERS = []
    ZEBRA_MODEL_KEYWORDS = ['ZDesigner', 'Zebra', 'ZD230', 'ZD421', 'ZD621', 'ZT', 'GK', 'GX']
    ALLOW_PRINTER_QUEUE_FALLBACK = False

if "USE_TEST_MODE" in os.environ:
    USE_TEST_MODE = os.environ["USE_TEST_MODE"].strip().lower() in {"1", "true", "yes", "on"}
if "ZEBRA_USB_SERIAL" in os.environ:
    ZEBRA_USB_SERIAL = os.environ["ZEBRA_USB_SERIAL"].strip()
    if ZEBRA_USB_SERIAL and ZEBRA_USB_SERIAL not in ZEBRA_USB_SERIALS:
        ZEBRA_USB_SERIALS.insert(0, ZEBRA_USB_SERIAL)
if "ZEBRA_USB_SERIALS" in os.environ:
    ZEBRA_USB_SERIALS = [x.strip() for x in os.environ["ZEBRA_USB_SERIALS"].split(',') if x.strip()]
    ZEBRA_USB_SERIAL = ZEBRA_USB_SERIALS[0] if ZEBRA_USB_SERIALS else ""
if "ZEBRA_MODEL_KEYWORDS" in os.environ:
    ZEBRA_MODEL_KEYWORDS = [x.strip() for x in os.environ["ZEBRA_MODEL_KEYWORDS"].split(",") if x.strip()]
if "ALLOW_PRINTER_QUEUE_FALLBACK" in os.environ:
    ALLOW_PRINTER_QUEUE_FALLBACK = os.environ["ALLOW_PRINTER_QUEUE_FALLBACK"].strip().lower() in {"1", "true", "yes", "on"}

PRINT_API_KEY = os.environ.get("PRINT_API_KEY", "")
EXTERNAL_API_KEY = os.environ.get("EXTERNAL_API_KEY", "")
EXTERNAL_API_BASE_URL = os.environ.get("EXTERNAL_API_BASE_URL", "https://tickets.w.media/link")
ATTENDEE_CACHE_TTL_SECONDS = int(os.environ.get("ATTENDEE_CACHE_TTL_SECONDS", "300"))
ATTENDEE_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}

# Initialize the Flask web server
app = Flask(__name__, static_folder='.')

# When running behind a TLS-terminating tunnel/proxy (e.g. ngrok), trust the
# X-Forwarded-Proto / X-Forwarded-Host headers it sends so that request.host_url
# reflects the public https origin the browser actually used. Without this the
# same-origin guard rejects legitimate same-origin browser calls with HTTP 401,
# because Flask only sees the internal "http://...:8080" origin while the page
# was loaded over "https://<name>.ngrok-free.app".
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


# Prevent browser caching of HTML/CSS/JS so deployments are picked up immediately.
@app.after_request
def add_no_cache_headers(response):
    if response.content_type and ('text/html' in response.content_type or 'text/css' in response.content_type or 'javascript' in response.content_type):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


# Static file serving routes
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    normalized = filename.replace("\\", "/")
    if normalized == "index.html":
        return send_from_directory('.', 'index.html')

    allowed_roots = ("fonts/", "assets/")
    allowed_extensions = {".css", ".woff", ".woff2", ".ttf", ".otf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}
    _, ext = os.path.splitext(normalized.lower())
    if (
        ".." in normalized.split("/")
        or normalized.startswith("/")
        or not normalized.startswith(allowed_roots)
        or ext not in allowed_extensions
    ):
        return jsonify({"success": False, "error": "Not found."}), 404

    return send_from_directory('.', normalized)


def _request_is_same_origin() -> bool:
    """Return True when browser-originated writes come from this app's origin."""
    expected = request.host_url.rstrip("/")
    saw_origin_header = False
    for header in ("Origin", "Referer"):
        value = request.headers.get(header)
        if not value:
            continue
        saw_origin_header = True
        parsed = urllib.parse.urlparse(value)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return origin == expected
    return False if request.method not in {"GET", "HEAD", "OPTIONS"} else saw_origin_header


def _print_request_authorized() -> bool:
    """Authorize local browser requests, plus optional env-keyed API clients."""
    provided = request.headers.get("X-API-Key", "")
    if PRINT_API_KEY and provided and secrets.compare_digest(provided, PRINT_API_KEY):
        return True
    return _request_is_same_origin()


def _get_cached_attendee(param_type: str, param_value: str) -> dict | None:
    cached = ATTENDEE_CACHE.get((param_type, param_value))
    if not cached:
        return None
    expires_at, attendee = cached
    if expires_at <= time.time():
        ATTENDEE_CACHE.pop((param_type, param_value), None)
        return None
    return attendee


def _set_cached_attendee(param_type: str, param_value: str, attendee: dict) -> None:
    ATTENDEE_CACHE[(param_type, param_value)] = (
        time.time() + ATTENDEE_CACHE_TTL_SECONDS,
        attendee,
    )

def derive_name_line1(name: str) -> str:
    """
    Pure helper that derives the first-line display name for the badge from the
    raw (already ASCII-stripped) `name` string.

    The logic is the verbatim block previously living inline inside
    print_badge: it handles
      * titles ending in '.' (e.g. 'Ir. Amir Kamarol' -> 'Ir. Amir'),
      * single-letter middle initials (e.g. 'Putra R Kurniawan' -> 'Putra'),
      * two- and three-or-more-name handling (e.g. 'Yow Chuan Teo' -> 'Yow Chuan'),
      * single-name passthrough (e.g. 'Jane' -> 'Jane'),
      * empty-string fallback ('' -> '').

    Behavior must match the inline block exactly; do not refactor.
    Extracted per task 3.1 of spec zpl-printer-migration.
    """
    name_parts = name.split()
    if len(name_parts) > 0:
        # Check if the first part is a title (ends with a dot)
        if name_parts[0].endswith('.'):
            # If the first part is a title, combine it with the next part (first name)
            if len(name_parts) > 1:
                name_line1 = name_parts[0] + " " + name_parts[1]  # e.g., "Ir. Amir"
                # For names with titles, only include third part under very specific conditions
                # This ensures "Ir. AMIR KAMAROL" becomes "Ir. AMIR" not "Ir. AMIR KAMAROL"
                if len(name_parts) > 2:
                    # Include third part only if the title + first name is very short
                    # This prevents "Ir. AMIR KAMAROL" from becoming "Ir. AMIR KAMAROL"
                    combined_name = name_parts[0] + " " + name_parts[1] + " " + name_parts[2]
                    if len(name_parts[0] + " " + name_parts[1]) < 6 and len(combined_name) <= 25:
                        name_line1 = combined_name
                    # Otherwise stick with just the title and first name
            else:
                name_line1 = name_parts[0]  # Just the title if no following name
        else:
            # If no title, handle different name patterns
            # This handles cases like "Yow Chuan Teo" -> "Yow Chuan" and "Putra R Kurniawan" -> "Putra"
            if len(name_parts) == 1:
                # Just one name part, use as is
                name_line1 = name_parts[0]
            elif len(name_parts) == 2:
                # Two name parts, use both: "Yow Chuan" -> "Yow Chuan"
                name_line1 = name_parts[0] + " " + name_parts[1]
            elif len(name_parts) >= 3:
                # Three or more name parts - check for initial pattern like "R" in "Putra R Kurniawan"
                first_name = name_parts[0]
                middle_part = name_parts[1]

                # If the middle part is a single character (initial), use only the first name
                if len(middle_part) == 1:
                    name_line1 = first_name
                else:
                    # For cases like "Yow Chuan Teo", use first and second names
                    name_line1 = first_name + " " + middle_part
                    # Only include third part under special conditions
                    if len(name_parts) > 2:
                        combined_name = first_name + " " + middle_part + " " + name_parts[2]
                        # Include third part only if first two are very short
                        if len(first_name + " " + middle_part) < 8 and len(combined_name) <= 25:
                            name_line1 = combined_name
    else:
        name_line1 = name  # Fallback if name is empty
    return name_line1

def split_company_lines(company: str) -> tuple[str, str]:
    """
    Pure helper that splits a company string into up to two badge lines.

    Returns a 2-tuple ``(line1, line2)``:
      * if ``len(company) <= 20``: ``(company, "")`` - the input is returned
        unchanged on line 1 and there is no second line.
      * otherwise the verbatim three-stage strategy from the legacy inline
        block is applied:

        1. regex ``^(.{0,20})\\s+(.*)`` - if it matches, both groups are
           ``.strip()``-ed and returned.
        2. fallback ``company.find(' ', 15)`` - if a space is found at an
           index ``!= -1`` AND ``<= 25``, split at that index.
        3. final fallback - hard split at column 20.

    Behavior must match the inline block in ``print_badge`` exactly;
    do not refactor. Extracted per task 3.2 of spec zpl-printer-migration.
    """
    if len(company) <= 20:
        return company, ""

    # Split company name at word boundaries to avoid breaking words
    match = re.search(r'^(.{0,20})\s+(.*)', company)
    if match:
        company_line1 = match.group(1).strip()
        company_line2 = match.group(2).strip()
    else:
        # If no space found in first 20 chars, try to split at a reasonable point
        space_after_15 = company.find(' ', 15)
        if space_after_15 != -1 and space_after_15 <= 25:  # Found a space after char 15
            company_line1 = company[:space_after_15].strip()
            company_line2 = company[space_after_15 + 1:].strip()
        else:
            # No suitable space found, split at 20 characters
            company_line1 = company[:20].strip()
            company_line2 = company[20:].strip()
    return company_line1, company_line2

def _zpl_escape(s: str) -> str:
    """Replace every '^' and every '~' in `s` with a single space character.

    These are the two ZPL II control characters that, if left embedded inside an
    ^FD ... ^FS data block, could be reinterpreted by the printer as the start
    of a new command (^XA, ^FS, ~CC, etc). Replacing them with spaces preserves
    layout while neutralizing the parser-confusion risk.

    Per spec zpl-printer-migration task 4.1 (requirements 5.3).
    """
    return s.replace('^', ' ').replace('~', ' ')


def _qr_payload(code: str) -> str:
    """Return the QR data payload for the badge.

    Returns ``_zpl_escape(code)`` when ``code.strip() != ""``, otherwise the
    literal ``"NO-CODE"`` (also passed through ``_zpl_escape`` for symmetry).

    Per spec zpl-printer-migration task 4.1 (requirements 4.4).
    """
    if code is not None and code.strip() != "":
        return _zpl_escape(code)
    return _zpl_escape("NO-CODE")


def _qr_to_gfa(data: str, target_dots: int = 400) -> str:
    """Generate a QR code as a ZPL ^GFA graphic field at the specified size in dots.

    Uses the Python qrcode library to render the QR, scales it to target_dotsÃ—target_dots,
    then converts the 1-bit bitmap to ZPL ^GFA hex format.

    Returns the full ^GFA...^FS command string (without ^FO positioning).
    """
    # Generate QR with medium error correction
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=0,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("1")

    # Scale to target size
    img = img.resize((target_dots, target_dots), Image.NEAREST)

    # Convert to ZPL ^GFA hex
    width_bytes = (target_dots + 7) // 8  # bytes per row
    total_bytes = width_bytes * target_dots
    hex_data = []
    pixels = img.load()
    for y in range(target_dots):
        row_bytes = []
        for bx in range(width_bytes):
            byte_val = 0
            for bit in range(8):
                x = bx * 8 + bit
                if x < target_dots:
                    # In PIL mode "1": 0=black, 255=white. ZPL: 1=black, 0=white.
                    if pixels[x, y] == 0:
                        byte_val |= (0x80 >> bit)
            row_bytes.append(f"{byte_val:02X}")
        hex_data.append("".join(row_bytes))

    hex_str = "".join(hex_data)
    return f"^GFA,{total_bytes},{total_bytes},{width_bytes},{hex_str}^FS"


def build_zpl(attendee: dict) -> str:
    """Build a complete ZPL II command string for one badge.

    Label physical dimensions: 101.6 mm wide x 152.4 mm tall (4" x 6").
    At 203 DPI: 812 dots wide x 1219 dots tall.

    The top 57 mm (456 dots) is a pre-printed header — do NOT print there.
    Printable zone: Y 456..1219, height = 763 dots.

    Layout order (all fields horizontally centered via ^FB), mirroring the
    badge design in printing.md:
      1. Name      — dominant, largest font
      2. Company   — medium font (auto-wraps up to 2 lines)
      3. Category  — small font, uppercase
      4. QR code   — large square, centered

    Horizontal centering uses ZPL Field Block (^FB) with center justification
    spanning the full label width, so text is truly centered regardless of
    string length — no character-width guessing.

    ``attendee`` expected fields (already sanitized):
        - 'category'      : str
        - 'name_line1'    : str
        - 'company_line1' : str
        - 'company_line2' : str  ('' if not used)
        - 'code'          : str
    """
    # ── Geometry (dots @ 203 dpi) ─────────────────────────────────────────────
    LABEL_W        = 812   # 101.6 mm
    LABEL_H        = 1219  # 152.4 mm
    HEADER_H       = 456   #  57.0 mm — pre-printed, must stay empty
    ZONE_TOP       = HEADER_H
    ZONE_H         = LABEL_H - HEADER_H          # 763 dots usable
    SIDE_MARGIN    = 20    # left/right text inset
    MARGIN_TOP     = 24
    MARGIN_BOTTOM  = 24
    GAP_NAME_COMP  = 14
    GAP_COMP_CAT   = 16
    GAP_CAT_QR     = 30

    # ^FB block width = label minus left+right margins; text centers within it.
    FB_W = LABEL_W - (2 * SIDE_MARGIN)

    # ── Field data ───────────────────────────────────────────────────────────
    category      = _zpl_escape(attendee.get("category", ""))
    name_line1    = _zpl_escape(attendee.get("name_line1", ""))
    company_line1 = _zpl_escape(attendee.get("company_line1", ""))
    company_line2_raw = attendee.get("company_line2", "")
    company_line2 = _zpl_escape(company_line2_raw)
    qr            = _qr_payload(attendee.get("code", ""))
    has_company2  = bool(company_line2_raw)

    # ── Smart font sizing (^A0N,h,w; h==w keeps glyphs square) ────────────────
    # Name dominates the badge; shrinks only for long strings so it still fits
    # the block width on a single line.
    def _name_font(text: str) -> int:
        n = len(text)
        if n <= 8:   return 120
        if n <= 12:  return 100
        if n <= 16:  return 84
        if n <= 20:  return 70
        return 58

    def _company_font(text: str) -> int:
        n = len(text)
        if n <= 12:  return 56
        if n <= 18:  return 48
        if n <= 26:  return 42
        return 36

    def _category_font(text: str) -> int:
        n = len(text)
        if n <= 12:  return 40
        if n <= 22:  return 34
        return 30

    name_fs = _name_font(name_line1)
    comp_fs = _company_font(company_line1)
    cat_fs  = _category_font(category)

    # ── QR size — large, ~37 % of label width ────────────────────────────────
    QR_SIZE = min(300, int(LABEL_W * 0.37))      # 300 dots ≈ 37.6 mm

    # ── Vertical layout — stack rows, then center the block in the zone ───────
    rows_h = (
        name_fs
        + GAP_NAME_COMP + comp_fs
        + (comp_fs if has_company2 else 0)        # second company line
        + GAP_COMP_CAT  + cat_fs
        + GAP_CAT_QR    + QR_SIZE
    )
    zone_usable = ZONE_H - MARGIN_TOP - MARGIN_BOTTOM
    v_start     = ZONE_TOP + MARGIN_TOP + max(0, (zone_usable - rows_h) // 2)

    y_name  = v_start
    y_comp  = y_name + name_fs + GAP_NAME_COMP
    comp_lines_h = comp_fs * (2 if has_company2 else 1)
    y_cat   = y_comp + comp_lines_h + GAP_COMP_CAT
    y_qr    = y_cat  + cat_fs + GAP_CAT_QR

    # Safety clamp so the QR never bleeds off the bottom edge
    y_qr = min(y_qr, LABEL_H - MARGIN_BOTTOM - QR_SIZE)

    qr_x = (LABEL_W - QR_SIZE) // 2

    # ── Field Block helper: centers text within FB_W starting at SIDE_MARGIN ──
    def _centered(text: str, fs: int, y: int, max_lines: int = 1) -> str:
        # ^FB<width>,<max_lines>,<line_spacing>,<justify=C>,<indent>
        return (
            f"^A0N,{fs},{fs}"
            f"^FO{SIDE_MARGIN},{y}"
            f"^FB{FB_W},{max_lines},0,C,0"
            f"^FD{text}^FS"
        )

    # ── Assemble ZPL ─────────────────────────────────────────────────────────
    parts = [
        "^XA",
        "^PON",
        f"^PW{LABEL_W}",
        f"^LL{LABEL_H}",
        "^LH0,0",
        # 1. Name — dominant, centered
        _centered(name_line1, name_fs, y_name, max_lines=1),
    ]

    # 2. Company — one ^FB block; if a 2nd line exists, allow wrapping/2 lines.
    if has_company2:
        company_text = f"{company_line1}\\&{company_line2}"  # \& = line break in ^FB
        parts.append(_centered(company_text, comp_fs, y_comp, max_lines=2))
    else:
        parts.append(_centered(company_line1, comp_fs, y_comp, max_lines=1))

    # 3. Category — centered
    parts.append(_centered(category, cat_fs, y_cat, max_lines=1))

    # 4. QR code — large, centered
    qr_gfa = _qr_to_gfa(qr, target_dots=QR_SIZE)
    parts.append(f"^FO{qr_x},{y_qr}{qr_gfa}")

    parts.append("^PQ1")
    parts.append("^XZ")
    return "".join(parts)


def _contains_any_keyword(text: str, keywords: list[str]) -> bool:
    text = (text or "").lower()
    return any(str(keyword).lower() in text for keyword in keywords)


def _extract_usb_serial_from_pnp_id(pnp_id: str) -> str:
    parts = (pnp_id or "").split("\\")
    return parts[-1] if len(parts) >= 3 else ""


# Windows printer status / attribute bit flags. A duplicate driver copy left on
# a dead USB port is normally flagged "work offline", so it must not be
# auto-selected even though it still appears as an installed queue.
PRINTER_STATUS_OFFLINE = 0x00000080
PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x00000400


def _printer_is_online(status: int, attributes: int) -> bool:
    """Return True when Windows reports the queue as usable right now."""
    if int(status or 0) & PRINTER_STATUS_OFFLINE:
        return False
    if int(attributes or 0) & PRINTER_ATTRIBUTE_WORK_OFFLINE:
        return False
    return True


def _to_int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _printer_states_by_name() -> dict:
    """Map queue name -> live readiness info from WMI Win32_Printer.

    win32print's GetPrinter ``Status`` is frequently 0 for USB Zebra queues even
    when the underlying device is gone or the queue is jammed, so a stale
    duplicate queue looks identical to the live one. WMI exposes the richer
    driver state (WorkOffline / PrinterStatus / PrinterState / DetectedErrorState)
    that does distinguish a genuinely-ready queue from a dead/jammed duplicate.

    Returns an empty dict when WMI is unavailable, in which case callers fall
    back to the attribute-based ``online`` flag and behave exactly as before.
    """
    if not WMI_AVAILABLE:
        return {}
    try:
        printers = wmi.WMI().Win32_Printer()
    except Exception:
        return {}

    states = {}
    for p in printers:
        name = str(getattr(p, "Name", "") or "")
        if not name:
            continue
        try:
            work_offline = bool(getattr(p, "WorkOffline", False))
        except Exception:
            work_offline = False
        printer_status = _to_int_or_none(getattr(p, "PrinterStatus", None))
        printer_state = _to_int_or_none(getattr(p, "PrinterState", None))
        detected_error = _to_int_or_none(getattr(p, "DetectedErrorState", None))
        # Ready = not offline, no problem state, and idle/printing. ``None`` is
        # treated as "no objection" so drivers that omit a field aren't excluded.
        ready = (
            not work_offline
            and printer_state in (0, None)
            and printer_status in (3, 4, None)
            and detected_error in (0, 2, None)
        )
        states[name] = {
            "work_offline": work_offline,
            "printer_status": printer_status,
            "printer_state": printer_state,
            "detected_error_state": detected_error,
            "ready": ready,
        }
    return states


def _list_installed_printers() -> list[dict]:
    if not WIN32PRINT_AVAILABLE:
        return []

    printers = win32print.EnumPrinters(
        win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    )
    result = []
    for printer in printers:
        queue_name = printer[2]
        info = {"name": queue_name, "driver": "", "port": "", "status": 0, "attributes": 0, "online": True, "ready": True}
        try:
            hprinter = win32print.OpenPrinter(queue_name)
            try:
                printer_info = win32print.GetPrinter(hprinter, 2)
                status = printer_info.get("Status", 0) or 0
                attributes = printer_info.get("Attributes", 0) or 0
                online = _printer_is_online(status, attributes)
                info = {
                    "name": printer_info.get("pPrinterName") or queue_name,
                    "driver": printer_info.get("pDriverName") or "",
                    "port": printer_info.get("pPortName") or "",
                    "status": status,
                    "attributes": attributes,
                    "online": online,
                    "ready": online,
                }
            finally:
                win32print.ClosePrinter(hprinter)
        except Exception:
            pass
        result.append(info)

    # Enrich with WMI readiness so a stale/jammed duplicate queue (which
    # win32print still reports as online) is correctly ranked below the live one.
    states = _printer_states_by_name()
    if states:
        for info in result:
            st = states.get(info["name"])
            if not st:
                continue
            if st["work_offline"]:
                info["online"] = False
            info["printer_status"] = st["printer_status"]
            info["printer_state"] = st["printer_state"]
            info["detected_error_state"] = st["detected_error_state"]
            info["ready"] = bool(st["ready"]) and info.get("online", True)
    return result


def _list_usb_pnp_devices() -> list[dict]:
    if not WMI_AVAILABLE:
        return []

    devices = []
    try:
        pnp_entities = wmi.WMI().Win32_PnPEntity()
    except Exception:
        return []

    for device in pnp_entities:
        name = str(device.Name or "")
        pnp_id = str(device.PNPDeviceID or "")
        search_text = f"{name} {pnp_id}"
        if "USB" not in pnp_id.upper():
            continue
        if not (
            "printer" in name.lower()
            or "zebra" in name.lower()
            or "zdesigner" in name.lower()
            or _contains_any_keyword(search_text, ZEBRA_MODEL_KEYWORDS)
        ):
            continue
        devices.append({
            "name": name,
            "pnp_id": pnp_id,
            "serial": _extract_usb_serial_from_pnp_id(pnp_id),
        })
    return devices


def _is_zebra_printer_record(record: dict) -> bool:
    text = f"{record.get('name', '')} {record.get('driver', '')} {record.get('port', '')}"
    return _contains_any_keyword(text, ZEBRA_MODEL_KEYWORDS)


def _configured_serials() -> list[str]:
    serials = []
    for value in [ZEBRA_USB_SERIAL, *ZEBRA_USB_SERIALS]:
        value = str(value or "").strip()
        if value and value not in serials and value != "AUTO_FILL_AFTER_SCAN":
            serials.append(value)
    for item in ZEBRA_PRINTERS:
        if isinstance(item, dict):
            value = str(item.get("serial", "")).strip()
            if value and value not in serials and value != "AUTO_FILL_AFTER_SCAN":
                serials.append(value)
    return serials


def _zebra_usb_devices_only() -> list[dict]:
    result = []
    for device in _list_usb_pnp_devices():
        text = f"{device.get('name', '')} {device.get('pnp_id', '')}"
        if _contains_any_keyword(text, ZEBRA_MODEL_KEYWORDS):
            result.append(device)
    return result


def discover_zebra_usb_serials() -> list[str]:
    serials = []
    for device in _zebra_usb_devices_only():
        serial = str(device.get("serial", "")).strip()
        if serial and serial not in serials:
            serials.append(serial)
    return serials


def _merge_serials_into_config_file(config_path: str = "printer_config.json") -> tuple[bool, str, dict]:
    detected_serials = discover_zebra_usb_serials()
    if not detected_serials:
        return False, "No Zebra USB serials detected.", {"usb_devices": _list_usb_pnp_devices()}

    try:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
        except json.JSONDecodeError as e:
            return False, f"{config_path} is invalid JSON: {e}", {}

        existing_printers = data.get("ZEBRA_PRINTERS", [])
        existing_by_serial = {
            str(p.get("serial", "")).strip(): p
            for p in existing_printers
            if isinstance(p, dict) and str(p.get("serial", "")).strip()
        }

        new_printers = []
        for index, serial in enumerate(detected_serials, start=1):
            existing = existing_by_serial.get(serial, {})
            new_printers.append({
                "label": existing.get("label") or f"PRINTER_{index}",
                "serial": serial,
                "queue": existing.get("queue", ""),
            })

        data["ZEBRA_USB_SERIALS"] = detected_serials
        data["ZEBRA_USB_SERIAL"] = detected_serials[0]
        data["ZEBRA_PRINTERS"] = new_printers
        data.setdefault("ZEBRA_MODEL_KEYWORDS", ZEBRA_MODEL_KEYWORDS)
        data.setdefault("ALLOW_PRINTER_QUEUE_FALLBACK", False)
        data.setdefault("USE_TEST_MODE", USE_TEST_MODE)

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

        return True, f"Saved {len(detected_serials)} Zebra USB serial(s) to {config_path}.", data
    except OSError as e:
        return False, f"Could not write {config_path}: {e}", {}


def find_zebra_usb_printer_queue(target_label: str = "", target_serial: str = "") -> tuple[bool, str, dict | None]:
    if not WIN32PRINT_AVAILABLE:
        return False, "pywin32 (win32print) is not installed. Install with: pip install pywin32", None

    installed = _list_installed_printers()
    zebra_queues = [p for p in installed if _is_zebra_printer_record(p)]
    non_zebra_printers = [p for p in installed if not _is_zebra_printer_record(p)]
    usb_devices = _zebra_usb_devices_only()

    target_label = str(target_label or "").strip()
    target_serial = str(target_serial or "").strip()
    
    # First: check if target_label directly matches an installed printer queue name (for any printer type)
    if target_label:
        for printer in installed:
            if printer["name"].lower() == target_label.lower() or printer["name"] == target_label:
                return True, printer["name"], {"queue": printer, "mode": "direct_queue_match"}
    
    # Second: check configured printers
    for item in ZEBRA_PRINTERS:
        if not isinstance(item, dict):
            continue
        item_label = str(item.get("label", "")).strip()
        item_serial = str(item.get("serial", "")).strip()
        item_queue = str(item.get("queue", "")).strip()
        label_match = target_label and item_label.lower() == target_label.lower()
        serial_match = target_serial and item_serial.lower() == target_serial.lower()
        if (label_match or serial_match) and item_queue:
            if not any(q["name"] == item_queue for q in zebra_queues):
                return False, f"Configured Zebra queue '{item_queue}' is not installed in Windows.", {"zebra_queues": zebra_queues}
            return True, item_queue, {"queue": next(q for q in zebra_queues if q["name"] == item_queue), "config": item}

    configured_serials = _configured_serials()
    if target_serial:
        configured_serials = [target_serial]
    elif target_label:
        labelled = [
            str(p.get("serial", "")).strip()
            for p in ZEBRA_PRINTERS
            if isinstance(p, dict) and str(p.get("label", "")).strip().lower() == target_label.lower()
        ]
        configured_serials = [x for x in labelled if x and x != "AUTO_FILL_AFTER_SCAN"] or configured_serials

    matching_usb_devices = []
    for serial in configured_serials:
        matches = [
            d for d in usb_devices
            if serial.lower() in f"{d.get('serial', '')} {d.get('pnp_id', '')}".lower()
        ]
        matching_usb_devices.extend(matches)

    if configured_serials and not matching_usb_devices:
        return False, (
            f"Configured Zebra USB serial(s) {configured_serials} were not found. "
            "Open /api/printers/debug or POST /api/printers/autofill to refresh serials."
        ), {"configured_serials": configured_serials, "installed_printers": installed, "usb_devices": usb_devices}

    if not zebra_queues:
        if non_zebra_printers:
            return False, (
                "No Zebra/ZDesigner printer queue was detected. Other printer brands were detected, "
                "but this app is locked to Zebra only and will not print to them."
            ), {"non_zebra_printers": non_zebra_printers, "usb_devices": usb_devices}
        return False, "No installed Zebra printer queues were detected.", {"installed_printers": installed, "usb_devices": usb_devices}

    # Rank queues by how usable Windows says they are. A stale duplicate copy on
    # a dead/jammed USB port often still reports as "online" via win32print, so
    # we additionally prefer queues WMI reports as fully ready (idle, no error,
    # no jam). Tiers: ready  >  online  >  anything installed.
    ready_zebra_queues = [q for q in zebra_queues if q.get("ready")]
    online_zebra_queues = [q for q in zebra_queues if q.get("online", True)]
    effective_queues = ready_zebra_queues or online_zebra_queues or zebra_queues

    if len(effective_queues) == 1:
        if ready_zebra_queues:
            mode = "single_ready_zebra_queue"
        elif online_zebra_queues:
            mode = "single_online_zebra_queue"
        else:
            mode = "single_zebra_queue"
        return True, effective_queues[0]["name"], {
            "queue": effective_queues[0],
            "matched_usb_devices": matching_usb_devices,
            "mode": mode,
        }

    # Try to disambiguate multiple candidate queues using live USB model overlap.
    for usb_device in matching_usb_devices:
        usb_name = usb_device.get("name", "").lower()
        tokens = [t for t in re.split(r"[^a-zA-Z0-9]+", usb_name) if len(t) >= 3]
        candidates = []
        for queue in effective_queues:
            queue_text = f"{queue.get('name', '')} {queue.get('driver', '')}".lower()
            if any(token in queue_text for token in tokens):
                candidates.append(queue)
        if len(candidates) == 1:
            return True, candidates[0]["name"], {"queue": candidates[0], "usb_device": usb_device, "mode": "model_overlap"}

    # Prefer the configured default queue when it is one of the candidates.
    configured_default = (PRINTER_QUEUE or "").strip().lower()
    if configured_default:
        for queue in effective_queues:
            if queue["name"].strip().lower() == configured_default:
                return True, queue["name"], {"queue": queue, "mode": "configured_default_online"}

    # Last resort: auto-detect picks the first candidate queue (ready ones first)
    # so the common single-printer case "just works". The choice is reported in
    # metadata and the browser dropdown lets the operator override it.
    chosen = effective_queues[0]
    return True, chosen["name"], {
        "queue": chosen,
        "alternatives": [q["name"] for q in effective_queues],
        "not_ready_or_offline": [q["name"] for q in zebra_queues if not q.get("ready", q.get("online", True))],
        "mode": "auto_first_ready" if ready_zebra_queues else "auto_first_online",
        "note": (
            "Multiple usable Zebra queues were detected; auto-selected the first. "
            "Pick a specific printer in the UI, or remove the duplicate/offline "
            "queues in Windows, for a deterministic choice."
        ),
    }


def _mm_to_px(mm: float, dpi: int = 203) -> int:
    """Convert millimetres to whole pixels at the given DPI."""
    return round(mm / 25.4 * dpi)


_WIN_FONTS = r"C:\Windows\Fonts"


def _load_font(bold: bool, size: int):
    """Load Arial (falling back to DejaVu / PIL default) at the given size.

    Ported verbatim from make_label_image.py so the printed badge matches the
    reference sample.
    """
    size = max(6, int(size))
    names = (["arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["arial.ttf", "DejaVuSans.ttf"])
    for name in names:
        for path in (os.path.join(_WIN_FONTS, name), name):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _measure(draw, text, font):
    """Return (width, height, x0, y0) of the text's tight bounding box."""
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font, anchor="la")
    return x1 - x0, y1 - y0, x0, y0


def _fit_font_to_width(draw, text, bold, size, max_w, min_size=8):
    """Largest font <= ``size`` whose rendered width fits ``max_w``."""
    size = max(min_size, int(size))
    while size > min_size:
        font = _load_font(bold, size)
        w, _, _, _ = _measure(draw, text, font)
        if w <= max_w:
            return font
        size -= 2
    return _load_font(bold, min_size)


def _make_qr_image(data: str, size_px: int) -> Image.Image:
    """Render ``data`` as a square QR image, black on white, with quiet zone."""
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=2,  # quiet zone for reliable scanning
    )
    qr.add_data(data or "NO-CODE")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((size_px, size_px), Image.NEAREST)


def render_badge_print_area(content: dict, dpi: int = 203) -> Image.Image:
    """Render the badge PRINT AREA image (101.6 mm x 95.4 mm) at ``dpi``.

    This is a faithful port of make_label_image.py's layout: each line is
    shrunk to fit the usable width, the whole stack is scaled down if it would
    exceed the print area, and the block is vertically centered inside it.

    Order (top -> bottom, horizontally centered):
        1. Name      (largest, bold)
        2. Company
        3. Category  (bold)
        4. QR code   (centered square)
        5. Eventcat

    Empty fields are skipped so they leave no phantom gap. The returned image
    is exactly the print area (no header band); _image_to_gfa() positions it
    57 mm below the top of the panel.
    """
    WIDTH_MM        = 101.6
    PRINT_AREA_MM   = 95.4
    SIDE_MARGIN_MM  = 4.0
    TOP_MARGIN_MM   = 3.0
    BOTTOM_MARGIN_MM = 3.0

    width_px = _mm_to_px(WIDTH_MM, dpi)
    print_px = _mm_to_px(PRINT_AREA_MM, dpi)
    side_m   = _mm_to_px(SIDE_MARGIN_MM, dpi)
    top_m    = _mm_to_px(TOP_MARGIN_MM, dpi)
    bot_m    = _mm_to_px(BOTTOM_MARGIN_MM, dpi)
    usable_w = width_px - 2 * side_m
    usable_h = print_px - top_m - bot_m

    img = Image.new("RGB", (width_px, print_px), "white")
    draw = ImageDraw.Draw(img)

    name     = (content.get("name") or "").strip()
    company  = (content.get("company") or "").strip()
    category = (content.get("category") or "").strip()
    eventcat = (content.get("eventcat") or "").strip()
    qr_data  = content.get("qr_data") or "NO-CODE"

    # spec row: (kind, data, bold, base_size_px, gap_before_px)
    spec = []
    if name:
        spec.append(("text", name, True, 110, 0))
    if company:
        spec.append(("text", company, False, 56, 16))
    if category:
        spec.append(("text", category, True, 40, 16))
    spec.append(("qr", qr_data, False, 300, 28))
    if eventcat:
        spec.append(("text", eventcat, False, 40, 22))

    def build_plan(scale):
        plan, total = [], 0.0
        for kind, data, bold, base, gap in spec:
            g = gap * scale
            if kind == "text":
                font = _fit_font_to_width(draw, data, bold, base * scale, usable_w)
                w, h, x0, y0 = _measure(draw, data, font)
                plan.append(("text", data, font, w, h, x0, y0, g))
                total += g + h
            else:
                qs = max(48, int(min(base * scale, usable_w)))
                plan.append(("qr", data, None, qs, qs, 0, 0, g))
                total += g + qs
        return plan, total

    plan, total = build_plan(1.0)
    scale = 1.0
    if total > usable_h:
        lo, hi = 0.1, 1.0
        for _ in range(22):
            mid = (lo + hi) / 2
            p, t = build_plan(mid)
            if t <= usable_h:
                plan, total, scale = p, t, mid
                lo = mid
            else:
                hi = mid

    # Vertically center the block inside the print area.
    cx = width_px / 2
    y = top_m + max(0.0, (usable_h - total) / 2)
    for item in plan:
        kind = item[0]
        gap = item[-1]
        y += gap
        if kind == "text":
            _, data, font, w, h, x0, y0, _ = item
            draw.text((cx - w / 2 - x0, y - y0), data,
                      font=font, fill="#111111", anchor="la")
            y += h
        else:  # qr
            _, data, _, qs, _, _, _, _ = item
            qr_img = _make_qr_image(data, qs)
            img.paste(qr_img, (int(cx - qs / 2), int(round(y))))
            y += qs

    return img


def build_zpl_from_attendee(content: dict) -> str:
    """Render the badge image server-side (matching make_label_image.py) and
    convert it to a positioned ZPL ^GFA job placed below the 57 mm header."""
    return _image_to_gfa(render_badge_print_area(content))


def _image_to_gfa(image: Image.Image) -> str:
    # Folding badge — printed one FRONT PANEL at a time.
    # Each panel (gap/diecut to gap/diecut) is 101.6 mm x 152.4 mm.
    # At 203 dpi: 812 x 1219 dots.
    #
    #   0 .. 57 mm   (0 .. 456 dots)   -> pre-printed green header (leave blank)
    #   57 .. 152.4  (456 .. 1219)     -> WHITE rectangle = printable area
    #                                     height 95.4 mm = 763 dots
    LABEL_W       = 812    # 101.6 mm  (^PW)
    LABEL_LEN     = 1219   # 152.4 mm  (^LL) — one front panel
    HEADER_OFFSET = 456    #  57.0 mm  — first writable row (below header)
    CONTENT_W     = 812    # 101.6 mm  — printable width
    CONTENT_H     = LABEL_LEN - HEADER_OFFSET  # 763 dots = 95.4 mm printable height

    # Fit the badge image INSIDE the 812 x 763 printable rectangle while
    # preserving its aspect ratio, then center it on a white canvas of exactly
    # that size. This prevents the vertical/horizontal stretching that a hard
    # resize() caused (e.g. a squashed/elongated QR code).
    src = image.convert("L")
    fitted = src.copy()
    fitted.thumbnail((CONTENT_W, CONTENT_H), Image.LANCZOS)  # scale-to-fit, no stretch
    canvas = Image.new("L", (CONTENT_W, CONTENT_H), 255)     # white background
    paste_x = (CONTENT_W - fitted.width) // 2
    paste_y = (CONTENT_H - fitted.height) // 2
    canvas.paste(fitted, (paste_x, paste_y))

    image = canvas.point(lambda px: 0 if px < 160 else 255, "1")
    width, height = image.size
    width_bytes = (width + 7) // 8
    total_bytes = width_bytes * height
    pixels = image.load()
    hex_rows = []
    for y in range(height):
        row = []
        for bx in range(width_bytes):
            byte_val = 0
            for bit in range(8):
                x = bx * 8 + bit
                if x < width and pixels[x, y] == 0:
                    byte_val |= 0x80 >> bit
            row.append(f"{byte_val:02X}")
        hex_rows.append("".join(row))
    return (
        f"^XA^POI^PW{LABEL_W}^LL{LABEL_LEN}^LH0,0"
        f"^FO0,{HEADER_OFFSET}^GFA,{total_bytes},{total_bytes},{width_bytes},{''.join(hex_rows)}^FS"
        f"^PQ1^XZ"
    )


def build_zpl_from_badge_image(data_url: str) -> str:
    prefix = "data:image/png;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        raise ValueError("badge_image must be a PNG data URL.")
    try:
        raw = base64.b64decode(data_url[len(prefix):], validate=True)
        image = Image.open(io.BytesIO(raw))
    except (binascii.Error, OSError) as e:
        raise ValueError(f"Invalid badge_image payload: {e}") from e
    return _image_to_gfa(image)


def send_zpl_to_printer(zpl: str, target_label: str = "", target_serial: str = "") -> tuple[bool, str]:
    """Submit a ZPL II command to the local Windows printer queue.

    In test mode (``USE_TEST_MODE`` is True) the bytes are written to
    ``test_print_command.prn`` in the current working directory and no print
    job is dispatched. In normal mode the bytes are submitted as a RAW print
    job to the queue named by ``PRINTER_QUEUE`` via ``win32print``; the
    spooler is responsible for forwarding them verbatim to the USB printer.

    The function MUST NOT raise. Every Win32 / OS error is caught and
    converted into the ``(False, message)`` form so callers can translate
    it directly into an HTTP 500 response.

    Returns:
        (True,  success_message) on success, or
        (False, error_message)   on any failure.

    Per spec zpl-printer-migration task 5.2
    (requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6).
    """
    if USE_TEST_MODE:
        try:
            with open("test_print_command.prn", "wb") as f:
                f.write(zpl.encode('ascii', errors='ignore'))
            return True, "Test mode: Print command saved to file."
        except OSError as e:
            return False, f"Test mode error: {e}"

    if not WIN32PRINT_AVAILABLE:
        return False, (
            "pywin32 (win32print) is not installed; cannot submit print job. "
            "Install with: pip install pywin32"
        )

    try:
        raw_bytes = zpl.encode('ascii', errors='ignore')
        detected, queue_or_error, metadata = find_zebra_usb_printer_queue(
            target_label=target_label,
            target_serial=target_serial,
        )
        if not detected:
            if ALLOW_PRINTER_QUEUE_FALLBACK and PRINTER_QUEUE:
                fallback_record = {"name": PRINTER_QUEUE, "driver": PRINTER_QUEUE, "port": ""}
                if not _is_zebra_printer_record(fallback_record):
                    return False, f"Refusing to use fallback printer '{PRINTER_QUEUE}' because it does not look like a Zebra queue."
                queue_or_error = PRINTER_QUEUE
            else:
                return False, queue_or_error

        hPrinter = win32print.OpenPrinter(queue_or_error)
        try:
            job_info = ("Badge Print Job", None, "RAW")
            win32print.StartDocPrinter(hPrinter, 1, job_info)
            try:
                win32print.StartPagePrinter(hPrinter)
                win32print.WritePrinter(hPrinter, raw_bytes)
                win32print.EndPagePrinter(hPrinter)
            finally:
                win32print.EndDocPrinter(hPrinter)
        finally:
            win32print.ClosePrinter(hPrinter)
        return True, f"Print command sent successfully to Zebra queue: {queue_or_error}"
    except Exception as e:
        return False, f"Printer error: {e}"


@app.route('/api/attendee', methods=['GET'])
def attendee_lookup():
    """Proxy attendee lookup so the external API key stays server-side."""
    if not EXTERNAL_API_KEY:
        return jsonify({"success": False, "error": "External API key is not configured."}), 500

    param_type = request.args.get("type", "")
    param_value = request.args.get("value", "")
    if param_type not in {"idreg", "idma"} or not re.fullmatch(r"\d{1,30}", param_value):
        return jsonify({"success": False, "error": "Invalid attendee lookup parameters."}), 400

    cached_attendee = _get_cached_attendee(param_type, param_value)
    if cached_attendee:
        return jsonify({"success": True, "attendee": cached_attendee, "cached": True})

    query = urllib.parse.urlencode({param_type: param_value})
    url = f"{EXTERNAL_API_BASE_URL}?{query}"
    req = urllib.request.Request(url, headers={"X-API-KEY": EXTERNAL_API_KEY}, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = response.read().decode("utf-8")
            data = json.loads(payload)
    except urllib.error.HTTPError as e:
        status_code = 404 if e.code == 404 else 502
        return jsonify({"success": False, "error": f"Ticket API returned HTTP {e.code}."}), status_code
    except (urllib.error.URLError, TimeoutError) as e:
        return jsonify({"success": False, "error": f"Ticket API request failed: {e}"}), 502
    except json.JSONDecodeError:
        return jsonify({"success": False, "error": "Ticket API returned invalid JSON."}), 502

    attendee = {
        "name": data.get("full_name") or data.get("name") or "Unknown Attendee",
        "jobTitle": data.get("job_title") or data.get("title") or "Attendee",
        "category": data.get("category") or "Delegate",
        "code": param_value,
        "email": data.get("email") or data.get("personal_email") or data.get("email_attendee") or "",
        "company": data.get("company_name") or "",
        "eventcat": data.get("eventcat") or "N/A",
    }
    _set_cached_attendee(param_type, param_value, attendee)
    return jsonify({"success": True, "attendee": attendee})


# This is the API endpoint that index.html will call
@app.route('/api/print', methods=['POST'])
def print_badge():
    """Build and submit one ZPL badge print job."""
    if not _print_request_authorized():
        return jsonify({"success": False, "error": "Unauthorized print request."}), 401

    try:
        data = request.get_json()
        if not data or 'attendee' not in data:
            return jsonify({"success": False, "error": "Invalid data format."}), 400

        attendee = data['attendee']
        name = attendee.get('name', 'N/A')
        category = attendee.get('category', 'Delegate').upper()
        code = attendee.get('code', 'NO-CODE')
        company = attendee.get('company', 'N/A')
        eventcat = attendee.get('eventcat', '') or ''

        print(f"Received print request for: {name}")

        # Sanitize: strip every byte outside ASCII (Req 5.1)
        name     = name.encode('ascii', errors='ignore').decode('ascii')
        category = category.encode('ascii', errors='ignore').decode('ascii')
        code     = code.encode('ascii', errors='ignore').decode('ascii')
        company  = company.encode('ascii', errors='ignore').decode('ascii')
        eventcat = eventcat.encode('ascii', errors='ignore').decode('ascii')

        # Apply existing helpers (Req 3.7, 3.3, 3.4)
        name_line1 = derive_name_line1(name)
        company_line1, company_line2 = split_company_lines(company)

        prepared = {
            "category":      category,
            "name_line1":    name_line1,
            "company_line1": company_line1,
            "company_line2": company_line2,
            "code":          code,
        }

        print_mode = str(data.get("print_mode", "zpl") or "zpl").strip().lower()

        # Build ZPL command (Req 1.7: any builder error -> HTTP 500)
        try:
            if print_mode == "image":
                # Render the badge image server-side (faithful port of
                # make_label_image.py) so the printout matches the reference
                # sample exactly and does not depend on browser fonts/canvas.
                zpl = build_zpl_from_attendee({
                    "name":     name,
                    "company":  company,
                    "category": category,
                    "qr_data":  code,
                    "eventcat": eventcat,
                })
            else:
                zpl = build_zpl(prepared)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

        printer_label = str(data.get('printer_label', '') or '').strip()
        printer_serial = str(data.get('printer_serial', '') or '').strip()

        # Submit (Req 6.1, 6.4, 6.6)
        success, message = send_zpl_to_printer(
            zpl,
            target_label=printer_label,
            target_serial=printer_serial,
        )
        if success:
            return jsonify({"success": True, "message": message})
        else:
            return jsonify({"success": False, "error": message}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/printers', methods=['GET'])
def printers_list():
    """List selectable Zebra printers for the browser dropdown.

    Merges any labelled ``ZEBRA_PRINTERS`` config entries with every Zebra
    queue currently installed in Windows, and reports each queue's online
    state so the operator can avoid stale/offline duplicates. The returned
    ``queue`` value can be sent straight back as ``printer_label`` to
    ``/api/print`` (it direct-matches an installed queue name)."""
    if not _print_request_authorized():
        return jsonify({"success": False, "error": "Unauthorized request."}), 401

    installed = _list_installed_printers()
    installed_zebra = [p for p in installed if _is_zebra_printer_record(p)]
    by_queue = {p["name"]: p for p in installed_zebra}

    printers = []
    seen_queues = set()

    # Configured printers first so their human-friendly labels take precedence.
    for item in ZEBRA_PRINTERS:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        queue = str(item.get("queue", "")).strip()
        if not label:
            continue
        match = by_queue.get(queue)
        printers.append({
            "label": label,
            "queue": queue,
            "installed": match is not None,
            "online": bool(match.get("online")) if match else False,
            "ready": bool(match.get("ready")) if match else False,
            "serial_set": bool(str(item.get("serial", "")).strip() and str(item.get("serial", "")).strip() != "AUTO_FILL_AFTER_SCAN"),
            "queue_set": bool(queue),
            "source": "configured",
        })
        if queue:
            seen_queues.add(queue)

    # Then every installed Zebra queue not already covered by config.
    for p in installed_zebra:
        if p["name"] in seen_queues:
            continue
        printers.append({
            "label": p["name"],
            "queue": p["name"],
            "installed": True,
            "online": bool(p.get("online")),
            "ready": bool(p.get("ready")),
            "serial_set": False,
            "queue_set": True,
            "source": "detected",
        })
        seen_queues.add(p["name"])

    return jsonify({"success": True, "printers": printers})


@app.route('/api/printers/debug', methods=['GET'])
def printers_debug():
    """Show installed Windows printers and USB/PnP IDs for Zebra setup."""
    if not _print_request_authorized():
        return jsonify({"success": False, "error": "Unauthorized request."}), 401

    target_label = str(request.args.get("printer_label", "") or "").strip()
    target_serial = str(request.args.get("printer_serial", "") or "").strip()
    detected, queue_or_error, metadata = find_zebra_usb_printer_queue(
        target_label=target_label,
        target_serial=target_serial,
    ) if WIN32PRINT_AVAILABLE else (False, "win32print unavailable", None)
    
    # Get all installed printers
    all_printers = _list_installed_printers()
    
    # Auto-generate printer list from all installed printers (excluding PDF/OneNote)
    auto_printers = []
    for printer in all_printers:
        name = printer['name']
        # Skip virtual printers
        if any(skip in name.lower() for skip in ['pdf', 'onenote', 'xps', 'fax']):
            continue
        auto_printers.append({
            "label": name,
            "queue": name
        })
    
    # Merge configured printers with auto-detected ones
    merged_printers = list(ZEBRA_PRINTERS)
    configured_queues = {p.get('queue', '') for p in ZEBRA_PRINTERS if isinstance(p, dict)}
    for auto_p in auto_printers:
        if auto_p['queue'] not in configured_queues:
            merged_printers.append(auto_p)
    
    return jsonify({
        "success": True,
        "detected": detected,
        "selected_queue": queue_or_error if detected else None,
        "error": None if detected else queue_or_error,
        "configured_zebra_usb_serial": ZEBRA_USB_SERIAL,
        "configured_zebra_usb_serials": _configured_serials(),
        "discovered_zebra_usb_serials": discover_zebra_usb_serials(),
        "configured_zebra_printers": merged_printers,
        "installed_printers": all_printers,
        "usb_devices": _list_usb_pnp_devices(),
        "zebra_usb_devices": _zebra_usb_devices_only(),
        "metadata": metadata,
    })


@app.route('/api/printers/autofill', methods=['POST'])
def printers_autofill():
    """Auto-fill printer_config.json with all detected Zebra USB serials."""
    if not _print_request_authorized():
        return jsonify({"success": False, "error": "Unauthorized request."}), 401

    ok, message, data = _merge_serials_into_config_file()
    status_code = 200 if ok else 500
    return jsonify({
        "success": ok,
        "message" if ok else "error": message,
        "config": data if ok else None,
        "discovered_zebra_usb_serials": discover_zebra_usb_serials(),
        "zebra_usb_devices": _zebra_usb_devices_only(),
        "note": "Restart the Flask app after autofill so the updated config is loaded." if ok else None,
    }), status_code

# Test endpoint to verify the server is running and configuration is loaded
@app.route('/api/status', methods=['GET'])
def status():
    """
    Returns server status and configuration information for debugging
    """
    detected, queue_or_error, metadata = find_zebra_usb_printer_queue() if WIN32PRINT_AVAILABLE else (False, "win32print unavailable", None)
    return jsonify({
        "status":        "running",
        "use_test_mode": USE_TEST_MODE,
        "print_api_key_set": bool(PRINT_API_KEY),
        "external_api_key_set": bool(EXTERNAL_API_KEY),
        "printer_queue_fallback": PRINTER_QUEUE,
        "printer_queue_fallback_enabled": ALLOW_PRINTER_QUEUE_FALLBACK,
        "zebra_usb_serial_set": bool(ZEBRA_USB_SERIAL),
        "zebra_usb_serials": _configured_serials(),
        "discovered_zebra_usb_serials": discover_zebra_usb_serials(),
        "zebra_printers": ZEBRA_PRINTERS,
        "zebra_model_keywords": ZEBRA_MODEL_KEYWORDS,
        "wmi_available": WMI_AVAILABLE,
        "win32print_available": WIN32PRINT_AVAILABLE,
        "detected_zebra_queue": queue_or_error if detected else None,
        "printer_detection_error": None if detected else queue_or_error,
    })

# --- Execute ---
if __name__ == "__main__":
    print("Starting HTTPS server for badge printing application...")
    print("Local URL: https://127.0.0.1:8443")
    print("Network URL: https://<this-server-ip>:8443")
    print("")
    print("NOTE: This server uses Flask's ad-hoc SSL certificates which work on all systems")
    print("without requiring OpenSSL to be installed separately.")
    print("")
    print("Certificate warnings are normal for development - accept them in your browser.")
    print("In browser, click 'Advanced' -> proceed to the server address.")
    print(f"Configuration: USE_TEST_MODE={USE_TEST_MODE}")
    print(f"Zebra USB serials: {_configured_serials() or '(not set - use POST /api/printers/autofill)'}")
    print(f"Printer fallback queue: {PRINTER_QUEUE} | fallback enabled: {ALLOW_PRINTER_QUEUE_FALLBACK}")
    
    # Use pyngrok for public access
    try:
        from pyngrok import ngrok
        NGROK_AUTHTOKEN = os.environ.get('NGROK_AUTHTOKEN')
        if NGROK_AUTHTOKEN:
            ngrok.set_auth_token(NGROK_AUTHTOKEN)
        # Use the reserved/permanent domain from the ngrok account.
        # Override with the NGROK_DOMAIN env var if needed.
        NGROK_DOMAIN = os.environ.get('NGROK_DOMAIN', 'sijori.ngrok.dev')
        public_url = ngrok.connect(8080, domain=NGROK_DOMAIN)
        print(f"\nPublic URL: {public_url}\n")
        app.run(host='0.0.0.0', port=8080, debug=False)
    except Exception as e:
        print(f"Could not start server: {e}")
        print("Make sure port is available and you have proper permissions.")
