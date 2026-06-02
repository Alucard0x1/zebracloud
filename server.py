import os
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dotenv import load_dotenv
from flask import Flask, send_from_directory, request, jsonify
import qrcode
from PIL import Image

load_dotenv()

# Guarded import of pywin32's win32print. On hosts where pywin32 is not
# installed (e.g. non-Windows dev machines) we still want server to import
# cleanly so test mode and the rest of the HTTP surface remain usable; the
# Printer_Transport surfaces a clean error string when called in normal mode.
# Per spec zpl-printer-migration task 5.1 (requirements 6.6).
try:
    import win32print
    WIN32PRINT_AVAILABLE = True
except ImportError:
    win32print = None
    WIN32PRINT_AVAILABLE = False

# Load non-secret printer configuration from printer_config.json.
# Secrets must come from environment variables so they are not exposed by
# configuration files or browser JavaScript.
try:
    with open('printer_config.json', 'r') as config_file:
        config = json.load(config_file)
        PRINTER_QUEUE = config.get('PRINTER_QUEUE', 'ZDesigner ZD230-203dpi ZPL')
        USE_TEST_MODE = config.get('USE_TEST_MODE', False)
except FileNotFoundError:
    print("Warning: printer_config.json not found. Using default configuration.")
    PRINTER_QUEUE = "ZDesigner ZD230-203dpi ZPL"
    USE_TEST_MODE = False
except json.JSONDecodeError:
    print("Warning: printer_config.json is not valid JSON. Using default configuration.")
    PRINTER_QUEUE = "ZDesigner ZD230-203dpi ZPL"
    USE_TEST_MODE = False

if "USE_TEST_MODE" in os.environ:
    USE_TEST_MODE = os.environ["USE_TEST_MODE"].strip().lower() in {"1", "true", "yes", "on"}

PRINT_API_KEY = os.environ.get("PRINT_API_KEY", "")
EXTERNAL_API_KEY = os.environ.get("EXTERNAL_API_KEY", "")
EXTERNAL_API_BASE_URL = os.environ.get("EXTERNAL_API_BASE_URL", "https://tickets.w.media/link")

# Initialize the Flask web server
app = Flask(__name__, static_folder='.')

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
    for header in ("Origin", "Referer"):
        value = request.headers.get(header)
        if not value:
            continue
        parsed = urllib.parse.urlparse(value)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return origin == expected
    return True


def _print_request_authorized() -> bool:
    """Authorize local browser requests, plus optional env-keyed API clients."""
    provided = request.headers.get("X-API-Key", "")
    if PRINT_API_KEY and provided and secrets.compare_digest(provided, PRINT_API_KEY):
        return True
    return _request_is_same_origin()

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

    ``attendee`` is expected to already contain sanitized, prepared fields:
        - 'category'      : str  (already ASCII-stripped and uppercased)
        - 'name_line1'    : str  (already ASCII-stripped and derived)
        - 'company_line1' : str  (already ASCII-stripped, may be '')
        - 'company_line2' : str  (already ASCII-stripped, '' if not used)
        - 'code'          : str  (already ASCII-stripped, '' allowed)

    Returns a single ``str`` that, when encoded as ASCII, is a valid ZPL II
    job framed by ``^XA`` ... ``^XZ``.

    Field emission order:
      1. ``^XA``
      2. ``^PON``
      3. ``^PW800``
      4. ``^LL1200``
      5. ``^LH0,0``
      6. Category : ``^A0N,50,50^FO50,80^FD<cat>^FS``
      7. Name     : ``^A0N,110,110^FO50,200^FD<name>^FS``
      8. Company1 : ``^A0N,50,50^FO50,380^FD<c1>^FS``
      9. Company2 : ``^A0N,50,50^FO50,450^FD<c2>^FS`` (only when non-empty)
     10. QR       : ``^FO200,640^GFA...^FS``
     11. ``^PQ1``
     12. ``^XZ``

    Per spec zpl-printer-migration task 4.2 (requirements 2.1-2.8, 3.1-3.6,
    4.1-4.3).
    """
    category      = _zpl_escape(attendee.get("category", ""))
    name_line1    = _zpl_escape(attendee.get("name_line1", ""))
    company_line1 = _zpl_escape(attendee.get("company_line1", ""))
    company_line2_raw = attendee.get("company_line2", "")
    company_line2 = _zpl_escape(company_line2_raw)
    qr            = _qr_payload(attendee.get("code", ""))

    parts = [
        "^XA",
        "^PON",
        "^PW800",
        "^LL1200",
        "^LH0,0",
        f"^A0N,50,50^FO50,80^FD{category}^FS",
        f"^A0N,110,110^FO50,200^FD{name_line1}^FS",
        f"^A0N,50,50^FO50,380^FD{company_line1}^FS",
    ]
    if company_line2_raw:
        parts.append(f"^A0N,50,50^FO50,450^FD{company_line2}^FS")
    # QR code as graphic: 5cm = 400 dots, rendered via Python qrcode library.
    # Bottom edge 2cm (160 dots) from label bottom: QR top = 1200 - 160 - 400 = 640.
    # Centered horizontally: (800 - 400) / 2 = 200.
    qr_data = qr if qr != "NO-CODE" else "NO-CODE"
    qr_gfa = _qr_to_gfa(qr_data, target_dots=400)
    parts.append(f"^FO200,640{qr_gfa}")
    parts.append("^PQ1")
    parts.append("^XZ")
    return "".join(parts)


def send_zpl_to_printer(zpl: str) -> tuple[bool, str]:
    """Submit a ZPL II command to the local Windows printer queue.

    In test mode (``USE_TEST_MODE`` is True) the bytes are written to
    ``test_print_command.prn`` in the current working directory and no print
    job is dispatched. In normal mode the bytes are submitted as a RAW print
    job to the queue named by ``PRINTER_QUEUE`` via ``win32print``; the
    spooler is responsible for forwarding them verbatim to the printer.

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
        hPrinter = win32print.OpenPrinter(PRINTER_QUEUE)
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
        return True, "Print command sent successfully."
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

    query = urllib.parse.urlencode({param_type: param_value})
    url = f"{EXTERNAL_API_BASE_URL}?{query}"
    req = urllib.request.Request(url, headers={"X-API-KEY": EXTERNAL_API_KEY}, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = response.read().decode("utf-8")
            data = json.loads(payload)
    except urllib.error.HTTPError as e:
        return jsonify({"success": False, "error": f"Ticket API returned HTTP {e.code}."}), 502
    except (urllib.error.URLError, TimeoutError) as e:
        return jsonify({"success": False, "error": f"Ticket API request failed: {e}"}), 502
    except json.JSONDecodeError:
        return jsonify({"success": False, "error": "Ticket API returned invalid JSON."}), 502

    attendee = {
        "name": data.get("full_name") or data.get("name") or "Unknown Attendee",
        "jobTitle": data.get("job_title") or data.get("title") or "Attendee",
        "category": data.get("category") or "Delegate",
        "code": param_value,
        "email": data.get("email") or data.get("personal_email") or "",
        "company": data.get("company_name") or "",
        "eventcat": data.get("eventcat") or "N/A",
    }
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

        print(f"Received print request for: {name}")

        # Sanitize: strip every byte outside ASCII (Req 5.1)
        name     = name.encode('ascii', errors='ignore').decode('ascii')
        category = category.encode('ascii', errors='ignore').decode('ascii')
        code     = code.encode('ascii', errors='ignore').decode('ascii')
        company  = company.encode('ascii', errors='ignore').decode('ascii')

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

        # Build ZPL command (Req 1.7: any builder error -> HTTP 500)
        try:
            zpl = build_zpl(prepared)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

        # Submit (Req 6.1, 6.4, 6.6)
        success, message = send_zpl_to_printer(zpl)
        if success:
            return jsonify({"success": True, "message": message})
        else:
            return jsonify({"success": False, "error": message}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# Test endpoint to verify the server is running and configuration is loaded
@app.route('/api/status', methods=['GET'])
def status():
    """
    Returns server status and configuration information for debugging
    """
    return jsonify({
        "status":        "running",
        "use_test_mode": USE_TEST_MODE,
        "print_api_key_set": bool(PRINT_API_KEY),
        "external_api_key_set": bool(EXTERNAL_API_KEY),
        "printer_queue": PRINTER_QUEUE
    })

# --- Execute ---
if __name__ == "__main__":
    print("Starting HTTPS server for badge printing application...")
    print("Server will be accessible at: https://192.168.1.2:8443")
    print("")
    print("NOTE: This server uses Flask's ad-hoc SSL certificates which work on all systems")
    print("without requiring OpenSSL to be installed separately.")
    print("")
    print("Certificate warnings are normal for development - accept them in your browser.")
    print("In browser, click 'Advanced' -> 'Proceed to 192.168.1.2 (unsafe)'")
    print(f"Configuration: PRINTER_QUEUE={PRINTER_QUEUE}, USE_TEST_MODE={USE_TEST_MODE}")
    
    # Use Flask's ad-hoc SSL which works on all systems without requiring OpenSSL
    try:
        app.run(host='0.0.0.0', port=8443, debug=False, ssl_context='adhoc')
    except Exception as e:
        if "Address already in use" in str(e) or "Only one usage of each socket" in str(e):
            print("Port 8443 is already in use.")
            print("Trying alternative port 8444...")
            try:
                app.run(host='0.0.0.0', port=8444, debug=False, ssl_context='adhoc')
            except Exception as e2:
                print(f"Could not start HTTPS server on port 8444: {e2}")
                print("Make sure ports 8443 or 8444 are available and you have proper permissions.")
        else:
            print(f"Could not start HTTPS server: {e}")
            print("Make sure ports are available and you have proper permissions.")
