import win32print

printer_name = "ZDesigner ZD421-203dpi ZPL"

print(f"Clearing printer queue for: {printer_name}\n")

try:
    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        jobs = win32print.EnumJobs(hPrinter, 0, -1, 1)
        print(f"Found {len(jobs)} jobs in queue")
        
        for job in jobs:
            try:
                win32print.SetJob(hPrinter, job['JobId'], 0, None, win32print.JOB_CONTROL_DELETE)
                print(f"Deleted job {job['JobId']}")
            except Exception as e:
                print(f"Could not delete job {job['JobId']}: {e}")
        
        print("\nQueue cleared!")
    finally:
        win32print.ClosePrinter(hPrinter)
except Exception as e:
    print(f"Error: {e}")
