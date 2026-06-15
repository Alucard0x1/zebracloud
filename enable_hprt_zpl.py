import win32print

printer_name = "HPRT HT100"

# TSPL command to enable ZPL emulation (if supported)
# Different HPRT models use different commands
commands_to_try = [
    ("~!EMULATION,ZPL\r\n", "EMULATION command"),
    ("! EMULATION ZPL\r\n", "Alternative EMULATION"),
    ("^XA^SZZ^XZ", "ZPL mode switch"),
]

print(f"Attempting to enable ZPL emulation on {printer_name}...\n")

for cmd, desc in commands_to_try:
    print(f"Trying: {desc}")
    try:
        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            hJob = win32print.StartDocPrinter(hPrinter, 1, ("Enable ZPL", None, "RAW"))
            try:
                win32print.StartPagePrinter(hPrinter)
                win32print.WritePrinter(hPrinter, cmd.encode('ascii'))
                win32print.EndPagePrinter(hPrinter)
            finally:
                win32print.EndDocPrinter(hPrinter)
        finally:
            win32print.ClosePrinter(hPrinter)
        print(f"  [OK] Command sent\n")
    except Exception as e:
        print(f"  [FAIL] {e}\n")

print("Commands sent. Now test with:")
print("  python test_hprt.py")
print("\nIf still not working, check your HPRT HT100 manual for:")
print("- Physical button combination to enter setup mode")
print("- LCD menu to change emulation mode")
print("- Or the printer may not support ZPL emulation")
