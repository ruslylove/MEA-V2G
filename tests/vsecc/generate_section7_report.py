#!/usr/bin/env python3
"""
Generate a LaTeX/PDF test report for MEA OCPP 1.6 Section 7.
Reads  tex/vsecc_section7_results.json  (produced by test_mea_section7.py)
Writes tex/vsecc_section7_report.tex
Compiles tex/vsecc_section7_report.pdf  (requires pdflatex)

Usage (from project root):
  python3 tests/vsecc/generate_section7_report.py
"""
import json
import os
import subprocess
import datetime

_ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_JSON = os.path.join(_ROOT, "tex", "vsecc_section7_results.json")
OUTPUT_TEX   = os.path.join(_ROOT, "tex", "vsecc_section7_report.tex")
PDF_NAME     = "vsecc_section7_report.pdf"
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


# Sub-scenario dividers: first item of each sub-scenario maps to its header text.
SUB_SCENARIO_HEADERS = {
    "7.1.1": r"7.1 --- RemoteStart while Unplugged (Available State)",
    "7.2.1": r"7.2 --- Concurrent RemoteStart (Second Rejected)",
    "7.3.1": r"7.3 --- Swap Card (Invalid then Valid RFID)",
    "7.4.1": r"7.4 --- Emergency Stop",
    "7.5.1": r"7.5 --- Open Door",
    "7.6.1": r"7.6 --- Power Loss / Reboot (Simulated via Hard Reset)",
    "7.7.1": r"7.7 --- Local List Offline",
}


def build_tex(data: dict) -> str:
    results    = data["results"]
    run_ts     = data.get("timestamp", NOW)
    ev_wait    = data.get("ev_wait_sec", "?")
    boot_wait  = data.get("boot_wait_sec", "?")
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
\large Section 7: Abnormal Operation Verification\\[0.3em]
\normalsize Vector vSECC.single Board vs MEA CSMS (""" + escape(CP_ID) + r""")}
\author{MEA-V2G Project --- Automated Test}
\date{""" + NOW + r"""}

\pagestyle{fancy}
\fancyhf{}
\lhead{MEA OCPP 1.6 -- Section 7}
\rhead{Page \thepage}

\begin{document}
\maketitle
\tableofcontents
\newpage

\section{Test Configuration}
\begin{tabular}{ll}
\textbf{Device Under Test}  & Vector vSECC.single Board \\
\textbf{Device IP}          & 192.168.1.166 \\
\textbf{OCPP Version}       & 1.6 (JSON) \\
\textbf{CP ID}              & """ + escape(CP_ID) + r""" \\
\textbf{CSMS}               & wss://ocpp.measandbox.com:2930 \\
\textbf{Connection}         & Direct (no proxy) \\
\textbf{Test PC}            & 192.168.111.185 (alias 192.168.1.200/24 on enp3s0) \\
\textbf{EV Plug Timeout}    & """ + str(ev_wait) + r""" s per plug event \\
\textbf{Boot Wait Timeout}  & """ + str(boot_wait) + r""" s (after Hard Reset) \\
\textbf{Local Stop Timeout} & """ + str(local_wait) + r""" s \\
\textbf{Test Date}          & """ + run_ts[:19].replace("T", " ") + r""" UTC \\
\textbf{Results file}       & tex/vsecc\_section7\_results.json \\
\end{tabular}

\subsection*{Test Architecture}
The test runs directly against the MEA CSMS with no proxy.
The test PC:
\begin{itemize}
  \item Calls the vSECC REST API (\texttt{http://192.168.1.166/api}) for login and state queries
  \item Subscribes to the vSECC MQTT broker (\texttt{mqtt://192.168.1.166:1883}) to observe
        StatusNotification, charging session state, and meter values
  \item Calls the MEA sandbox REST API (\texttt{https://ocppapi.measandbox.com/EV/})
        with HTTP Digest authentication for RemoteStart, RemoteStop, Reset,
        and SendLocalList
\end{itemize}
Several sub-scenarios (7.3, 7.4, 7.5) require physical hardware interaction
(RFID card scan, emergency stop button, door open switch) that cannot be
triggered programmatically.  Those items are recorded as \textbf{WARN} with a remark
describing the required physical action.
Sub-scenario 7.6 simulates power loss by issuing a Hard Reset via the MEA REST API.

\section{Section 7 Results}
\textbf{Summary: """ + str(pass_n) + r""" PASS \quad """ + str(fail_n) + r""" FAIL \quad """ + str(warn_n) + r""" WARN \quad """ + str(skip_n) + r""" SKIP \quad (""" + str(total) + r""" total)}

\renewcommand{\arraystretch}{1.35}
\newcolumntype{L}[1]{>{\raggedright\arraybackslash}p{#1}}
\newcolumntype{M}[1]{>{\small\ttfamily\raggedright\arraybackslash}p{#1}}
\newcolumntype{C}[1]{>{\centering\arraybackslash}p{#1}}
\begin{longtable}{|L{1.1cm}|L{5.4cm}|M{7cm}|C{1.8cm}|}
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
        # Insert sub-scenario divider header row before the first item of each sub-scenario
        if item in SUB_SCENARIO_HEADERS:
            hdr = escape(SUB_SCENARIO_HEADERS[item])
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

\section{Notes on Failures and Warnings}

\subsection*{7.1 --- RemoteStart while unplugged}
\begin{itemize}
  \item \textbf{7.1.2} The MEA REST API call returns HTTP~200 if the command was
        delivered to the vSECC.  The vSECC OCPP layer should respond
        \texttt{Rejected} to the CSMS because no EV is present.
        This rejection is observable only via a proxy intercepting the
        OCPP WebSocket; therefore the item is recorded as PASS for HTTP~200
        delivery with a WARN remark about the expected Rejected response.
\end{itemize}

\subsection*{7.2 --- Concurrent RemoteStart}
\begin{itemize}
  \item \textbf{7.2.4} A second RemoteStart while a session is already active
        will be blocked by the CSMS before reaching the charger.
        Whether the charger itself responds \texttt{Rejected} is not
        observable without a proxy.  The item is recorded as WARN.
  \item \textbf{7.2.5--7.2.11} Depend on the first RemoteStart being accepted
        and the EV being physically connected (item 7.2.2).
\end{itemize}

\subsection*{7.3 --- Swap Card}
\begin{itemize}
  \item \textbf{7.3.3, 7.3.4} The vSECC reads RFID cards directly via its
        hardware reader.  There is no REST or MQTT API to inject an RFID
        scan.  Both items (invalid card, then valid card) require the operator
        to physically tap RFID cards on the vSECC unit.
  \item \textbf{7.3.5--7.3.11} All subsequent items in this sub-scenario
        depend on the completed RFID swap sequence and are therefore WARN.
\end{itemize}

\subsection*{7.4 --- Emergency Stop}
\begin{itemize}
  \item \textbf{7.4.7} The emergency stop is a physical button on the vSECC
        hardware.  There is no software API to trigger it.
        Items 7.4.8--7.4.10 depend on the E-stop being pressed and released
        physically and are recorded as WARN.
\end{itemize}

\subsection*{7.5 --- Open Door}
\begin{itemize}
  \item \textbf{7.5.2} The door-open fault is triggered by a physical
        door/panel switch on the vSECC unit.
        There is no software API to simulate this condition.
        Item 7.5.3 depends on the door being physically closed and is WARN.
\end{itemize}

\subsection*{7.6 --- Power Loss / Reboot}
\begin{itemize}
  \item \textbf{7.6.7} Physical power loss cannot be reproduced in a
        controlled automated test without additional hardware.
        A Hard Reset via \texttt{POST /EV/remote/reset} is used as the
        closest available approximation.  The item is recorded as PASS if
        the MEA REST API returns HTTP~200, with a WARN remark.
  \item \textbf{7.6.8} After the Hard Reset the vSECC reboots.  The test
        waits up to \texttt{BOOT\_WAIT\_SEC} seconds for reconnection,
        confirmed by the MQTT \texttt{vsecc/ocpp\_connection\_status=connected}
        event or a successful REST API probe.
  \item \textbf{7.6.9, 7.6.10} EV must remain plugged in through the reboot
        and then be unplugged to trigger the final Available transition.
\end{itemize}

\subsection*{7.7 --- Local List Offline}
\begin{itemize}
  \item \textbf{7.7.1} SendLocalList is issued via
        \texttt{POST /EV/remote/SendLocalList} on the MEA REST API
        (PascalCase endpoint --- lowercase \texttt{sendLocalList} returns HTTP~404).
        HTTP~200 confirms the command was accepted by the CSMS and relayed
        to the vSECC.
  \item \textbf{7.7.2--7.7.7} Offline start, local RFID authorization,
        transaction buffering, and reconnect flushing are handled entirely
        within the vSECC firmware.  Without a proxy intercepting the OCPP
        WebSocket it is not possible to observe these behaviors from the
        test PC.  All items are recorded as WARN.
\end{itemize}

\section{Dependency Map}

\begin{tabular}{lp{11cm}}
\textbf{7.1} & Independent (no EV required) \\[2pt]
\textbf{7.2} & 7.2.2 (EV plug) $\rightarrow$ 7.2.3 (RemoteStart\,1) $\rightarrow$
               7.2.5 $\rightarrow$ 7.2.6 $\rightarrow$ 7.2.7 $\rightarrow$
               7.2.8 (RemoteStop) $\rightarrow$ 7.2.9 $\rightarrow$ 7.2.10
               $\rightarrow$ 7.2.11 \\[2pt]
\textbf{7.3} & 7.3.2 (EV plug) $\rightarrow$ 7.3.3/7.3.4 (physical RFID)
               $\rightarrow$ 7.3.5 $\rightarrow$ \ldots $\rightarrow$ 7.3.11 \\[2pt]
\textbf{7.4} & 7.4.2 (EV plug) $\rightarrow$ 7.4.3 (RemoteStart) $\rightarrow$
               7.4.4 $\rightarrow$ 7.4.5 $\rightarrow$ 7.4.6 $\rightarrow$
               7.4.7 (physical E-stop) $\rightarrow$ 7.4.8 $\rightarrow$
               7.4.9 $\rightarrow$ 7.4.10 \\[2pt]
\textbf{7.5} & 7.5.2 (physical door open) $\rightarrow$ 7.5.3 \\[2pt]
\textbf{7.6} & 7.6.2 (EV plug) $\rightarrow$ 7.6.3 (RemoteStart) $\rightarrow$
               7.6.4 $\rightarrow$ 7.6.5 $\rightarrow$ 7.6.6 $\rightarrow$
               7.6.7 (Hard Reset) $\rightarrow$ 7.6.8 (BootNotification)
               $\rightarrow$ 7.6.9 $\rightarrow$ 7.6.10 \\[2pt]
\textbf{7.7} & 7.7.1 (SendLocalList) independent;
               7.7.2--7.7.7 require offline network isolation \\
\end{tabular}

\end{document}
"""
    return tex


def main():
    if not os.path.exists(RESULTS_JSON):
        print(f"ERROR: {RESULTS_JSON} not found.  Run test_mea_section7.py first.")
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
