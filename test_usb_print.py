import win32print

# Simple ZPL test label
zpl = """^XA
^FO50,50^A0N,50,50^FDTEST PRINT^FS
^FO50,120^A0N,30,30^FDZebra USB Test^FS
^XZ"""

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

try:
    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        hJob = win32print.StartDocPrinter(hPrinter, 1, ("Test Label", None, "RAW"))
        try:
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, zpl.encode('ascii'))
            win32print.EndPagePrinter(hPrinter)
        finally:
            win32print.EndDocPrinter(hPrinter)
    finally:
        win32print.ClosePrinter(hPrinter)
    print("[OK] Print job sent successfully!")
except Exception as e:
    print(f"[FAIL] Print failed: {e}")
