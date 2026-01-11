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

\title{MEA OCPP Live Test}
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
    
    current_section = None
    
    SECTION_TITLES = {
        "7_01": "Remote Start (Unplugged)",
        "7_02": "Concurrent Remote Start",
        "7_03": "Swap Card",
        "7_04": "Emergency Stop",
        "7_05": "Open Door",
        "7_06": "Power Loss (Single)",
        "7_07": "Local List (Offline)"
    }
    
    for case in testsuite.findall('testcase'):
        name = case.attrib.get('name', 'Unknown')
        classname = case.attrib.get('classname', '').replace('tests.api.', '')
        time_taken = case.attrib.get('time', '0.000')
        
        # Check for section change
        # Expected format: test_SECTION_SUBSECTION_...
        # e.g. test_7_01_01 -> section "7_01"
        parts = name.split('_') # ['test', '7', '01', '01', ...]
        if len(parts) >= 3 and parts[0] == 'test':
            section_key = f"{parts[1]}_{parts[2]}"
            if section_key != current_section:
                current_section = section_key
                title = SECTION_TITLES.get(section_key)
                if title:
                    # Insert header row
                    clean_title = title.replace('_', r'\_')
                    tex_content += f"\\multicolumn{{3}}{{l}}{{\\textbf{{{parts[1]}.{int(parts[2])} {clean_title}}}}} \\\\ \\midrule\n"
        
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


    tex_content += r"""
\end{document}
"""

    with open(tex_path, 'w') as f:
        f.write(tex_content)
    
    print(f"Generated {tex_path}")

if __name__ == "__main__":
    generate_report('tex/api_test_results.xml', 'tex/api_test_report.tex', 'tex/api_test.log')
