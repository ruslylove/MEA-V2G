#!/usr/bin/env python3
"""
Generate a LaTeX/PDF test report for MEA OCPP 1.6 Section 6.
Reads  tex/vsecc_section6_results.json  (produced by test_mea_section6.py)
Writes tex/vsecc_section6_report.tex
Compiles tex/vsecc_section6_report.pdf  (requires pdflatex)

Usage (from project root):
  python3 tests/vsecc/generate_section6_report.py
"""
import json
import os
import subprocess
import datetime

_ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_JSON = os.path.join(_ROOT, "tex", "vsecc_section6_results.json")
OUTPUT_TEX   = os.path.join(_ROOT, "tex", "vsecc_section6_report.tex")
PDF_NAME     = "vsecc_section6_report.pdf"
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
    "6.1":  r"Flow 1: Local Start with Charging Profile (6.1--6.13)",
    "6.14": r"Flow 2: Remote Start with Charging Profile (6.14--6.26)",
}


def build_tex(data: dict) -> str:
    results    = data["results"]
    run_ts     = data.get("timestamp", NOW)
    ev_wait    = data.get("ev_wait_sec", "?")
    local_wait = data.get("local_stop_wait_sec", "?")
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
\large Section 6: Charging Profile Verification\\[0.3em]
\normalsize Vector vSECC.single Board vs MEA CSMS (""" + escape(CP_ID) + r""")}
\author{MEA-V2G Project --- Automated Test}
\date{""" + NOW + r"""}

\pagestyle{fancy}
\fancyhf{}
\lhead{MEA OCPP 1.6 -- Section 6}
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
\textbf{Results file}      & tex/vsecc\_section6\_results.json \\
\end{tabular}

\subsection*{Test Architecture}
SetChargingProfile commands are issued via the MEA REST API
(\texttt{POST /EV/remote/SetChargingProfile}).
Two charging sessions are tested:
\begin{enumerate}
  \item \textbf{Local start with charging profiles (6.1--6.13):}
        EV plugged in; driver taps RFID card to authorize and start;
        SetChargingProfile (TxProfile, 5\,kW) is applied during charging;
        a second SetChargingProfile update (7.4\,kW) is applied;
        MeterValues are sampled before and after each profile change;
        session is terminated by local card tap (or RemoteStop fallback).
  \item \textbf{Remote start with charging profiles (6.14--6.26):}
        EV plugged in; CSMS sends RemoteStartTransaction via MEA REST API;
        SetChargingProfile (5\,kW) is applied, then updated (7.4\,kW);
        MeterValues are sampled after each change;
        CSMS sends RemoteStopTransaction to end the session.
\end{enumerate}
MQTT subscription to \texttt{mqtt://192.168.1.166:1883} is used to observe all
StatusNotification, Authorize, StartTransaction, StopTransaction, and MeterValues events.

\section{Section 6 Results}
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

\subsection*{SetChargingProfile command}
The MEA sandbox REST API endpoint \texttt{POST /EV/remote/SetChargingProfile} accepts
a \texttt{csChargingProfiles} object with \texttt{chargingProfilePurpose=TxProfile},
\texttt{chargingProfileKind=Absolute}, and a \texttt{chargingSchedule} containing
one or more \texttt{chargingSchedulePeriod} entries with \texttt{chargingRateUnit=W}.
Items 6.7, 6.9, 6.19, and 6.21 are PASS if the API returns HTTP~200.

\subsection*{EV-dependent items}
\begin{itemize}
  \item \textbf{6.2, 6.14} StatusNotification Preparing: require a physical EV to
        plug into the vSECC connector within the \texttt{EV\_WAIT\_SEC} timeout.
  \item \textbf{6.3--6.13} All Flow 1 session items depend on the EV being connected
        in item 6.2.
  \item \textbf{6.16--6.26} All Flow 2 session items depend on RemoteStart succeeding
        with an EV present (item 6.15 depends on EV presence from 6.14).
\end{itemize}

\subsection*{Item 6.11 --- Manual stop}
Item 6.11 tests that the EV driver can tap their RFID card on the vSECC to trigger a
local \texttt{StopTransaction} with \texttt{reason=Local}.
If no card tap is detected within \texttt{LOCAL\_STOP\_WAIT\_SEC} seconds, the test
falls back to RemoteStop to clear the session so Flow~2 can proceed.
The fallback is recorded as \textbf{WARN} with a remark.

\subsection*{Items 6.6, 6.8, 6.10, 6.18, 6.20, 6.22 --- MeterValues}
The MEA sandbox REST API may return HTTP~404 for TriggerMessage.
MeterValues are observable only when the vSECC autonomously publishes them to MQTT
based on the \texttt{MeterValueSampleInterval} configuration.
These items verify that MeterValues continue to be reported correctly both before
and after each SetChargingProfile command.

\subsection*{Items 6.15, 6.23 --- RemoteStart / RemoteStop}
\begin{itemize}
  \item \textbf{6.15} Uses a dedicated password (\texttt{MEA\_PASS\_START}) for the
        \texttt{/EV/cmd/chargepoint/remoteStart} endpoint.
  \item \textbf{6.23} RemoteStop with \texttt{transaction\_id=0} succeeds when exactly one
        transaction is active; FAIL if no active transaction exists.
\end{itemize}

\section{Dependency Map}
\begin{tabular}{ll}
\textbf{Flow 1} & 6.2 $\rightarrow$ 6.3 $\rightarrow$ 6.4 $\rightarrow$ 6.5
                  $\rightarrow$ 6.6 $\rightarrow$ 6.7 $\rightarrow$ 6.8
                  $\rightarrow$ 6.9 $\rightarrow$ 6.10 $\rightarrow$ 6.11
                  $\rightarrow$ 6.12 $\rightarrow$ 6.13 \\
\textbf{Flow 2} & 6.14 $\rightarrow$ 6.15 $\rightarrow$ 6.16 $\rightarrow$ 6.17
                  $\rightarrow$ 6.18 $\rightarrow$ 6.19 $\rightarrow$ 6.20
                  $\rightarrow$ 6.21 $\rightarrow$ 6.22 $\rightarrow$ 6.23
                  $\rightarrow$ 6.24 $\rightarrow$ 6.25 $\rightarrow$ 6.26 \\
\end{tabular}

\medskip
\noindent Item 6.1 is independent of EV presence.

\end{document}
"""
    return tex


def main():
    if not os.path.exists(RESULTS_JSON):
        print(f"ERROR: {RESULTS_JSON} not found.  Run test_mea_section6.py first.")
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
