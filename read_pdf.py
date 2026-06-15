import PyPDF2
import sys

# Fix encoding for Windows console
sys.stdout.reconfigure(encoding='utf-8')

pdf_path = "HPTZPL.pdf.pdf"

try:
    with open(pdf_path, 'rb') as file:
        pdf_reader = PyPDF2.PdfReader(file)
        print(f"Total pages: {len(pdf_reader.pages)}\n")
        
        # Search for ZPL-related content
        keywords = ['zpl', 'emulation', 'mode', 'enable', 'switch', 'command']
        
        for i, page in enumerate(pdf_reader.pages):
            text = page.extract_text().lower()
            if any(keyword in text for keyword in keywords):
                print(f"\n{'='*80}")
                print(f"Page {i+1} - Contains ZPL-related content:")
                print('='*80)
                print(page.extract_text()[:2000])  # First 2000 chars
                
except Exception as e:
    print(f"Error: {e}")
