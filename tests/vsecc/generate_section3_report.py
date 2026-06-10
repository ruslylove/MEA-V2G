#!/usr/bin/env python3
"""
Generate a LaTeX/PDF test report for MEA OCPP 1.6 Section 3.
Reads  tex/vsecc_section3_results.json  (produced by test_mea_section3.py)
Writes tex/vsecc_section3_report.tex
Compiles tex/vsecc_section3_report.pdf  (requires pdflatex)

Usage (from project root):
  python3 tests/vsecc/generate_section3_report.py
"""
import json
import os
import subprocess
import datetime

_ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_JSON = os.path.join(_ROOT, "tex", "vsecc_section3_results.json")
OUTPUT_TEX   = os.path.join(_ROOT, "tex", "vsecc_section3_report.tex")
PDF_NAME     = "vsecc_section3_report.pdf"
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


def build_tex(data: dict) -> str:
    results      = data["results"]
    run_ts       = data.get("timestamp", NOW)
    ev_wait      = data.get("ev_wait_sec", "?")
    local_wait   = data.get("local_stop_wait_sec", "?")
    pass_n       = sum(1 for r in results if r["status"] == "PASS")
    fail_n       = sum(1 for r in results if r["status"] == "FAIL")
    warn_n       = sum(1 for r in results if r["status"] == "WARN")
    skip_n       = sum(1 for r in results if r["status"] == "SKIP")
    total        = pass_n + fail_n + warn_n + skip_n

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
\large Section 3: Normal Operation Check\\[0.3em]
\normalsize Vector vSECC.single Board vs MEA CSMS (""" + escape(CP_ID) + r""")}
\author{MEA-V2G Project --- Automated Test}
\date{""" + NOW + r"""}

\pagestyle{fancy}
\fancyhf{}
\lhead{MEA OCPP 1.6 -- Section 3}
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
\textbf{Local Stop Timeout} & """ + str(local_wait) + r""" s (RFID card tap) \\
\textbf{Test Date}         & """ + run_ts[:19].replace("T", " ") + r""" UTC \\
\textbf{Results file}      & tex/vsecc\_section3\_results.json \\
\end{tabular}

\subsection*{Test Architecture}
The test runs directly against the MEA CSMS with no proxy.
Two charging sessions are tested:
\begin{itemize}
  \item \textbf{Manual session (3.1--3.10):} EV plugged in; driver taps RFID card to
        authorize and start; driver taps card again to stop locally.
  \item \textbf{Remote session (3.11--3.19):} EV plugged in; CSMS sends RemoteStartTransaction
        via MEA REST API; CSMS sends RemoteStopTransaction to stop.
\end{itemize}
MQTT subscription to \texttt{mqtt://192.168.1.166:1883} is used to observe all
StatusNotification, Authorize, StartTransaction, StopTransaction, and MeterValues events.

\section{Section 3 Results}
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

    # Section dividers in table
    manual_items  = {"3.1","3.2","3.3","3.4","3.5","3.6","3.7","3.8","3.9","3.10"}
    remote_items  = {"3.11","3.12","3.13","3.14","3.15","3.16","3.17","3.18","3.19"}
    first_manual  = True
    first_remote  = True

    for r in results:
        item = r["item"]
        if item in manual_items and first_manual:
            tex += r"\multicolumn{4}{|l|}{\cellcolor{gray!10}\small\textbf{Manual Operation Flow (3.1--3.10)}} \\" + "\n\\hline\n"
            first_manual = False
        if item in remote_items and first_remote:
            tex += r"\multicolumn{4}{|l|}{\cellcolor{gray!10}\small\textbf{Remote Operation Flow (3.11--3.19)}} \\" + "\n\\hline\n"
            first_remote = False

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

\section{Notes on Failures and Warnings}

\subsection*{EV-dependent items}
\begin{itemize}
  \item \textbf{3.3, 3.11} StatusNotification Preparing: require a physical EV to
        plug into the vSECC connector within the \texttt{EV\_WAIT\_SEC} timeout.
  \item \textbf{3.4--3.10, 3.13--3.19} All session items depend on the EV being
        connected in items 3.3 / 3.11 respectively.
\end{itemize}

\subsection*{Item 3.8 — Manual stop}
Item 3.8 tests that the EV driver can tap their RFID card on the vSECC to trigger a
local \texttt{StopTransaction} with \texttt{reason=Local}.
If no card tap is detected within \texttt{LOCAL\_STOP\_WAIT\_SEC} seconds, the test
falls back to RemoteStop to clear the session so the remote-session phase can proceed.
The fallback is recorded as \textbf{WARN} with a remark.

\subsection*{Items 3.7, 3.15 — MeterValues}
The MEA sandbox REST API returns HTTP~404 for TriggerMessage.
MeterValues are observable only when the vSECC autonomously publishes them to MQTT
based on the \texttt{MeterValueSampleInterval} configuration (set in Section~1 item~1.11).

\subsection*{Items 3.12, 3.16 — RemoteStart / RemoteStop}
\begin{itemize}
  \item \textbf{3.12} Uses a dedicated password (\texttt{MEA\_PASS\_START}) for the
        \texttt{/EV/cmd/chargepoint/remoteStart} endpoint.
  \item \textbf{3.16} RemoteStop with \texttt{transaction\_id=0} succeeds when exactly one
        transaction is active; FAIL if no active transaction exists.
\end{itemize}

\section{Dependency Map}
\begin{tabular}{ll}
\textbf{Manual} & 3.3 $\rightarrow$ 3.4 $\rightarrow$ 3.5 $\rightarrow$ 3.6
                  $\rightarrow$ 3.7 $\rightarrow$ 3.8 $\rightarrow$ 3.9 $\rightarrow$ 3.10 \\
\textbf{Remote} & 3.11 $\rightarrow$ 3.12 $\rightarrow$ 3.13 $\rightarrow$ 3.14
                  $\rightarrow$ 3.15 $\rightarrow$ 3.16 $\rightarrow$ 3.17
                  $\rightarrow$ 3.18 $\rightarrow$ 3.19 \\
\end{tabular}

\medskip
\noindent Items 3.1 and 3.2 are independent of EV presence.

\end{document}
"""
    return tex


def main():
    if not os.path.exists(RESULTS_JSON):
        print(f"ERROR: {RESULTS_JSON} not found.  Run test_mea_section3.py first.")
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
