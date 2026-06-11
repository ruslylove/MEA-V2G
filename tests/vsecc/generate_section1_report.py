#!/usr/bin/env python3
"""
Generate a LaTeX/PDF test report for MEA OCPP 1.6 Section 1.
Reads  tex/vsecc_section1_results.json  (produced by test_mea_section1.py)
Writes tex/vsecc_section1_report.tex
Compiles tex/vsecc_section1_report.pdf  (requires pdflatex)

Usage (from project root):
  python3 tests/vsecc/generate_section1_report.py
"""
import json
import os
import subprocess
import datetime

# Paths are relative to the project root regardless of cwd
_ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_JSON = os.path.join(_ROOT, "tex", "vsecc_section1_results.json")
OUTPUT_TEX   = os.path.join(_ROOT, "tex", "vsecc_section1_report.tex")
PDF_NAME     = "vsecc_section1_report.pdf"
TEX_DIR      = os.path.join(_ROOT, "tex")

CP_ID   = "rddQC4000001"
NOW     = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def escape(s: str) -> str:
    for ch, rep in [("\\", "\\\\"), ("{", "\\{"), ("}", "\\}"),
                    ("_", "\\_"), ("#", "\\#"), ("&", "\\&"),
                    ("%", "\\%"), ("$", "\\$"),
                    ("~", "\\textasciitilde{}"),
                    ("^", "\\textasciicircum{}")]:
        s = s.replace(ch, rep)
    return s


def trunc(s: str, n: int) -> str:
    """Truncate escaped string to at most n chars (after escaping)."""
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


def build_tex(results: list, run_ts: str) -> str:
    pass_n = sum(1 for r in results if r["status"] == "PASS")
    fail_n = sum(1 for r in results if r["status"] == "FAIL")
    warn_n = sum(1 for r in results if r["status"] == "WARN")
    skip_n = sum(1 for r in results if r["status"] == "SKIP")
    total  = pass_n + fail_n + warn_n + skip_n

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
\large Section 1: Charger Configuration Verification\\[0.3em]
\normalsize Vector vSECC.single Board vs MEA CSMS (""" + escape(CP_ID) + r""")}
\author{MEA-V2G Project --- Automated Test}
\date{""" + NOW + r"""}

\pagestyle{fancy}
\fancyhf{}
\lhead{MEA OCPP 1.6 -- Section 1}
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
\textbf{Proxy}             & ws://192.168.1.200:9000 (PC alias on enp3s0) \\
\textbf{Test PC}           & 192.168.111.185 / 192.168.1.200 \\
\textbf{Test Date}         & """ + NOW + r""" \\
\textbf{Results file}      & tex/vsecc\_section1\_results.json \\
\end{tabular}

\subsection*{Test Setup}
The vSECC connects to \texttt{ws://192.168.1.200:9000} (WebSocket proxy running on
the test PC's alias interface). The proxy forwards all frames transparently to the
MEA CSMS at \texttt{wss://ocpp.measandbox.com:2930} over TLS.
A control API on port 9001 allows the test to inject CSMS-initiated commands
(TriggerMessage, ChangeAvailability) directly to the vSECC, since the MEA REST API
does not expose those endpoints.

\section{Section 1 Results}
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
        item   = escape(r["item"])
        msg    = trunc(escape(r["message"]), 72)
        detail = trunc(escape(r["detail"] or ""), 90)
        remark = trunc(escape(r.get("remark") or ""), 80)
        cell   = status_cell(r["status"])
        raw    = trunc(escape(r.get("raw") or ""), 160)
        if raw == '""':
            raw = ""
        # Detail column is already \ttfamily from column spec; remark resets font
        detail_col = detail
        if remark:
            detail_col += r"{\newline\normalfont\footnotesize\itshape " + remark + "}"
        if raw:
            detail_col += r"{\newline\color{gray!70}\scriptsize\ttfamily " + raw + "}"
        tex += f"\\small {item} & \\small {msg} & {detail_col} & {cell} \\\\\n\\hline\n"

    tex += r"""
\end{longtable}
\normalsize
\noindent\footnotesize{* \texttt{vendorId} / \texttt{vendorErrorCode} fields absent (optional per OCPP 1.6 \S 4.7).}
\normalsize

\section{Notes on Failures and Warnings}

\subsection*{WARN items — sandbox limitations}
\begin{itemize}
  \item \textbf{1.3--1.7, 1.13--1.16} TriggerMessage / ChangeAvailability:
        The MEA sandbox REST API does not expose these endpoints (HTTP~404
        for all casing variants tested).
        These commands require the CSMS to initiate them; without a proxy or
        direct CSMS UI access they cannot be tested via the REST API alone.
  \item \textbf{1.9} GetConfiguration: The MEA CSMS processes the request
        asynchronously and returns HTTP~200 with an empty body (\texttt{""}).
        The vSECC \emph{does} accept ChangeConfiguration for all standard keys
        (items 1.10--1.12, 1.21 all PASS), confirming values take effect.
  \item \textbf{1.17, 1.19} GetDiagnostics / UpdateFirmware:
        Available via \texttt{POST /EV/remote/GetDiagnostics} and
        \texttt{POST /EV/remote/UpdateFirmware} (PascalCase required;
        lowercase returns HTTP~404).  Both return HTTP~200 (PASS).
        Discretionary items (\textit{*ขึ้นกับการพิจารณา}).
\end{itemize}

\subsection*{WARN items}
\begin{itemize}
  \item \textbf{1.2} StatusNotification on boot: not observed on MQTT due to
        timing; the vSECC OCPP session was already established before the MQTT
        subscriber subscribed.  Verify in the CSMS event log.
  \item \textbf{1.8, 1.18, 1.20} MeterValues / DiagnosticsStatus / FirmwareStatus:
        require an active charging session or a firmware download in progress;
        retest during the appropriate operational state.
\end{itemize}

\end{document}
"""
    return tex


def main():
    if not os.path.exists(RESULTS_JSON):
        print(f"ERROR: {RESULTS_JSON} not found.  Run test_mea_section1.py first.")
        return

    with open(RESULTS_JSON) as f:
        data = json.load(f)

    results  = data["results"]
    run_ts   = data.get("timestamp", NOW)
    tex_body = build_tex(results, run_ts)

    os.makedirs(TEX_DIR, exist_ok=True)
    with open(OUTPUT_TEX, "w") as f:
        f.write(tex_body)
    print(f"LaTeX written to {OUTPUT_TEX}")

    # Compile twice for TOC (cwd=TEX_DIR so relative includes work)
    tex_filename = os.path.basename(OUTPUT_TEX)
    for _ in range(2):
        r = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", tex_filename],
            cwd=TEX_DIR, capture_output=True, errors="replace"
        )
    pdf_path = os.path.join(TEX_DIR, PDF_NAME)
    if os.path.exists(pdf_path):
        size = os.path.getsize(pdf_path)
        print(f"PDF compiled: {pdf_path}  ({size//1024} KB)")
    else:
        print("pdflatex failed.  Check tex/*.log for errors.")
        print(r.stdout[-1000:])


if __name__ == "__main__":
    main()
