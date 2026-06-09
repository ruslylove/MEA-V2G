#!/usr/bin/env python3
"""
Generate a LaTeX/PDF test report for MEA OCPP 1.6 Section 5.
Reads  tex/vsecc_section5_results.json  (produced by test_mea_section5.py)
Writes tex/vsecc_section5_report.tex
Compiles tex/vsecc_section5_report.pdf  (requires pdflatex)

Usage (from project root):
  python3 tests/vsecc/generate_section5_report.py
"""
import json
import os
import subprocess
import datetime

_ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_JSON = os.path.join(_ROOT, "tex", "vsecc_section5_results.json")
OUTPUT_TEX   = os.path.join(_ROOT, "tex", "vsecc_section5_report.tex")
PDF_NAME     = "vsecc_section5_report.pdf"
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


FLOW_HEADERS = {
    "5.1":  r"Flow 1: Reserve $\rightarrow$ Cancel --- Items 5.1--5.5",
    "5.6":  r"Flow 2: Reserve $\rightarrow$ Expiry --- Items 5.6--5.8",
    "5.9":  r"Flow 3: Reserve $\rightarrow$ EV Plug $\rightarrow$ RemoteStart $\rightarrow$ Charge $\rightarrow$ RemoteStop --- Items 5.9--5.19",
}


def build_tex(data: dict) -> str:
    results      = data["results"]
    run_ts       = data.get("timestamp", NOW)
    ev_wait      = data.get("ev_wait_sec", "?")
    expiry_wait  = data.get("expiry_wait_sec", "?")
    pass_n  = sum(1 for r in results if r["status"] == "PASS")
    fail_n  = sum(1 for r in results if r["status"] == "FAIL")
    warn_n  = sum(1 for r in results if r["status"] == "WARN")
    skip_n  = sum(1 for r in results if r["status"] == "SKIP")
    total   = pass_n + fail_n + warn_n + skip_n

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
\large Section 5: Reservation Check\\[0.3em]
\normalsize Vector vSECC.single Board vs MEA CSMS (""" + escape(CP_ID) + r""")}
\author{MEA-V2G Project --- Automated Test}
\date{""" + NOW + r"""}

\pagestyle{fancy}
\fancyhf{}
\lhead{MEA OCPP 1.6 -- Section 5}
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
\textbf{Expiry Wait}       & """ + str(expiry_wait) + r""" s (wait for CSMS to expire short reservation) \\
\textbf{Test Date}         & """ + run_ts[:19].replace("T", " ") + r""" UTC \\
\textbf{Results file}      & tex/vsecc\_section5\_results.json \\
\end{tabular}

\subsection*{Test Architecture}
Reservation commands are issued via the MEA REST API.
\begin{itemize}
  \item \texttt{POST /EV/cmd/chargepoint/reserve}
        with \texttt{chargepoint}, \texttt{connector}, \texttt{card\_id}, and \texttt{duration}.
  \item \texttt{POST /EV/cmd/chargepoint/cancel}
        with \texttt{chargepoint} and \texttt{reservation\_id}.
\end{itemize}
MQTT topics \texttt{vsecc/connector/+/status/\#} and
\texttt{vsecc/connector/+/ev/\#} are observed for status changes.

Three reservation scenarios are tested:
\begin{enumerate}
  \item \textbf{Flow 1 --- Reserve \textrightarrow{} Cancel (5.1--5.5):}
        ReserveNow is sent for 5 minutes, then CancelReservation is issued.
        Charger should return to Available.
  \item \textbf{Flow 2 --- Reserve \textrightarrow{} Expiry (5.6--5.8):}
        ReserveNow is sent with a short duration.
        The CSMS expires the reservation automatically and
        the charger returns to Available.
  \item \textbf{Flow 3 --- Reserve \textrightarrow{} EV Plug \textrightarrow{} Charge (5.9--5.19):}
        ReserveNow is sent for 10 minutes, an EV is plugged in while
        the connector is reserved, RemoteStart initiates a charging session,
        and RemoteStop terminates it.
\end{enumerate}

\section{Section 5 Results}
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
            hdr = FLOW_HEADERS[item]
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

\subsection*{Reservation API}
\begin{itemize}
  \item The reserve endpoint is \texttt{POST /EV/cmd/chargepoint/reserve}.
        Payload keys: \texttt{"chargepoint"} (not \texttt{"chargepoint\_id"}),
        \texttt{"connector"}, \texttt{"card\_id"}, and \texttt{"duration"} (seconds).
  \item The cancel endpoint is \texttt{POST /EV/cmd/chargepoint/cancel}.
        Payload keys: \texttt{"chargepoint"} and \texttt{"reservation\_id"}.
  \item The \texttt{reservation\_id} is extracted from the ReserveNow response body
        at \texttt{data["result"]["reservation\_id"]}.
\end{itemize}

\subsection*{StatusNotification observation}
MQTT topic \texttt{vsecc/connector/1/status/\ldots} reports \texttt{Reserved},
\texttt{Preparing}, \texttt{Charging}, \texttt{Finishing}, and \texttt{Available}
states. Items that WARN on this topic indicate that the vSECC did not publish
a state change within the observation window, or that the status key differs
from the expected keyword.

\subsection*{Reservation expiry (Flow 2)}
The MEA CSMS may enforce a minimum reservation duration longer than the
requested 60 seconds. If the Available status is not observed within
\texttt{EXPIRY\_WAIT\_SEC}, the test cancels the reservation automatically
to unblock Flow 3. Rerun with a longer expiry wait or a shorter minimum
enforced at the CSMS if this item WARNs.

\subsection*{EV-dependent items (Flow 3)}
\begin{itemize}
  \item \textbf{5.11} StatusNotification Reserved/Preparing:
        requires a physical EV to be connected to the reserved connector.
  \item \textbf{5.12--5.19} All depend on RemoteStart succeeding with an
        EV present and a valid reserved RFID tag.
\end{itemize}
Set \texttt{EV\_WAIT\_SEC > 0} in the test script to enable EV plug detection.

\section{Dependency Map}
\begin{tabular}{ll}
\textbf{Flow 1} & 5.1 $\rightarrow$ 5.2 (ReserveNow) $\rightarrow$ 5.3
                  $\rightarrow$ 5.4 (CancelReservation) $\rightarrow$ 5.5 \\
\textbf{Flow 2} & 5.6 (ReserveNow) $\rightarrow$ 5.7 (Expiry)
                  $\rightarrow$ 5.8 \\
\textbf{Flow 3} & 5.9 (ReserveNow) $\rightarrow$ 5.10 $\rightarrow$ 5.11
                  (EV plug) $\rightarrow$ 5.12 (RemoteStart) \\
                & $\rightarrow$ 5.13 $\rightarrow$ 5.14 $\rightarrow$ 5.15
                  (MeterValues) $\rightarrow$ 5.16 (RemoteStop) \\
                & $\rightarrow$ 5.17 $\rightarrow$ 5.18 $\rightarrow$ 5.19
                  (Unplug) \\
\end{tabular}

\end{document}
"""
    return tex


def main():
    if not os.path.exists(RESULTS_JSON):
        print(f"ERROR: {RESULTS_JSON} not found.  Run test_mea_section5.py first.")
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
