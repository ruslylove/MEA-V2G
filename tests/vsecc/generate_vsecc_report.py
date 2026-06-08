#!/usr/bin/env python3
"""
Generate a LaTeX test report for the vSECC OCPP compliance test.
Usage: python3 generate_vsecc_report.py
Outputs: tex/vsecc_ocpp_report.tex  and  tex/vsecc_ocpp_test.log
"""

import subprocess
import re
import os
import sys
import datetime

TEST_SCRIPT = "tests/system/test_vsecc_ocpp.py"
PROXY_SCRIPT = "tests/system/ws_proxy.py"
LOG_FILE    = "tex/vsecc_ocpp_test.log"
OUTPUT_TEX  = "tex/vsecc_ocpp_report.tex"

PYTHON = "/home/ruslee/Development/FreeV2G/.venv/bin/python3"
if not os.path.exists(PYTHON):
    PYTHON = sys.executable

RUN_DATE = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

TEMPLATE_HEADER = r"""\documentclass[10pt, a4paper]{article}
\usepackage[left=2cm, right=2cm, top=2.5cm, bottom=2.5cm]{geometry}
\usepackage{longtable}
\usepackage{array}
\usepackage{xcolor}
\usepackage{colortbl}
\usepackage{fancyhdr}
\usepackage{booktabs}
\usepackage{verbatim}
\usepackage{listings}
\usepackage[hidelinks]{hyperref}

\definecolor{passgreen}{RGB}{34,139,34}
\definecolor{failred}{RGB}{200,0,0}
\definecolor{skipgray}{RGB}{120,120,120}

\title{\textbf{vSECC OCPP 1.6 Compliance Test Report}\\[0.5em]
\large Vector vSECC.single Board vs MEA CSMS (rddQC4000001)}
\author{MEA-V2G Project --- Automated Test}
\date{""" + RUN_DATE + r"""}

\pagestyle{fancy}
\fancyhf{}
\lhead{vSECC OCPP 1.6 Compliance Report}
\rhead{Page \thepage}

\begin{document}
\maketitle
\tableofcontents
\newpage

\section{Test Configuration}
\begin{tabular}{ll}
\textbf{Device Under Test} & Vector vSECC.single Board \\
\textbf{Device IP} & 192.168.1.166 \\
\textbf{OCPP Version} & 1.6 (JSON) \\
\textbf{CP ID} & rddQC4000001 \\
\textbf{CSMS} & wss://ocpp.measandbox.com:2930 \\
\textbf{Test PC} & 192.168.1.100 (WS proxy) \\
\textbf{Test Date} & """ + RUN_DATE + r""" \\
\end{tabular}

\subsection*{Test Setup}
The vSECC connects to a local WebSocket proxy on the test PC (\texttt{ws://192.168.1.100:9000}).
The proxy transparently forwards all OCPP frames to the MEA CSMS over TLS
(\texttt{wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6/rddQC4000001}).
OCPP extension messages unsupported by MEA (e.g.\ \texttt{SecurityEventNotification})
are intercepted by the proxy and answered with an empty ACK to maintain the session.

\section{Test Results Summary}

\renewcommand{\arraystretch}{1.3}
\begin{longtable}{|c|p{6cm}|p{5cm}|c|}
\hline
\rowcolor{gray!30}
\textbf{\#} & \textbf{Test Item} & \textbf{Detail} & \textbf{Result} \\
\hline
\endfirsthead
\hline
\rowcolor{gray!30}
\textbf{\#} & \textbf{Test Item} & \textbf{Detail} & \textbf{Result} \\
\hline
\endhead
\hline
\endfoot
"""

TEMPLATE_FOOTER = r"""
\end{longtable}

\section{OCPP Message Trace (Proxy Log)}
\label{sec:proxy_log}
\lstset{
    basicstyle=\small\ttfamily,
    breaklines=true,
    columns=fullflexible,
    keepspaces=true,
    frame=single,
    numbers=left,
    numberstyle=\tiny\color{gray},
    stepnumber=1,
    numbersep=5pt
}
\lstinputlisting{vsecc_ocpp_test.log}

\end{document}
"""


def run_proxy_if_needed():
    """Start proxy if not already running on port 9000."""
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", 9000), timeout=1)
        s.close()
        print("  Proxy already running on :9000")
        return None
    except Exception:
        pass
    import subprocess as sp
    proc = sp.Popen([PYTHON, PROXY_SCRIPT],
                    stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    import time; time.sleep(2)
    print(f"  Proxy started (PID {proc.pid})")
    return proc


def run_test():
    print(f"Running {TEST_SCRIPT}...")
    ansi = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    try:
        result = subprocess.run(
            [PYTHON, TEST_SCRIPT],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True
        )
        clean = ansi.sub('', result.stdout)
        clean = re.sub(r'.*DeprecationWarning.*\n?', '', clean)
        clean = re.sub(r'.*Callback API version.*\n?', '', clean)
        os.makedirs("tex", exist_ok=True)
        with open(LOG_FILE, "w") as f:
            f.write(clean)
        print(f"  Log saved to {LOG_FILE}")
        return clean.splitlines()
    except Exception as e:
        print(f"Error running test: {e}")
        return []


def parse_results(lines):
    rows = []
    current_section = ""
    counter = 0

    section_re = re.compile(r'^\s*[─]+\s*$')
    title_re   = re.compile(r'^\s+(\d+)\.\s+(.+?)\s*$')
    result_re  = re.compile(r'^\s+\[(PASS|FAIL|SKIP)\]\s+(.+?)(?:\s+\((.+)\))?\s*$')

    i = 0
    while i < len(lines):
        line = lines[i]
        # Section separator followed by title
        if section_re.match(line) and i + 1 < len(lines):
            title_match = title_re.match(lines[i + 1])
            if title_match:
                current_section = lines[i + 1].strip()
                rows.append({"type": "section", "title": current_section})
                i += 2
                continue
        # Result line
        m = result_re.match(line)
        if m:
            status, item, detail = m.group(1), m.group(2), m.group(3) or ""
            counter += 1
            rows.append({
                "type": "row",
                "num": counter,
                "section": current_section,
                "item": item,
                "detail": detail,
                "status": status
            })
        i += 1
    return rows


def escape(s):
    for ch, rep in [("\\","\\\\"),("{","\\{"),("}", "\\}"),
                    ("_","\\_"),("#","\\#"),("&","\\&"),
                    ("%","\\%"),("$","\\$"),("~","\\textasciitilde{}"),
                    ("^","\\textasciicircum{}")]:
        s = s.replace(ch, rep)
    return s


def generate_tex(rows, log_lines):
    pass_count = sum(1 for r in rows if r["type"] == "row" and r["status"] == "PASS")
    fail_count = sum(1 for r in rows if r["type"] == "row" and r["status"] == "FAIL")
    skip_count = sum(1 for r in rows if r["type"] == "row" and r["status"] == "SKIP")
    total = pass_count + fail_count

    content = TEMPLATE_HEADER

    # Summary stats block
    content += f"""
\\hline
\\multicolumn{{4}}{{|c|}}{{\\cellcolor{{gray!15}}
  \\textbf{{Summary: {pass_count}/{total} PASS \\quad {fail_count} FAIL \\quad {skip_count} SKIP}}}} \\\\
\\hline
"""

    prev_section = ""
    for row in rows:
        if row["type"] == "section":
            title = escape(row["title"])
            content += (f"\\multicolumn{{4}}{{|l|}}{{\\cellcolor{{blue!8}}"
                        f"\\textbf{{{title}}}}} \\\\\n\\hline\n")
            prev_section = row["title"]
        else:
            status = row["status"]
            if status == "PASS":
                color = r"\textcolor{passgreen}{\textbf{PASS}}"
            elif status == "FAIL":
                color = r"\textcolor{failred}{\textbf{FAIL}}"
            else:
                color = r"\textcolor{skipgray}{SKIP}"
            item   = escape(row["item"])
            detail = escape(row["detail"])
            content += f"{row['num']} & {item} & \\texttt{{{detail}}} & {color} \\\\\n\\hline\n"

    content += TEMPLATE_FOOTER

    os.makedirs("tex", exist_ok=True)
    with open(OUTPUT_TEX, "w") as f:
        f.write(content)
    print(f"LaTeX report saved to {OUTPUT_TEX}")
    print(f"Result: {pass_count} PASS / {fail_count} FAIL / {skip_count} SKIP  ({total} total)")


def main():
    print("=== vSECC OCPP Report Generator ===")
    proxy_proc = run_proxy_if_needed()
    log_lines = run_test()
    if proxy_proc:
        proxy_proc.terminate()
    if not log_lines:
        print("No output captured.")
        return
    rows = parse_results(log_lines)
    generate_tex(rows, log_lines)
    print(f"\nTo compile: cd tex && pdflatex vsecc_ocpp_report.tex")


if __name__ == "__main__":
    main()
