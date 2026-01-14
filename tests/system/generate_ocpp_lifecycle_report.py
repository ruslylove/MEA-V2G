import re
import os

# Configuration
LOG_FILE = "tests/system/ocpp_lifecycle.log"
OUTPUT_TEX = "tex/ocpp_lifecycle_report.tex"
TEMPLATE_HEADER = r"""\documentclass[10pt, a4paper, landscape]{article}
\usepackage[left=1cm, right=1cm, top=2cm, bottom=2cm]{geometry}
\usepackage{longtable}
\usepackage{array}
\usepackage{xcolor}
\usepackage{colortbl}
\usepackage{fancyhdr}
\usepackage{lscape}
\usepackage{verbatim}
\usepackage{fancyvrb}
\usepackage{listings}
\usepackage[hidelinks]{hyperref}

\title{\textbf{MEA Live Test Suite Report}}
\author{MEA OCPP Certification (Automated)}
\date{\today}

\pagestyle{fancy}
\fancyhf{}
\lhead{MEA Compliance Test Report - Live Suite}
\rhead{Page \thepage}

\begin{document}

\maketitle

\section*{MEA Live Test Execution Results}

\renewcommand{\arraystretch}{1.3}
\begin{longtable}{|p{6cm}|p{2.5cm}|p{10cm}|c|}
\hline
\rowcolor{gray!30}
\textbf{Test Case} & \textbf{Direction} & \textbf{Log Preview / Proof} & \textbf{Result} \\
\hline
\endfirsthead

\hline
\hline
\rowcolor{gray!30}
\textbf{Test Case} & \textbf{Direction} & \textbf{Log Preview / Proof} & \textbf{Result} \\
\hline
\endhead

\hline
\endfoot
"""

TEMPLATE_FOOTER = r"""
\end{longtable}
\newpage
\appendix
\section{Raw Test Logs}
\label{sec:raw_logs}
\normalsize
\lstinputlisting[
    breaklines=true,
    basicstyle=\small\ttfamily,
    columns=fullflexible,
    keepspaces=true,
    breakatwhitespace=false,
    frame=single,
    rulecolor=\color{gray!30},
    numbers=left,
    numberstyle=\tiny\color{gray},
    stepnumber=1,
    numbersep=5pt
]{ocpp_lifecycle.log}

\end{document}
"""

def parse_logs():
    if not os.path.exists(LOG_FILE):
        return []

    with open(LOG_FILE, "r") as f:
        log_lines = f.readlines()

    rows = []
    current_test = None
    test_logs = []
    
    test_start_pattern = re.compile(r"^tests/system/test_ocpp_lifecycle.py::(\w+)")
    result_pattern = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED)$")

    for line in log_lines:
        line = line.strip()
        
        match = test_start_pattern.match(line)
        if match:
            # If we had a previous test, finalize it
            if current_test:
                rows.append({
                    "name": current_test,
                    "logs": test_logs[:10], # Keep first 10 log lines for preview
                    "result": last_result if 'last_result' in locals() else "Unknown"
                })
            
            current_test = match.group(1)
            test_logs = []
            # Check if result is on the same line (PASSED/FAILED)
            rem = line.split()
            if len(rem) > 1 and result_pattern.match(rem[-1]):
                last_result = rem[-1]
            else:
                last_result = "Pending"
            continue
            
        if current_test:
            if result_pattern.match(line):
                last_result = line
            else:
                test_logs.append(line)

    # Finalize last test
    if current_test:
        rows.append({
            "name": current_test,
            "logs": test_logs,
            "result": last_result
        })

    return rows

def generate_tex(rows):
    content = TEMPLATE_HEADER
    
    for row in rows:
        # Format logs for LaTeX
        clean_logs = []
        for l in row["logs"][:8]: # Limit preview
            # Escape LaTeX
            l = l.replace("_", r"\_").replace("{", r"\{").replace("}", r"\}").replace("$", r"\$").replace("&", r"\&").replace("%", r"\%")
            if len(l) > 100: l = l[:97] + "..."
            clean_logs.append(f"\\texttt{{\\scriptsize {l}}}")
        
        log_preview = r" \newline ".join(clean_logs)
        
        result_display = row["result"]
        if "FAILED" in result_display or "ERROR" in result_display:
            result_display = r"\textcolor{red}{" + result_display + "}"
        elif "PASSED" in result_display:
            result_display = r"\textcolor{green}{" + result_display + "}"

        name = row["name"].replace("_", r"\_")
        
        content += f"{name} & CS <-> CSMS & {log_preview} & {result_display} \\\\\n\\hline\n"

    content += TEMPLATE_FOOTER
    
    with open(OUTPUT_TEX, "w") as f:
        f.write(content)
    print(f"Generated LaTeX report at {OUTPUT_TEX}")

def main():
    rows = parse_logs()
    if not rows:
        print("No tests found in log.")
        return
    generate_tex(rows)

if __name__ == "__main__":
    main()
