#!/usr/bin/env python3
"""
Generate a LaTeX/PDF test report for MEA OCPP 1.6 Section 8.
Reads  tex/vsecc_section8_results.json  (produced by test_mea_section8.py)
Writes tex/vsecc_section8_report.tex
Compiles tex/vsecc_section8_report.pdf  (requires pdflatex)

Usage (from project root):
  python3 tests/vsecc/generate_section8_report.py
"""
import json
import os
import subprocess
import datetime

_ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_JSON = os.path.join(_ROOT, "tex", "vsecc_section8_results.json")
OUTPUT_TEX   = os.path.join(_ROOT, "tex", "vsecc_section8_report.tex")
PDF_NAME     = "vsecc_section8_report.pdf"
TEX_DIR      = os.path.join(_ROOT, "tex")

CP_ID = "rddQC4000001"
NOW   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def escape(s: str) -> str:
    for ch, rep in [("\\", "\\\\"), ("{", "\\{"), ("}", "\\}"),
                    ("_", "\\_"), ("#", "\\#"), ("&", "\\&"),
                    ("%", "\\%"), ("$", "\\$"),
                    ("~", "\\textasciitilde{}"),
                    ("^", "\\textasciicircum{}")]:
        s = s.replace(ch, rep)
    return s


def trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + r"\ldots{}"


def status_cell(status: str) -> str:
    if status == "PASS":
        return r"\textcolor{passgreen}{\textbf{PASS}}"
    elif status == "FAIL":
        return r"\textcolor{failred}{\textbf{FAIL}}"
    elif status == "WARN":
        return r"\textcolor{warnorange}{\textbf{WARN}}"
    else:
        return r"\textcolor{skipgray}{SKIP}"


# Sub-section dividers
SUBSECTION_HEADERS = {
    "8.1.1": r"8.1 Concurrent RemoteStart --- Dual Connectors",
    "8.2.1": r"8.2 Shared Emergency Stop --- Dual Connectors",
    "8.3.1": r"8.3 Power Loss --- Dual Connectors",
}


def build_tex(data: dict) -> str:
    results   = data["results"]
    run_ts    = data.get("timestamp", NOW)
    pass_n    = sum(1 for r in results if r["status"] == "PASS")
    fail_n    = sum(1 for r in results if r["status"] == "FAIL")
    warn_n    = sum(1 for r in results if r["status"] == "WARN")
    skip_n    = sum(1 for r in results if r["status"] == "SKIP")
    total     = pass_n + fail_n + warn_n + skip_n

    tex = r"""\documentclass[10pt, a4paper]{article}
\usepackage[left=2cm, right=2cm, top=2.5cm, bottom=2.5cm]{geometry}
\usepackage{longtable}
\usepackage{array}
\usepackage{xcolor}
\usepackage{colortbl}
\usepackage{fancyhdr}
\usepackage{booktabs}
\usepackage[hidelinks]{hyperref}
\setlength{\emergencystretch}{3em}

\definecolor{passgreen}{RGB}{34,139,34}
\definecolor{failred}{RGB}{200,0,0}
\definecolor{warnorange}{RGB}{200,100,0}
\definecolor{skipgray}{RGB}{120,120,120}

\title{\textbf{MEA OCPP 1.6 Compliance Test Report}\\[0.3em]
\large Section 8: Dual Connector Verification\\[0.3em]
\normalsize Vector vSECC.single Board vs MEA CSMS (""" + escape(CP_ID) + r""")}
\author{MEA-V2G Project --- Automated Test}
\date{""" + NOW + r"""}

\pagestyle{fancy}
\fancyhf{}
\lhead{MEA OCPP 1.6 -- Section 8}
\rhead{Page \thepage}

\begin{document}
\maketitle
\tableofcontents
\newpage

\section{Test Configuration}
\begin{tabular}{ll}
\textbf{Device Under Test} & Vector vSECC.single Board \\
\textbf{Device IP}         & 192.168.1.166 \\
\textbf{OCPP Version}      & 1.6 (JSON) \\
\textbf{CP ID}             & """ + escape(CP_ID) + r""" \\
\textbf{CSMS}              & wss://ocpp.measandbox.com:2930 \\
\textbf{Connection}        & Direct (no proxy) \\
\textbf{Test PC}           & 192.168.111.185 (alias 192.168.1.200/24 on enp3s0) \\
\textbf{Test Date}         & """ + run_ts[:19].replace("T", " ") + r""" UTC \\
\textbf{Results file}      & tex/vsecc\_section8\_results.json \\
\end{tabular}

\section{Applicability Note}

\begin{center}
\large\textbf{Section 8 is NOT APPLICABLE to this device.}
\end{center}

\noindent
The \textbf{Vector vSECC.single} is a \emph{single-connector} SECC (Supply Equipment
Communication Controller). Section~8 of the MEA OCPP~1.6 compliance form requires
\textbf{two independent connectors} operating simultaneously to test:
\begin{itemize}
  \item \textbf{8.1} Concurrent RemoteStart on both connectors at the same time
  \item \textbf{8.2} A shared Emergency Stop signal that simultaneously terminates
        active sessions on both connectors
  \item \textbf{8.3} Power loss behaviour with two concurrent charging sessions
\end{itemize}
Since the vSECC.single hardware provides only one connector (Connector~1), none of
these dual-connector scenarios can be executed or observed. All 50 items in
Section~8 are therefore recorded as \textbf{SKIP} — Not Applicable.

\medskip\noindent
If a multi-connector SECC (e.g.\ vSECC.dual or another charger with two connectors)
is used in the future, all items in this section should be re-executed on that device.

\section{Section 8 Results}
\textbf{Summary: """ + str(pass_n) + r""" PASS \quad """ + str(fail_n) + r""" FAIL \quad """ + str(warn_n) + r""" WARN \quad """ + str(skip_n) + r""" SKIP \quad (""" + str(total) + r""" total)}

\renewcommand{\arraystretch}{1.35}
\newcolumntype{L}[1]{>{\raggedright\arraybackslash}p{#1}}
\newcolumntype{M}[1]{>{\small\ttfamily\raggedright\arraybackslash}p{#1}}
\newcolumntype{C}[1]{>{\centering\arraybackslash}p{#1}}
\begin{longtable}{|L{1.2cm}|L{6cm}|M{6.3cm}|C{1.8cm}|}
\hline
\rowcolor{gray!25}
\textbf{Item} & \small\textbf{Test Description} & \small\textbf{Detail / Evidence} & \small\textbf{Result} \\
\hline
\endfirsthead
\hline
\rowcolor{gray!25}
\textbf{Item} & \small\textbf{Test Description} & \small\textbf{Detail / Evidence} & \small\textbf{Result} \\
\hline
\endhead
\hline
\endfoot
"""

    for r in results:
        item = r["item"]
        if item in SUBSECTION_HEADERS:
            hdr = escape(SUBSECTION_HEADERS[item])
            tex += (r"\multicolumn{4}{|l|}{\cellcolor{gray!10}"
                    r"\small\textbf{" + hdr + r"}} \\" + "\n\\hline\n")

        item_esc   = escape(item)
        msg        = trunc(escape(r["message"]), 72)
        detail     = trunc(escape(r["detail"] or ""), 90)
        remark     = trunc(escape(r.get("remark") or ""), 80)
        cell       = status_cell(r["status"])
        raw        = trunc(escape(r.get("raw") or ""), 160)
        if raw == '""': raw = ""
        detail_col = detail
        if remark:
            detail_col += r"{\newline\normalfont\footnotesize\itshape " + remark + "}"
        if raw:
            detail_col += r"{\newline\color{gray!70}\scriptsize\ttfamily " + raw + "}"
        tex += f"\\small {item_esc} & \\small {msg} & {detail_col} & {cell} \\\\\n\\hline\n"

    tex += r"""
\end{longtable}
\normalsize

\section{Notes}

\subsection*{Why all items are SKIP}
The Vector vSECC.single board exposes exactly \textbf{one} OCPP connector.
Dual-connector tests require two simultaneously active EVSE connectors so that:
\begin{enumerate}
  \item Two independent charging sessions can be started concurrently (Section 8.1)
  \item An emergency stop can interrupt both sessions at the same time (Section 8.2)
  \item A power-loss event can be observed across two active sessions (Section 8.3)
\end{enumerate}
None of these conditions can be reproduced on a single-connector device.
Attempting to run these tests would require a different hardware platform.

\subsection*{Impact on compliance}
The MEA compliance form classifies dual-connector scenarios as a separate hardware
configuration class. Single-connector installations are not expected to satisfy
Section~8. The SKIP status (Not Applicable) indicates that the test was reviewed
and intentionally excluded rather than omitted by error.

\subsection*{Recommended future action}
Should a multi-connector vSECC variant become available for integration testing,
re-run \texttt{test\_mea\_section8.py} against that device after removing the
\texttt{SKIP} guards and implementing the two-connector session orchestration logic.

\end{document}
"""
    return tex


def main():
    if not os.path.exists(RESULTS_JSON):
        print(f"ERROR: {RESULTS_JSON} not found.  Run test_mea_section8.py first.")
        return

    with open(RESULTS_JSON) as f:
        data = json.load(f)

    tex_body = build_tex(data)
    os.makedirs(TEX_DIR, exist_ok=True)
    with open(OUTPUT_TEX, "w") as f:
        f.write(tex_body)
    print(f"LaTeX written to {OUTPUT_TEX}")

    tex_filename = os.path.basename(OUTPUT_TEX)
    for _ in range(2):
        r = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", tex_filename],
            cwd=TEX_DIR, capture_output=True, errors="replace"
        )
    pdf_path = os.path.join(TEX_DIR, PDF_NAME)
    if os.path.exists(pdf_path):
        size = os.path.getsize(pdf_path)
        print(f"PDF compiled: {pdf_path}  ({size // 1024} KB)")
    else:
        print("pdflatex failed.  Check tex/*.log for errors.")
        print(r.stdout[-1000:])


if __name__ == "__main__":
    main()
