import win32print

printer_name = "HPRT HT100"

try:
    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        jobs = win32print.EnumJobs(hPrinter, 0, -1, 1)
        if jobs:
            print(f"Jobs in queue: {len(jobs)}")
            for job in jobs:
                print(f"\nJob ID: {job['JobId']}")
                print(f"  Status: {job['Status']}")
                print(f"  Document: {job['pDocument']}")
                print(f"  Pages: {job['TotalPages']}")
                print(f"  Bytes: {job['Size']}")
        else:
            print("No jobs in queue")
    finally:
        win32print.ClosePrinter(hPrinter)
except Exception as e:
    print(f"Error: {e}")
