import win32print

printer_name = "HPRT HT100"

# TSPL command to check printer status
tspl_status = "~!T\r\n"

# Try ZPL host status command
zpl_status = "~HS\r\n"

print(f"Sending status query to {printer_name}...")
print("\nTrying TSPL status command...")

try:
    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        hJob = win32print.StartDocPrinter(hPrinter, 1, ("Status Query", None, "RAW"))
        try:
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, tspl_status.encode('ascii'))
            win32print.EndPagePrinter(hPrinter)
        finally:
            win32print.EndDocPrinter(hPrinter)
    finally:
        win32print.ClosePrinter(hPrinter)
    print("[OK] TSPL status command sent")
except Exception as e:
    print(f"[FAIL] {e}")

print("\nTo enable ZPL emulation on HPRT HT100:")
print("1. Turn off printer")
print("2. Hold FEED button while turning on")
print("3. Look for 'Emulation' or 'Language' option")
print("4. Select 'ZPL' or 'Zebra' mode")
print("5. Save and restart")
print("\nOR check printer manual for TSPL command to switch mode")
print("Some HPRT models support: ~!ZPL to enable ZPL mode")
