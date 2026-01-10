import xml.etree.ElementTree as ET
import os
from datetime import datetime

def generate_report(xml_path, tex_path, log_path=None):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    
    # Iterate to find the testsuite element (pytest puts it as root or child)
    testsuite = root if root.tag == 'testsuite' else root.find('testsuite')
    
    if testsuite is None:
        # Sometimes root *is* the testsuites collection
        testsuite = root.find('testsuite')

    stats = {
        'tests': testsuite.attrib.get('tests', '0'),
        'failures': testsuite.attrib.get('failures', '0'),
        'errors': testsuite.attrib.get('errors', '0'),
        'skipped': testsuite.attrib.get('skipped', '0'),
        'time': testsuite.attrib.get('time', '0'),
        'timestamp': testsuite.attrib.get('timestamp', datetime.now().isoformat())
    }

    # Prepare TeX content
    tex_content = r"""\documentclass{article}
\usepackage[utf8]{inputenc}
\usepackage{geometry}
\usepackage{longtable}
\usepackage{xcolor}
\usepackage{booktabs}
\usepackage{listings}
\usepackage{pdfpages} % Added for including external PDFs
\geometry{a4paper, margin=1in}

\lstset{
    basicstyle=\ttfamily\scriptsize,
    breaklines=true,
    breakatwhitespace=false,
    frame=single,
    showstringspaces=false
}

\title{MEA API Test Report}
\author{Automated Test Suite}
\date{\today}

\begin{document}

\maketitle

\section{Summary}
\begin{itemize}
    \item \textbf{Total Tests:} """ + stats['tests'] + r"""
    \item \textbf{Passed:} """ + str(int(stats['tests']) - int(stats['failures']) - int(stats['errors']) - int(stats['skipped'])) + r"""
    \item \textbf{Failed:} """ + stats['failures'] + r"""
    \item \textbf{Errors:} """ + stats['errors'] + r"""
    \item \textbf{Skipped:} """ + stats['skipped'] + r"""
    \item \textbf{Total Duration:} """ + stats['time'] + r""" seconds
\end{itemize}

\section{Detailed Results}
\begin{longtable}{p{0.6\textwidth} p{0.2\textwidth} p{0.1\textwidth}}
\toprule
\textbf{Test Case} & \textbf{Status} & \textbf{Time (s)} \\
\midrule
\endhead
"""

    detailed_logs = ""
    
    for case in testsuite.findall('testcase'):
        name = case.attrib.get('name', 'Unknown')
        classname = case.attrib.get('classname', '').replace('tests.api.', '')
        time_taken = case.attrib.get('time', '0.000')
        
        # Determine status
        status = "Pass"
        color = "green"
        
        failure = case.find('failure')
        error = case.find('error')
        skipped = case.find('skipped')
        
        if failure is not None:
            status = "Fail"
            color = "red"
        elif error is not None:
            status = "Error"
            color = "red"
        elif skipped is not None:
            status = "Skipped"
            color = "orange"
            
        # Escape TeX special chars in name
        safe_name = name.replace('_', r'\_')
        
        row = f"{safe_name} & \\textcolor{{{color}}}{{{status}}} & {time_taken} \\\\\n"
        tex_content += row
        
        # Extract system-out log
        system_out = case.find('system-out')
        if system_out is not None and system_out.text:
            # Sanitize log text to remove non-ASCII chars that break pdflatex (e.g. smart quotes)
            log_text = system_out.text.encode('ascii', 'ignore').decode('ascii').strip()
            if log_text:
                detailed_logs += f"\\subsection*{{{safe_name}}}\n"
                detailed_logs += "\\begin{lstlisting}\n"
                detailed_logs += log_text
                detailed_logs += "\n\\end{lstlisting}\n"

    tex_content += r"""\bottomrule
\end{longtable}
"""
    
    # Append per-test logs if any
    if detailed_logs:
        tex_content += r"\section{Individual Test Case Logs}"
        tex_content += detailed_logs

    # Append global raw log if provided (optional backup)
    if log_path and os.path.exists(log_path):
        with open(log_path, 'r', errors='ignore') as log_file:
            log_content = log_file.read()
            # Also sanitize this content specifically for LaTeX compatibility
            log_content = log_content.encode('ascii', 'ignore').decode('ascii')
        tex_content += r"""
\section{Global Execution Log}
\begin{lstlisting}
""" + log_content + r"""
\end{lstlisting}
"""


    # Server Side Logs Section
    tex_content += r"""
\newpage
\section{Server Side Logs}
The following pages contain the official server-side logs from the MEA Sandbox admin panel, confirming the receipt and processing of the OCPP messages.

"""

    # Copy and Include PDFs
    import shutil
    import subprocess
    import re
    
    source_dir = "doc/MEA"
    target_dir = "tex"
    
    pdfs_to_include = [
        ("Select OCPP Message to view | OCPP - Admin.pdf", "server_log_msgs.pdf"),
        ("Select Communication to view | OCPP - Admin.pdf", "server_log_comms.pdf")
    ]
    
    def get_pdf_dims(path):
        try:
            res = subprocess.run(['pdfinfo', path], capture_output=True, text=True)
            if res.returncode != 0: return None
            # Extract "Page size:       1450 x 5177 pts"
            m = re.search(r'Page size:\s+(\d+(?:\.\d+)?)\s+x\s+(\d+(?:\.\d+)?)\s+pts', res.stdout)
            if m:
                return float(m.group(1)), float(m.group(2))
        except Exception as e:
            print(f"Error getting dims for {path}: {e}")
        return None

    for original_name, safe_name in pdfs_to_include:
        src_path = os.path.join(source_dir, original_name)
        dst_path = os.path.join(target_dir, safe_name)
        
        if os.path.exists(src_path):
            try:
                shutil.copy(src_path, dst_path)
                print(f"Copied {original_name} to {safe_name}")
                
                dims = get_pdf_dims(dst_path)
                if dims:
                    w, h = dims
                    print(f"PDF {safe_name} dimensions: {w}x{h}")
                    
                    # A4 Portrait Aspect Ratio (approx)
                    # We want to fit Width to Page Width.
                    # Height of the slice should be proportional to A4 aspect ratio.
                    # A4 is 595.28 x 841.89 pts -> Ratio ~ 1.414
                    target_ratio = 842.0 / 595.0
                    chunk_height = w * target_ratio
                    
                    import math
                    num_chunks = math.ceil(h / chunk_height)
                    
                    for i in range(num_chunks):
                        # Chunk 0 is the TOP of the page.
                        # y coordinates start at 0 (bottom).
                        upper_y = h - (i * chunk_height)
                        lower_y = max(0, h - ((i + 1) * chunk_height))
                        
                        # viewport="<llx> <lly> <urx> <ury>"
                        # We clip the PDF to this rectangle.
                        viewport = f"0 {lower_y} {w} {upper_y}"
                        
                        print(f"  > Slice {i+1}/{num_chunks}: {viewport}")
                        
                        # Use clip=true and viewport. 
                        # We also scale=0.85 to fit nicely with margins and avoid footer overlap.
                        tex_content += r"\includepdf[pages=1, viewport=" + viewport + r", clip, scale=0.85, pagecommand={\thispagestyle{plain}}, frame=true, linkname=" + f"{safe_name}_{i}" + r"]{" + safe_name + r"}" + "\n"
                else:
                    # Fallback if pdfinfo fails
                    tex_content += r"\includepdf[pages=-, scale=0.85, pagecommand={\thispagestyle{plain}}, frame=true]{" + safe_name + r"}" + "\n"

            except Exception as e:
                print(f"Error copying/processing {original_name}: {e}")
                tex_content += f"\n% Error including {original_name}: {e}\n"
        else:
             print(f"Warning: {original_name} not found at {src_path}")
             tex_content += f"\n% Warning: {original_name} not found\n"

    tex_content += r"""
\end{document}
"""

    with open(tex_path, 'w') as f:
        f.write(tex_content)
    
    print(f"Generated {tex_path}")

if __name__ == "__main__":
    generate_report('tex/api_test_results.xml', 'tex/api_test_report.tex', 'tex/api_test.log')
