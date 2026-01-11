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

    # Calculate actual stats excluding initialization
    total_tests = 0
    passed = 0
    failures = 0
    errors = 0
    skipped = 0
    
    # Get total time from suite
    total_time = testsuite.attrib.get('time', '0')

    for case in testsuite.findall('testcase'):
        name = case.attrib.get('name', 'Unknown')
        if 'initialization' in name:
            continue
            
        total_tests += 1
        if case.find('failure') is not None:
             failures += 1
        elif case.find('error') is not None:
             errors += 1
        elif case.find('skipped') is not None:
             skipped += 1
        else:
             passed += 1

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
    \item \textbf{Total Tests:} """ + str(total_tests) + r"""
    \item \textbf{Passed:} """ + str(passed) + r"""
    \item \textbf{Failed:} """ + str(failures) + r"""
    \item \textbf{Errors:} """ + str(errors) + r"""
    \item \textbf{Skipped:} """ + str(skipped) + r"""
    \item \textbf{Total Duration:} """ + total_time + r""" seconds
\end{itemize}

"""

    # Collect rows by section
    section_rows = {}
    
    # Also keep track of other sections for generic handling if needed, but primarily 8.x
    
    SECTION_TITLES = {
        "1": "Configuration & Boot Notification",
        "2": "Auto Charge Verification",
        "3": "Normal Charge Verification",
        "4": "Reset Verification",
        "5": "Reservation Order Verification",
        "6": "Smart Charging Profile Verification",
        
        "7_01": "Remote Start (Unplugged)",
        "7_02": "Concurrent Remote Start",
        "7_03": "Swap Card",
        "7_04": "Emergency Stop",
        "7_05": "Open Door",
        "7_06": "Power Loss",
        "7_07": "Local List (Offline)",

        "8_01": "Dual Connector Concurrent Remote Start",
        "8_02": "Dual Connector Shared Emergency Stop",
        "8_03": "Dual Connector Power Loss",

        "9_01": "MEA Specific Configuration",
        "9_02": "Meter Value Sample Interval",
        "9_03": "Local Authorize Offline",
        "9_04": "Power Demand Verification",

        "10": "Summary Verification",
        "11": "Performance Verification"
    }

    detailed_logs = ""

    for case in testsuite.findall('testcase'):
        name = case.attrib.get('name', 'Unknown')

        # Skip initialization tests from the report
        if 'initialization' in name:
            continue

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

        # Check for Injection Annotation in logs
        system_out = case.find('system-out')
        if system_out is not None and system_out.text:
            if "[RESULT_ANNOTATION] (Injected)" in system_out.text:
                 status = "Pass (Injected)"
                 color = "blue"
            
        # Escape TeX special chars in name
        safe_name = name.replace('_', r'\_')
        
        # Determine grouping key
        parts = name.split('_') # ['test', '8', '01', '01', ...]
        section_key = "unknown"
        
        # Sections that should have sub-tables (7, 8, 9)
        SPLIT_SECTIONS = ['7', '8', '9']
        
        if len(parts) >= 3 and parts[0] == 'test':
            sec_id = parts[1]
            if sec_id in SPLIT_SECTIONS:
                # Group by Sub-Section (e.g. 7_01, 8_03)
                section_key = f"{parts[1]}_{parts[2]}"
            else:
                # Group by Main Section (e.g. 1, 2, 10)
                section_key = sec_id

            # Format: test_X_Y_Z... -> X.Y.Z or test_X_Y... -> X.Y
            if len(parts) >= 3:
                try:
                    p1 = parts[1]
                    # Handle single digit vs double digit parsing if needed, 
                    # but usually strings "01" etc are fine to keep unless stripping leading zeros.
                    # Current strict logic:
                    safe_p2 = str(int(parts[2])) if parts[2].isdigit() else parts[2]
                    case_num = f"{p1}.{safe_p2}"
                    
                    # If it's a split section, we often want the 3rd or 4th part?
                    # Actually for 8.1.1, parts are test_8_01_01. 
                    # p1=8, p2=01 -> 1. So 8.1.
                    # Then if next part is 01, -> 8.1.1
                    
                    if len(parts) >= 4 and parts[3].isdigit():
                         p3 = str(int(parts[3]))
                         case_num += f".{p3}"
                except:
                    case_num = safe_name
            else:
                case_num = safe_name

            row = f"{case_num} & {safe_name} & \\textcolor{{{color}}}{{{status}}} & {time_taken} \\\\\n"
            
            if section_key in section_rows:
                section_rows[section_key].append(row)
            else:
                # Fallback for non-8 sections if mixed (though we run section 8 only)
                if section_key not in section_rows:
                    section_rows[section_key] = []
                section_rows[section_key].append(row)

        # Extract logs (same as before)
        system_out = case.find('system-out')
        if system_out is not None and system_out.text:
            log_text = system_out.text.encode('ascii', 'ignore').decode('ascii').strip()
            if log_text:
                detailed_logs += f"\\subsection*{{{safe_name}}}\n"
                detailed_logs += "\\begin{lstlisting}\n"
                detailed_logs += log_text
                detailed_logs += "\n\\end{lstlisting}\n"

    # Generate Tables
    tex_content += r"\section{Detailed Results}" + "\n"
    
    # Sort keys to ensure order 8_01, 8_02, 8_03
    sorted_keys = sorted(section_rows.keys())
    
    for section_key in sorted_keys:
        rows = section_rows[section_key]
        if not rows:
            continue
            
        title = SECTION_TITLES.get(section_key, f"Section {section_key.replace('_', '.')}")
        
        tex_content += f"\\subsection{{{title}}}\n"
        tex_content += r"""
\begin{longtable}{p{0.15\textwidth} p{0.55\textwidth} p{0.15\textwidth} p{0.1\textwidth}}
\toprule
\textbf{Test Case} & \textbf{Test Name} & \textbf{Status} & \textbf{Time (s)} \\
\midrule
\endhead
"""
        for row in rows:
            tex_content += row
            
        tex_content += r"""\bottomrule
\end{longtable}
"""

    if detailed_logs:
        tex_content += r"\section{Individual Test Case Logs}" + detailed_logs

    # Append global raw log
    # If a log file is provided and has content, use it.
    # Otherwise, reconstruct it from the XML system-out elements.
    log_content = ""
    if log_path and os.path.exists(log_path):
        with open(log_path, 'r', errors='ignore') as log_file:
            content = log_file.read().strip()
            if len(content) > 100: # Arbitrary threshold to decide if file has real logs
                log_content = content.encode('ascii', 'ignore').decode('ascii')

    if not log_content:
        print("Log file empty or missing, reconstructing from XML system-out...")
        reconstructed_log = []
        for case in testsuite.findall('testcase'):
            name = case.attrib.get('name', 'Unknown')
            system_out = case.find('system-out')
            if system_out is not None and system_out.text:
                 reconstructed_log.append(f"--- Test Case: {name} ---")
                 reconstructed_log.append(system_out.text.strip())
                 reconstructed_log.append("") # Separator
        
        if reconstructed_log:
            log_content = "\n".join(reconstructed_log).encode('ascii', 'ignore').decode('ascii')

    if log_content:
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
