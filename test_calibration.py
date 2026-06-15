import win32print

# Simple calibration label
zpl = """^XA
^PW800
^LL1200
^LH0,0
^FO50,50^GB700,1100,2^FS
^FO50,50^A0N,30,30^FDTop Left (50,50)^FS
^FO50,600^A0N,30,30^FDMiddle Left (50,600)^FS
^FO50,1100^A0N,30,30^FDBottom Left (50,1100)^FS
^FO400,50^A0N,50,50^FDSize Test^FS
^FO400,150^A0N,30,30^FD30pt Font^FS
^FO400,200^A0N,50,50^FD50pt Font^FS
^FO400,270^A0N,100,100^FD100pt^FS
^XZ"""

printers = ["ZDesigner ZD421-203dpi ZPL", "HPRT HT100 - ZPL"]

for printer_name in printers:
    print(f"\nPrinting calibration to: {printer_name}")
    try:
        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            hJob = win32print.StartDocPrinter(hPrinter, 1, ("Calibration", None, "RAW"))
            try:
                win32print.StartPagePrinter(hPrinter)
                win32print.WritePrinter(hPrinter, zpl.encode('ascii'))
                win32print.EndPagePrinter(hPrinter)
            finally:
                win32print.EndDocPrinter(hPrinter)
        finally:
            win32print.ClosePrinter(hPrinter)
        print("[OK] Sent")
    except Exception as e:
        print(f"[FAIL] {e}")

print("\nCompare the two labels:")
print("- Check if box sizes match")
print("- Check if text positions match")
print("- Check if font sizes match")
