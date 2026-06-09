#!/usr/bin/env python3
"""
Generate a LaTeX/PDF test report for MEA OCPP 1.6 Section 4.
Reads  tex/vsecc_section4_results.json  (produced by test_mea_section4.py)
Writes tex/vsecc_section4_report.tex
Compiles tex/vsecc_section4_report.pdf  (requires pdflatex)

Usage (from project root):
  python3 tests/vsecc/generate_section4_report.py
"""
import json
import os
import subprocess
import datetime

_ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_JSON = os.path.join(_ROOT, "tex", "vsecc_section4_results.json")
OUTPUT_TEX   = os.path.join(_ROOT, "tex", "vsecc_section4_report.tex")
PDF_NAME     = "vsecc_section4_report.pdf"
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


# Flow boundaries for section dividers
FLOW_HEADERS = {
    "4.1":  r"Flow 1: Hard Reset (Idle) --- Items 4.1--4.8",
    "4.9":  r"Flow 2: Soft Reset (During Charging) --- Items 4.9--4.17",
    "4.18": r"Flow 3: Hard Reset (During Charging) --- Items 4.18--4.21",
}


def build_tex(data: dict) -> str:
    results    = data["results"]
    run_ts     = data.get("timestamp", NOW)
    ev_wait    = data.get("ev_wait_sec", "?")
    boot_wait  = data.get("boot_wait_sec", "?")
    pass_n     = sum(1 for r in results if r["status"] == "PASS")
    fail_n     = sum(1 for r in results if r["status"] == "FAIL")
    warn_n     = sum(1 for r in results if r["status"] == "WARN")
    skip_n     = sum(1 for r in results if r["status"] == "SKIP")
    total      = pass_n + fail_n + warn_n + skip_n

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
\large Section 4: Reset Check\\[0.3em]
\normalsize Vector vSECC.single Board vs MEA CSMS (""" + escape(CP_ID) + r""")}
\author{MEA-V2G Project --- Automated Test}
\date{""" + NOW + r"""}

\pagestyle{fancy}
\fancyhf{}
\lhead{MEA OCPP 1.6 -- Section 4}
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
\textbf{EV Plug Timeout}   & """ + str(ev_wait) + r""" s per plug event \\
\textbf{Boot Wait Timeout} & """ + str(boot_wait) + r""" s (after Hard Reset) \\
\textbf{Test Date}         & """ + run_ts[:19].replace("T", " ") + r""" UTC \\
\textbf{Results file}      & tex/vsecc\_section4\_results.json \\
\end{tabular}

\subsection*{Test Architecture}
Reset commands are issued via the MEA REST API
(\texttt{POST /EV/remote/reset}).
Three reset scenarios are tested:
\begin{enumerate}
  \item \textbf{Hard Reset while idle (4.1--4.8):}
        Hard Reset is sent when the charger is idle.
        The vSECC reboots and sends a new BootNotification.
        A charging session is then started via RemoteStart.
  \item \textbf{Soft Reset during charging (4.9--4.17):}
        Soft Reset is sent during an active charging session.
        The vSECC should gracefully send StopTransaction before rebooting.
        A new charging session is started after recovery.
  \item \textbf{Hard Reset during charging (4.18--4.21):}
        Hard Reset is sent during an active charging session.
        The session may be aborted immediately; the vSECC reboots
        and reports Available after reconnection.
\end{enumerate}

\section{Section 4 Results}
\textbf{Summary: """ + str(pass_n) + r""" PASS \quad """ + str(fail_n) + r""" FAIL \quad """ + str(warn_n) + r""" WARN \quad """ + str(skip_n) + r""" SKIP \quad (""" + str(total) + r""" total)}

\renewcommand{\arraystretch}{1.35}
\newcolumntype{L}[1]{>{\raggedright\arraybackslash}p{#1}}
\newcolumntype{M}[1]{>{\small\ttfamily\raggedright\arraybackslash}p{#1}}
\newcolumntype{C}[1]{>{\centering\arraybackslash}p{#1}}
\begin{longtable}{|L{1cm}|L{5.5cm}|M{7cm}|C{1.8cm}|}
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
        if item in FLOW_HEADERS:
            hdr = escape(FLOW_HEADERS[item])
            tex += (r"\multicolumn{4}{|l|}{\cellcolor{gray!10}"
                    r"\small\textbf{" + hdr + r"}} \\" + "\n\\hline\n")

        item_esc   = escape(item)
        msg        = trunc(escape(r["message"]), 72)
        detail     = trunc(escape(r["detail"] or ""), 90)
        remark     = trunc(escape(r.get("remark") or ""), 80)
        cell       = status_cell(r["status"])
        detail_col = detail
        if remark:
            detail_col += r"{\newline\normalfont\footnotesize\itshape " + remark + "}"
        tex += f"\\small {item_esc} & \\small {msg} & {detail_col} & {cell} \\\\\n\\hline\n"

    tex += r"""
\end{longtable}
\normalsize

\section{Notes on Failures and Warnings}

\subsection*{Reset command}
The MEA sandbox REST API endpoint \texttt{POST /EV/remote/reset} accepts
\texttt{"reset\_type": "Hard"} or \texttt{"reset\_type": "Soft"}.
Items 4.1, 4.9, and 4.18 are PASS if the API returns HTTP~200.

\subsection*{Hard Reset reboot timing}
After a Hard Reset the vSECC physically reboots.
The test waits up to \texttt{BOOT\_WAIT\_SEC} seconds for the MQTT
\texttt{vsecc/ocpp\_connection\_status=connected} event or a successful
REST API probe to confirm reconnection.

\subsection*{EV-dependent items}
\begin{itemize}
  \item \textbf{4.4, 4.13} StatusNotification Preparing: require a physical EV
        to plug in within the \texttt{EV\_WAIT\_SEC} timeout.
  \item \textbf{4.6--4.8, 4.15--4.17} Charging session items depend on RemoteStart
        succeeding with an EV present.
\end{itemize}

\subsection*{StopTransaction on reset}
\begin{itemize}
  \item \textbf{4.10} Soft Reset: the OCPP specification requires the charger to send
        StopTransaction with \texttt{reason=SoftReset} before restarting.
  \item \textbf{4.19} Hard Reset: the specification allows the charger to immediately
        reboot without a graceful StopTransaction; session is recovered on the CSMS
        side via a timeout or explicit cleanup.
\end{itemize}

\section{Dependency Map}
\begin{tabular}{ll}
\textbf{Flow 1} & 4.1 (Reset Hard) $\rightarrow$ 4.2 (Boot) $\rightarrow$ 4.3
                  $\rightarrow$ 4.4 $\rightarrow$ 4.5 $\rightarrow$ 4.6
                  $\rightarrow$ 4.7 $\rightarrow$ 4.8 \\
\textbf{Flow 2} & 4.9 (Reset Soft) $\rightarrow$ 4.10 $\rightarrow$ 4.11
                  $\rightarrow$ 4.12 $\rightarrow$ 4.13 $\rightarrow$ 4.14
                  $\rightarrow$ 4.15 $\rightarrow$ 4.16 $\rightarrow$ 4.17 \\
\textbf{Flow 3} & 4.18 (Reset Hard) $\rightarrow$ 4.19 $\rightarrow$ 4.20
                  $\rightarrow$ 4.21 \\
\end{tabular}

\end{document}
"""
    return tex


def main():
    if not os.path.exists(RESULTS_JSON):
        print(f"ERROR: {RESULTS_JSON} not found.  Run test_mea_section4.py first.")
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
