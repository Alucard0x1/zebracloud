import win32print

# Simple ZPL test label
zpl = """^XA
^FO50,50^A0N,50,50^FDTEST HPRT^FS
^FO50,120^A0N,30,30^FDHPRT HT100 Test^FS
^XZ"""

printer_name = "HPRT HT100"

print(f"Testing printer: {printer_name}")
print(f"Sending {len(zpl)} bytes of ZPL data...\n")

try:
    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        hJob = win32print.StartDocPrinter(hPrinter, 1, ("HPRT Test", None, "RAW"))
        try:
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, zpl.encode('ascii'))
            win32print.EndPagePrinter(hPrinter)
        finally:
            win32print.EndDocPrinter(hPrinter)
    finally:
        win32print.ClosePrinter(hPrinter)
    print("[OK] Print job sent successfully!")
    print("Check your HPRT HT100 printer for output.")
except Exception as e:
    print(f"[FAIL] Print failed: {e}")

# Also check printer status
print("\nChecking printer status...")
try:
    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        printer_info = win32print.GetPrinter(hPrinter, 2)
        print(f"Status: {printer_info['Status']}")
        print(f"Jobs in queue: {printer_info['cJobs']}")
        print(f"Port: {printer_info['pPortName']}")
        print(f"Driver: {printer_info['pDriverName']}")
    finally:
        win32print.ClosePrinter(hPrinter)
except Exception as e:
    print(f"Status check failed: {e}")
