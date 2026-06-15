import win32print

printer_name = "HPRT HT100 - ZPL"

with open('label.zpl', 'r') as f:
    zpl = f.read()

print(f"Testing: {printer_name}")
print(f"Sending {len(zpl)} bytes from label.zpl...\n")

try:
    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        hJob = win32print.StartDocPrinter(hPrinter, 1, ("Label Test", None, "RAW"))
        try:
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, zpl.encode('ascii'))
            win32print.EndPagePrinter(hPrinter)
        finally:
            win32print.EndDocPrinter(hPrinter)
    finally:
        win32print.ClosePrinter(hPrinter)
    print("[OK] Label printed successfully on HPRT!")
except Exception as e:
    print(f"[FAIL] {e}")
