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
import qrcode
from PIL import Image

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


def _contains_any_keyword(text: str, keywords: list[str]) -> bool:
    text = (text or "").lower()
    return any(str(keyword).lower() in text for keyword in keywords)


def _extract_usb_serial_from_pnp_id(pnp_id: str) -> str:
    parts = (pnp_id or "").split("\\")
    return parts[-1] if len(parts) >= 3 else ""


def _list_installed_printers() -> list[dict]:
    if not WIN32PRINT_AVAILABLE:
        return []

    printers = win32print.EnumPrinters(
        win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    )
    result = []
    for printer in printers:
        queue_name = printer[2]
        info = {"name": queue_name, "driver": "", "port": ""}
        try:
            hprinter = win32print.OpenPrinter(queue_name)
            try:
                printer_info = win32print.GetPrinter(hprinter, 2)
                info = {
                    "name": printer_info.get("pPrinterName") or queue_name,
                    "driver": printer_info.get("pDriverName") or "",
                    "port": printer_info.get("pPortName") or "",
                }
            finally:
                win32print.ClosePrinter(hprinter)
        except Exception:
            pass
        result.append(info)
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

    if len(zebra_queues) == 1:
        return True, zebra_queues[0]["name"], {"queue": zebra_queues[0], "matched_usb_devices": matching_usb_devices, "mode": "single_zebra_queue"}

    for usb_device in matching_usb_devices:
        usb_name = usb_device.get("name", "").lower()
        tokens = [t for t in re.split(r"[^a-zA-Z0-9]+", usb_name) if len(t) >= 3]
        candidates = []
        for queue in zebra_queues:
            queue_text = f"{queue.get('name', '')} {queue.get('driver', '')}".lower()
            if any(token in queue_text for token in tokens):
                candidates.append(queue)
        if len(candidates) == 1:
            return True, candidates[0]["name"], {"queue": candidates[0], "usb_device": usb_device, "mode": "model_overlap"}

    return False, (
        "Multiple Zebra printer queues were detected. Serial numbers were auto-detected, but Windows did not expose "
        "a safe one-to-one serial -> queue map. Add the correct Windows queue name to each ZEBRA_PRINTERS entry "
        "after running /api/printers/autofill."
    ), {"zebra_queues": zebra_queues, "matched_usb_devices": matching_usb_devices, "configured_serials": configured_serials}


def _image_to_gfa(image: Image.Image) -> str:
    image = image.convert("L").resize((800, 1200), Image.NEAREST)
    image = image.point(lambda px: 0 if px < 160 else 255, "1")
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
    return f"^XA^PON^PW800^LL1200^LH0,0^FO0,0^GFA,{total_bytes},{total_bytes},{width_bytes},{''.join(hex_rows)}^FS^PQ1^XZ"


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

        print_mode = str(data.get("print_mode", "zpl") or "zpl").strip().lower()

        # Build ZPL command (Req 1.7: any builder error -> HTTP 500)
        try:
            if print_mode == "image":
                zpl = build_zpl_from_badge_image(data.get("badge_image", ""))
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
    """Return configured printer labels for the browser UI."""
    if not _print_request_authorized():
        return jsonify({"success": False, "error": "Unauthorized request."}), 401

    printers = []
    for item in ZEBRA_PRINTERS:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        if not label:
            continue
        printers.append({
            "label": label,
            "serial_set": bool(str(item.get("serial", "")).strip() and str(item.get("serial", "")).strip() != "AUTO_FILL_AFTER_SCAN"),
            "queue_set": bool(str(item.get("queue", "")).strip()),
        })
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
    return jsonify({
        "success": True,
        "detected": detected,
        "selected_queue": queue_or_error if detected else None,
        "error": None if detected else queue_or_error,
        "configured_zebra_usb_serial": ZEBRA_USB_SERIAL,
        "configured_zebra_usb_serials": _configured_serials(),
        "discovered_zebra_usb_serials": discover_zebra_usb_serials(),
        "configured_zebra_printers": ZEBRA_PRINTERS,
        "installed_printers": _list_installed_printers(),
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
