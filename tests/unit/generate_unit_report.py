import re
import os
import sys

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
    # Match outcome at the end OR before progress indicators like [ 4%]
    result_pattern = re.compile(r".*\s(PASSED|FAILED|ERROR|SKIPPED)(\s+\[.*\])?$")
    
    # Store results in a dictionary
    test_results = {} # name -> result
    test_logs = {}    # name -> list of log lines

    # Phase 1: Scan for test results
    for line in log_lines:
        line = line.strip()
        match = test_start_pattern.match(line)
        if match:
            test_name = match.group(1)
            # Check for result on the same line
            res_match = result_pattern.match(line)
            if res_match:
                test_results[test_name] = res_match.group(1)
            else:
                test_results[test_name] = "Pending"
    
    # Phase 2: Scan for captured logs in "PASSES" or "FAILURES" sections
    # Header format: __________________ TestEvseStates.test_initial_state __________________
    header_pattern = re.compile(r"^_+ \w+\.(test_\w+) _+$")
    current_log_test = None
    
    capturing = False
    for line in log_lines:
        sline = line.strip()
        
        # Check for start of captured output section
        if "=== PASSES ===" in line or "=== FAILURES ===" in line or "=== ERRORS ===" in line:
            capturing = True
            continue
            
        if not capturing:
            continue
            
        # Check for test header
        match = header_pattern.match(sline)
        if match:
            current_log_test = match.group(1)
            if current_log_test not in test_logs:
                test_logs[current_log_test] = []
            continue
            
        # Stop capturing if we hit another section or end
        if sline.startswith("=======") and "summary" in sline:
            capturing = False
            current_log_test = None
            continue
            
        if current_log_test:
            # Skip "Captured stdout call" divider
            if "Captured stdout" in sline or "Captured stderr" in sline:
               continue
            if sline:
               test_logs[current_log_test].append(sline)

    # Combine results
    for name, result in test_results.items():
        rows.append({
            "name": name,
            "logs": test_logs.get(name, [])[:12],
            "result": result
        })
        
    # Enrich with Test IDs from source file
    test_ids = parse_test_ids()
    for row in rows:
        row["id"] = test_ids.get(row["name"], "-")
        
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
            l = l.replace("\\", r"\textbackslash{}").replace("_", r"\_").replace("{", r"\{").replace("}", r"\}").replace("$", r"\$").replace("&", r"\&").replace("%", r"\%").replace("#", r"\#")
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

def ensure_logs_exist():
    # If log file doesn't exist, try to generate it
    # Always regenerate to ensure we have the latest format (-rP)
    # if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
    if True: 
        print(f"Running pytest to generate logs...")
        print(f"Using python: {sys.executable}")
        # Run pytest and capture stdout to LOG_FILE
        # Using subprocess to run pytest
        import subprocess
        try:
            # We use tee or just redirection. simpler to just run and redirect
            # Note: pytest output goes to stderr/stdout. 
            # We want to run: sys.executable -m pytest -v -rP tests/unit/test_evse_states.py > tests/unit/unit_test_output.log
            # But capturing it in python:
            cmd = [sys.executable, "-m", "pytest", "-v", "-rP", "tests/unit/test_evse_states.py"]
            print(f"Running command: {cmd}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False
            )
            
            print(f"Return code: {result.returncode}")
            print(f"Stdout len: {len(result.stdout)}")
            print(f"Stderr len: {len(result.stderr)}")
            
            # Combine stdout and stderr
            output = result.stdout + "\n" + result.stderr
            
            with open(LOG_FILE, "w") as f:
                f.write(output)

            # if len(result.stderr) > 0:
            #    print(f"Stderr output:\n{result.stderr[:200]}...")

        except Exception as e:
            print(f"Failed to run pytest: {e}")

    # Also make sure it is in the tex directory for the latex report
    # The latex file expects unit_test_output.log in the same directory usually, 
    # or we should copy it to where the latex build happens.
    # The latex header says: \lstinputlisting[...]{unit_test_output.log}
    # It assumes it is in the same dir as the .tex file (tex/) or in the search path.
    # Let's copy it to tex/unit_test_output.log
    
    target_log = os.path.join(os.path.dirname(OUTPUT_TEX), "unit_test_output.log")
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 0:
        import shutil
        shutil.copy(LOG_FILE, target_log)
        print(f"Copied log to {target_log}")
    else:
        print(f"Warning: Could not create {target_log} because source is missing or empty.")

def main():
    ensure_logs_exist()
    rows = parse_logs()
    if not rows:
        print("No tests found in log.")
        return
    generate_tex(rows)

if __name__ == "__main__":
    main()
