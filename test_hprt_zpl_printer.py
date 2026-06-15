import win32print

printer_name = "HPRT HT100 - ZPL"

zpl = """^XA
^PW576
^LL400
^FO50,50^A0N,50,50^FDHPRT ZPL^FS
^FO50,150^A0N,30,30^FDMode Working!^FS
^XZ"""

print(f"Testing: {printer_name}")

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
    print("[OK] Print sent! Check HPRT for label.")
except Exception as e:
    print(f"[FAIL] {e}")
