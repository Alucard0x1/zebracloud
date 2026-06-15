import win32print

# Read the ZPL file
with open('label.zpl', 'r') as f:
    zpl = f.read()

print("Available printers:")
printers = [printer[2] for printer in win32print.EnumPrinters(2)]
for i, printer in enumerate(printers, 1):
    print(f"{i}. {printer}")

print("\nLooking for Zebra printers...")
zebra_printers = [p for p in printers if any(kw in p.lower() for kw in ['zebra', 'zdesigner'])]

if not zebra_printers:
    print("No Zebra printers found!")
    exit(1)

printer_name = zebra_printers[0]
print(f"\nUsing: {printer_name}")
print(f"Sending {len(zpl)} bytes of ZPL data...\n")

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
    print("[OK] Print job sent successfully!")
    print("Check your Zebra printer for output.")
except Exception as e:
    print(f"[FAIL] Print failed: {e}")
