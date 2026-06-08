#!/usr/bin/env python3
"""
Generate a LaTeX/PDF test report for MEA OCPP 1.6 Section 2.
Reads  tex/vsecc_section2_results.json  (produced by test_mea_section2.py)
Writes tex/vsecc_section2_report.tex
Compiles tex/vsecc_section2_report.pdf  (requires pdflatex)

Usage (from project root):
  python3 tests/vsecc/generate_section2_report.py
"""
import json
import os
import subprocess
import datetime

_ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_JSON = os.path.join(_ROOT, "tex", "vsecc_section2_results.json")
OUTPUT_TEX   = os.path.join(_ROOT, "tex", "vsecc_section2_report.tex")
PDF_NAME     = "vsecc_section2_report.pdf"
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
    results  = data["results"]
    run_ts   = data.get("timestamp", NOW)
    ev_wait  = data.get("ev_wait_sec", "?")
    pass_n   = sum(1 for r in results if r["status"] == "PASS")
    fail_n   = sum(1 for r in results if r["status"] == "FAIL")
    warn_n   = sum(1 for r in results if r["status"] == "WARN")
    skip_n   = sum(1 for r in results if r["status"] == "SKIP")
    total    = pass_n + fail_n + warn_n + skip_n

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
\large Section 2: Auto Charge Verification\\[0.3em]
\normalsize Vector vSECC.single Board vs MEA CSMS (""" + escape(CP_ID) + r""")}
\author{MEA-V2G Project --- Automated Test}
\date{""" + NOW + r"""}

\pagestyle{fancy}
\fancyhf{}
\lhead{MEA OCPP 1.6 -- Section 2}
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
\textbf{EV Wait Timeout}   & """ + str(ev_wait) + r""" s per plug event \\
\textbf{Test Date}         & """ + run_ts[:19].replace("T", " ") + r""" UTC \\
\textbf{Results file}      & tex/vsecc\_section2\_results.json \\
\end{tabular}

\subsection*{Test Architecture}
The test runs directly against the MEA CSMS with no proxy.
The test PC:
\begin{itemize}
  \item Calls the vSECC REST API (\texttt{http://192.168.1.166/api}) for configuration and status
  \item Subscribes to the vSECC MQTT broker (\texttt{mqtt://192.168.1.166:1883}) to observe
        StatusNotification, Authorize, Start/StopTransaction, and MeterValues events
  \item Calls the MEA sandbox REST API (\texttt{https://ocppapi.measandbox.com/EV/})
        with HTTP Digest authentication for ChangeConfiguration, RemoteStart, and RemoteStop
\end{itemize}
Items 2.2, 2.5, and 2.14 require a physical EV to be connected to the vSECC connector.
If no EV is connected within the configured timeout, those items and all items that
depend on an active charging session are recorded as \textbf{WARN}.

\section{Section 2 Results}
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
        item       = escape(r["item"])
        msg        = trunc(escape(r["message"]), 72)
        detail     = trunc(escape(r["detail"] or ""), 90)
        remark     = trunc(escape(r.get("remark") or ""), 80)
        cell       = status_cell(r["status"])
        detail_col = detail
        if remark:
            detail_col += r"{\newline\normalfont\footnotesize\itshape " + remark + "}"
        tex += f"\\small {item} & \\small {msg} & {detail_col} & {cell} \\\\\n\\hline\n"

    tex += r"""
\end{longtable}
\normalsize

\section{Notes on Failures and Warnings}

\subsection*{FAIL items}
\begin{itemize}
  \item \textbf{2.1, 2.4} ChangeConfiguration AutoCharge:
        The \texttt{AutoCharge} key is not a standard OCPP~1.6 configuration key.
        The vSECC maps it internally to its OCPP~2.x variable
        \texttt{tx\_ctrlr\_tx\_before\_accepted\_enabled}.
        The MEA CSMS may return \texttt{Rejected} for non-standard keys.
        If the CSMS returns \texttt{Accepted}, the item passes.
\end{itemize}

\subsection*{WARN items}
\begin{itemize}
  \item \textbf{2.2, 2.5, 2.14} StatusNotification Preparing:
        Requires a physical EV to be plugged into the vSECC connector.
        The test waits up to the configured \texttt{EV\_WAIT\_SEC} timeout.
        If no EV was present during the test run, these are recorded as WARN.
  \item \textbf{2.6--2.9, 2.11--2.13, 2.15--2.21}
        All items that depend on an active charging session (Authorize,
        StartTransaction, Charging, MeterValues, StopTransaction, etc.)
        are WARN when no EV is connected.
  \item \textbf{2.9, 2.18} MeterValues:
        The MEA sandbox REST API does not expose TriggerMessage (HTTP~404).
        MeterValues are observable only if the vSECC publishes them
        autonomously to MQTT based on \texttt{MeterValueSampleInterval}.
  \item \textbf{2.19} StatusNotification SuspendedEV:
        Requires the EV to stop drawing current (BMS charge limit reached or
        EV-side power management). Cannot be reliably triggered without a
        real EV battery management system.
  \item \textbf{2.20} StopTransaction (reason=EVDisconnected):
        Requires the user to physically unplug the EV connector.
\end{itemize}

\section{Dependency Map}

Items in Section~2 form two sequential charging session chains:

\begin{tabular}{ll}
\textbf{Session 1} & 2.2 $\rightarrow$ 2.3 (unplug) \\
                   & 2.5 $\rightarrow$ 2.6 $\rightarrow$ 2.7 $\rightarrow$ 2.8
                     $\rightarrow$ 2.9 $\rightarrow$ 2.10 (RemoteStop) \\
                   & $\rightarrow$ 2.11 $\rightarrow$ 2.12 $\rightarrow$ 2.13 \\
\textbf{Session 2} & 2.14 $\rightarrow$ 2.15 $\rightarrow$ 2.16 $\rightarrow$ 2.17
                     $\rightarrow$ 2.18 $\rightarrow$ 2.19 \\
                   & $\rightarrow$ 2.20 (EVDisconnect) $\rightarrow$ 2.21 \\
\end{tabular}

\medskip
\noindent Items 2.1 and 2.4 (ChangeConfiguration) are independent of EV presence.

\end{document}
"""
    return tex


def main():
    if not os.path.exists(RESULTS_JSON):
        print(f"ERROR: {RESULTS_JSON} not found.  Run test_mea_section2.py first.")
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
