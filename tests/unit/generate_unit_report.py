import re
import os

# Configuration
LOG_FILE = "tests/unit/unit_test_output.log"
OUTPUT_TEX = "tex/evse_unit_test_report.tex"
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
\usepackage{tikz}
\usetikzlibrary{automata, positioning, arrows}

\title{\textbf{Evse.py Unit Test Report}}
\author{MEA OCPP Certification (Automated)}
\date{\today}

\pagestyle{fancy}
\fancyhf{}
\lhead{MEA Evse State Machine Verification}
\rhead{Page \thepage}

\begin{document}

\maketitle

\section*{State Transition Diagram}
\begin{center}
\begin{tikzpicture}[>=stealth, node distance=3.5cm, on grid, auto, initial text=Test 1]
  \node[state, initial] (unavailable) {Unavailable};
  \node[state] (available) [right=of unavailable] {Available};
  \node[state] (reserved) [below=of unavailable] {Reserved};
  \node[state] (preparing) [right=of available] {Preparing};
  \node[state] (charging) [right=of preparing] {Charging};
  \node[state] (suspendedev) [above right=5cm of charging] {SuspendedEV};
  \node[state] (suspendedevse) [below right=of charging] {SuspEVSE};
  \node[state] (faulted) [below=of charging] {Faulted};
  \node[state] (finishing) [above=of charging] {Finishing};

  \path[->]
    (unavailable) edge [bend left] node {2,12} (available)
    (available) edge [bend left] node {11} (unavailable)
    
    (unavailable) edge node [left] {9} (reserved)
    (available) edge node {13} (reserved)
    (reserved) edge [bend right] node [swap] {14} (available)
    (reserved) edge [bend right] node [swap] {15} (preparing)
    
    (available) edge node {3} (preparing)
    (preparing) edge node {4} (charging)
    (preparing) edge [bend right] node [swap] {21} (faulted)
    
    (charging) edge [bend left] node {5} (suspendedev)
    (suspendedev) edge [bend left] node {16} (charging)
    (suspendedev) edge [bend left] node {18} (finishing)
    
    (charging) edge [bend right] node [swap] {6} (suspendedevse)
    (suspendedevse) edge [bend right] node [swap] {17} (charging)
    (suspendedevse) edge [bend right] node [swap] {19} (finishing)
    
    (charging) edge node {7} (finishing)
    (charging) edge node {8} (faulted)
    
    (finishing) edge [bend right=45] node [swap] {10} (unavailable)
    (finishing) edge node {20} (available)
    
    (faulted) edge [bend left] node {22} (available)
    (faulted) edge [bend left] node {23} (unavailable);
\end{tikzpicture}
\end{center}
\vspace{1cm}

\section*{Unit Test Execution Results}

\renewcommand{\arraystretch}{1.3}
\begin{longtable}{|c|p{6cm}|p{2.5cm}|p{8cm}|c|}
\hline
\rowcolor{gray!30}
\textbf{ID} & \textbf{Test Case} & \textbf{State Check} & \textbf{Log Preview / Trace} & \textbf{Result} \\
\hline
\endfirsthead

\hline
\hline
\rowcolor{gray!30}
\textbf{ID} & \textbf{Test Case} & \textbf{State Check} & \textbf{Log Preview / Trace} & \textbf{Result} \\
\hline
\endhead

\hline
\endfoot
"""

TEMPLATE_FOOTER = r"""
\end{longtable}
\newpage
\appendix
\section{Raw Unit Test Logs}
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
]{unit_test_output.log}

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
    
    # Pytest output format for unittest: 
    # tests/unit/test_evse_states.py::TestEvseStates::test_initial_state PASSED
    # or sometimes on multiple lines if -v is used (which we did)
    test_start_pattern = re.compile(r"^tests/unit/test_evse_states.py::TestEvseStates::(\w+)")
    result_pattern = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED)$")

    for line in log_lines:
        line = line.strip()
        
        match = test_start_pattern.match(line)
        if match:
            # If we had a previous test, finalize it
            if current_test:
                rows.append({
                    "name": current_test,
                    "logs": test_logs[:12], # Keep preview
                    "result": last_result if 'last_result' in locals() else "Unknown"
                })
            
            current_test = match.group(1)
            test_logs = []
            
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
                # Capture transition logs and other EVSE outputs
                # Exclude purely empty lines or pytest noise if possible
                if line and not line.startswith("test_") and "::" not in line and "plugins:" not in line:
                     test_logs.append(line)

    # Finalize last test
    if current_test:
        rows.append({
            "name": current_test,
            "logs": test_logs[:12],
            "result": last_result
        })
        
    # Enrich with Test IDs from source file
    test_ids = parse_test_ids()
    for row in rows:
        row["id"] = test_ids.get(row["name"], "-")
        # Try to sort numerically if possible for display?
        pass
        
    # Sort rows by ID logic
    def get_sort_key(r):
        try:
            return int(r["id"])
        except:
            return 999
    
    rows.sort(key=get_sort_key)

    return rows

def parse_test_ids():
    ids = {}
    with open("tests/unit/test_evse_states.py", "r") as f:
        content = f.read()
    
    # regex for def test_name(self): ... """Test X: ..."""
    # or simpler line by line
    current_def = None
    for line in content.splitlines():
        def_match = re.search(r"def (test_\w+)\(self\):", line)
        if def_match:
            current_def = def_match.group(1)
        
        if current_def and '"""Test' in line:
            id_match = re.search(r'Test (\d+):', line)
            if id_match:
                ids[current_def] = id_match.group(1)
                current_def = None
    return ids

def generate_tex(rows):
    content = TEMPLATE_HEADER
    
    for row in rows:
        # Format logs for LaTeX
        clean_logs = []
        for l in row["logs"]:
            # Escape LaTeX
            l = l.replace("_", r"\_").replace("{", r"\{").replace("}", r"\}").replace("$", r"\$").replace("&", r"\&").replace("%", r"\%")
            if len(l) > 110: l = l[:107] + "..."
            clean_logs.append(f"\\texttt{{\\scriptsize {l}}}")
        
        log_preview = r" \newline ".join(clean_logs)
        
        result_display = row["result"]
        if "FAILED" in result_display or "ERROR" in result_display:
            result_display = r"\textcolor{red}{" + result_display + "}"
        elif "PASSED" in result_display:
            result_display = r"\textcolor{green}{" + result_display + "}"

        name = row["name"].replace("_", r"\_")
        test_id = row["id"]
        
        content += f"{test_id} & {name} & Logic Check & {log_preview} & {result_display} \\\\\n\\hline\n"

    content += TEMPLATE_FOOTER
    
    with open(OUTPUT_TEX, "w") as f:
        f.write(content)
    print(f"Generated LaTeX report at {OUTPUT_TEX}")

def main():
    rows = parse_logs()
    if not rows:
        print("No tests found in log.")
        # Debug printing
        if os.path.exists(LOG_FILE):
             print("Log file content preview:")
             with open(LOG_FILE) as f:
                 print(f.read()[:500])
        return
    generate_tex(rows)

if __name__ == "__main__":
    main()
