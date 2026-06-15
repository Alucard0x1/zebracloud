import win32print

printer_name = "HPRT HT100"

# According to manual, send simple ZPL test
zpl = """^XA
^PW576
^LL400
^FO50,50^A0N,50,50^FDHPRT TEST^FS
^FO50,150^A0N,30,30^FDZPL MODE OK^FS
^XZ"""

print(f"Sending ZPL to {printer_name}...")
print(zpl)

try:
    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        hJob = win32print.StartDocPrinter(hPrinter, 1, ("ZPL Test", None, "RAW"))
        try:
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, zpl.encode('ascii'))
            win32print.EndPagePrinter(hPrinter)
        finally:
            win32print.EndDocPrinter(hPrinter)
    finally:
        win32print.ClosePrinter(hPrinter)
    print("\n[OK] ZPL sent successfully")
    print("Check HPRT printer - label should print immediately")
    print("\nIf nothing prints:")
    print("1. Check printer is in ZPL mode (not TSPL mode)")
    print("2. Check printer has paper loaded")
    print("3. Try power cycling the printer")
except Exception as e:
    print(f"\n[FAIL] {e}")
