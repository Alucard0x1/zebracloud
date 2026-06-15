import win32print

printer_name = "ZDesigner ZD421-203dpi ZPL"

print(f"Checking printer: {printer_name}\n")

try:
    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        printer_info = win32print.GetPrinter(hPrinter, 2)
        print(f"Status: {printer_info['Status']}")
        print(f"Jobs in queue: {printer_info['cJobs']}")
        print(f"Port: {printer_info['pPortName']}")
        print(f"Driver: {printer_info['pDriverName']}")
        print(f"Attributes: {printer_info['Attributes']}")
        
        # Check job queue
        jobs = win32print.EnumJobs(hPrinter, 0, -1, 1)
        if jobs:
            print(f"\nPending jobs: {len(jobs)}")
            for job in jobs:
                print(f"  Job ID: {job['JobId']}, Status: {job['Status']}, Pages: {job['TotalPages']}")
        else:
            print("\nNo jobs in queue")
            
    finally:
        win32print.ClosePrinter(hPrinter)
except Exception as e:
    print(f"Error: {e}")

print("\nTrying to print a simple test...")
try:
    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        # Very simple ZPL test
        simple_zpl = "^XA^FO50,50^A0N,50,50^FDTEST^FS^XZ"
        hJob = win32print.StartDocPrinter(hPrinter, 1, ("Simple Test", None, "RAW"))
        try:
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, simple_zpl.encode('ascii'))
            win32print.EndPagePrinter(hPrinter)
        finally:
            win32print.EndDocPrinter(hPrinter)
    finally:
        win32print.ClosePrinter(hPrinter)
    print("Simple test sent!")
except Exception as e:
    print(f"Failed: {e}")
