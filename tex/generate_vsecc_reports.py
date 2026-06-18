#!/usr/bin/env python3
"""
Generate vsecc_sectionN_report.tex from vsecc_sectionN_results.json
and compile each to PDF with pdflatex.
"""
import json
import os
import re
import subprocess
import sys

TEX_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Section metadata ──────────────────────────────────────────────────────────
SECTION_META = {
    1:  {"title_en": "Charger Configuration Verification",
         "header": "Section 1"},
    2:  {"title_en": "Auto Charge Verification",
         "header": "Section 2"},
    3:  {"title_en": "Normal Charging Verification",
         "header": "Section 3"},
    4:  {"title_en": "Reset Verification",
         "header": "Section 4"},
    5:  {"title_en": "Reservation Verification",
         "header": "Section 5"},
    6:  {"title_en": "Charging Profile Verification",
         "header": "Section 6"},
    7:  {"title_en": "Abnormal Operation Verification",
         "header": "Section 7"},
    8:  {"title_en": "Dual Connector Verification",
         "header": "Section 8"},
    9:  {"title_en": "MEA-Specific Configuration \\& V2G/BPT",
         "header": "Section 9"},
    10: {"title_en": "CSMS Command Verification",
         "header": "Section 10"},
    11: {"title_en": "Performance -- Reconnect Time",
         "header": "Section 11"},
}

# ── Section-specific notes ────────────────────────────────────────────────────
SECTION_NOTES = {
    1: r"""
\subsection*{Sandbox limitations (items 1.3--1.7)}
The MEA sandbox REST API does not expose \texttt{/remote/triggerMessage} (HTTP~404).
TriggerMessage requires the CSMS to initiate the command; items 1.4, 1.6, 1.8 depend on it.

\subsection*{ChangeAvailability (items 1.13--1.16)}
\texttt{/remote/changeAvailability} returns HTTP~404.
ChangeAvailability was issued via vSECC MQTT \texttt{set\_availability}.
The resulting \texttt{StatusNotification} frames were confirmed via \texttt{ocpplib.log}.

\subsection*{GetConfiguration (item 1.9)}
MEA CSMS processes GetConfiguration asynchronously (HTTP~200, empty body).
The vSECC accepts ChangeConfiguration for all standard keys (1.10--1.12, 1.21 PASS).

\subsection*{DiagnosticsStatus / FirmwareStatus (items 1.18, 1.20)}
Discretionary items. vSECC does not send these notifications without a real FTP endpoint.
""",
    2: r"""
\subsection*{EV-dependent items (2.2, 2.5, 2.14 and dependents)}
These items require a physical EV to be plugged into the vSECC connector.
All items that depend on an active charging session (Authorize, StartTransaction,
Charging, MeterValues, StopTransaction, etc.) are WARN when no EV is connected.

\subsection*{MeterValues (items 2.9, 2.18)}
MEA sandbox does not expose TriggerMessage (HTTP~404).
MeterValues are observable only if published autonomously on \texttt{MeterValueSampleInterval}.

\subsection*{StatusNotification SuspendedEV (item 2.19)}
Requires the EV BMS to stop drawing current; cannot be reliably triggered without a real EV.
""",
    3: r"""
\subsection*{EV-dependent items}
Items 3.3, 3.11 require a physical EV plug event. All downstream items are WARN without an EV.

\subsection*{RemoteStartTransaction (items 3.12--3.15)}
RemoteStart is issued via MEA REST API. Session items confirmed via MQTT or \texttt{ocpplib.log}.

\subsection*{MeterValues (items 3.7, 3.15)}
MEA sandbox does not expose TriggerMessage (HTTP~404).
Values observed via MQTT autonomous publication or \texttt{ocpplib.log}.
""",
    4: r"""
\subsection*{Reset flows}
Hard Reset and Soft Reset commands are issued via MEA REST API.
BootNotification after Hard Reset confirms vSECC reconnection.
All dependent session items (StartTransaction, MeterValues, StopTransaction)
require a physical EV to be connected during the test.

\subsection*{BootNotification after reset}
Verified via MQTT \texttt{vsecc/ocpp\_connection\_status} reconnect event.
The \texttt{ocpplib.log} provides additional frame-level evidence.
""",
    5: r"""
\subsection*{Reservation flow}
ReserveNow and CancelReservation are issued via MEA REST API.
StatusNotification Reserved/Available transitions are confirmed via MQTT or \texttt{ocpplib.log}.

\subsection*{EV-dependent items}
Items 5.11 (EV plug) and all downstream charging session items require a physical EV.

\subsection*{Reservation expiry (item 5.7)}
The vSECC must release the reservation automatically when the timer expires.
Observed via MQTT StatusNotification Available after the expiry window.
""",
    6: r"""
\subsection*{Charging profile flows}
SetChargingProfile (TxProfile) commands are issued via MEA REST API during an active session.
MeterValues before/after profile changes confirm the power limit adjustment.

\subsection*{EV-dependent items}
All charging session items require a physical EV. Without an EV the test records WARN.
""",
    7: r"""
\subsection*{Remote-controllable flows (7.2, 7.6)}
RemoteStart/RemoteStop and Reset commands are verifiable via MEA REST API and \texttt{ocpplib.log}.

\subsection*{Physical-hardware items (7.3, 7.4.7--7.4.10, 7.5)}
Card-swap, emergency stop, door-open switch, and power-loss scenarios require
physical interaction with the hardware. These are permanently recorded as WARN
with a remark indicating the required operator action.

\subsection*{Offline buffering (7.7)}
Offline transaction buffering cannot be verified without direct OCPP frame capture
from the reconnection burst. Recorded as WARN pending further tooling.
""",
    8: r"""
\subsection*{Dual connector}
The Vector vSECC.single Board supports only a single connector.
All Section~8 dual-connector items are recorded as SKIP.
""",
    9: r"""
\subsection*{V2G/BPT configuration (items 9.1--9.4)}
ChangeConfiguration commands (V2GMode, MeterValueSampleInterval, LocalAuthorizeOffline)
are issued via MEA REST API and return HTTP~200.

\subsection*{V2G power demand (items 9.4.1--9.4.3)}
Require an active ISO~15118 V2G charging session (physical EV with V2G capability).
Observed via MQTT MeterValues with V2G measurands or \texttt{ocpplib.log}.
""",
    10: r"""
\subsection*{CS\(\to\)CSMS items (10.1, 10.2, 10.5, 10.9--10.11)}
These OCPP actions are charger-initiated and cannot be triggered via the MEA REST API.
The test checks \texttt{ocpplib.log} for recent \texttt{>>>} frames as secondary evidence.
A PASS requires the frame to appear in the log within a 3\,s window.

\subsection*{TriggerMessage (item 10.15)}
The MEA sandbox does not expose \texttt{/remote/triggerMessage} (HTTP~404).

\subsection*{ChangeAvailability (item 10.18)}
\texttt{/remote/changeAvailability} returns HTTP~404.
Verified via MQTT \texttt{set\_availability} with \texttt{ocpplib.log} fallback.

\subsection*{GetDiagnostics / UpdateFirmware (items 10.19--10.20)}
MEA sandbox returns HTTP~404 for these endpoints (PascalCase path required but both 404).
""",
    11: r"""
\subsection*{Reconnect time measurement}
The vSECC is hard-reset via MEA REST API and the test measures elapsed time until
\texttt{vsecc/ocpp\_connection\_status = connected} is observed on MQTT.
PASS threshold: $<90$\,s. WARN: 90--180\,s. FAIL: $>180$\,s or no reconnect.
Three measurements are taken for the average (item~11.2).
\texttt{ocpplib.log} provides BootNotification frame timestamps as supplementary evidence.
""",
}

# ── LaTeX helpers ─────────────────────────────────────────────────────────────
def tex_escape_raw(s: str) -> str:
    """Escape a raw OCPP frame string for display inside \\ttfamily."""
    s = s.encode("ascii", errors="ignore").decode("ascii")
    s = s.replace("\\", r"\textbackslash{}")
    s = s.replace("{",  r"\{")
    s = s.replace("}",  r"\}")
    s = s.replace("$",  r"\$")
    s = s.replace("#",  r"\#")
    s = s.replace("%",  r"\%")
    s = s.replace("&",  r"\&")
    s = s.replace("_",  r"\_")
    s = s.replace("^",  r"\textasciicircum{}")
    s = s.replace("~",  r"\textasciitilde{}")
    return s


def tex_escape(s: str) -> str:
    """Escape special LaTeX characters; drop non-ASCII (Thai, etc.) safely."""
    # Known Thai phrase replacements before stripping
    thai_map = {
        "ขึ้นกับการพิจารณา": "discretionary",
    }
    for thai, eng in thai_map.items():
        s = s.replace(thai, eng)
    # Strip remaining non-ASCII
    s = s.encode("ascii", errors="ignore").decode("ascii")
    replacements = [
        ("\\", r"\textbackslash{}"),
        ("&",  r"\&"),
        ("%",  r"\%"),
        ("$",  r"\$"),
        ("#",  r"\#"),
        ("_",  r"\_"),
        ("{",  r"\{"),
        ("}",  r"\}"),
        ("~",  r"\textasciitilde{}"),
        ("^",  r"\textasciicircum{}"),
        ("→",  r"$\to$"),
        ("≤",  r"$\leq$"),
        ("≥",  r"$\geq$"),
    ]
    for old, new in replacements:
        s = s.replace(old, new)
    return s


def result_cell(status: str) -> str:
    colour = {
        "PASS": "passgreen",
        "FAIL": "failred",
        "WARN": "warnorange",
        "SKIP": "skipgray",
    }.get(status, "black")
    return r"\textcolor{" + colour + r"}{\textbf{" + status + r"}}"


def table_row(r: dict) -> str:
    item   = tex_escape(r["item"])
    msg    = tex_escape(r["message"])
    detail = tex_escape(r.get("detail", ""))
    remark = tex_escape(r.get("remark", ""))
    raw    = (r.get("raw") or "").strip()

    if remark:
        evidence = detail + r"{\newline\normalfont\footnotesize\itshape " + remark + "}"
    else:
        evidence = detail

    main_row = (r"\small " + item + " & \small " + msg + " & " +
                evidence + " & " + result_cell(r["status"]) + r" \\" + "\n" + r"\hline")

    if not raw:
        return main_row

    # Render each frame line as a monospace continuation row spanning all columns
    lines = [tex_escape_raw(ln.strip()) for ln in raw.splitlines() if ln.strip()]
    content = r"\ \newline ".join(lines)
    log_row = (
        r"\multicolumn{4}{|l|}{\cellcolor{gray!8}\footnotesize\ttfamily "
        + content
        + r"} \\" + "\n" + r"\hline"
    )
    return main_row + "\n" + log_row


# ── Main generator ────────────────────────────────────────────────────────────
PREAMBLE = r"""\documentclass[10pt, a4paper]{article}
\usepackage[left=2cm, right=2cm, top=2.5cm, bottom=2.5cm]{geometry}
\usepackage{longtable}
\usepackage{array}
\usepackage{xcolor}
\usepackage{colortbl}
\usepackage{fancyhdr}
\usepackage{booktabs}
\usepackage[hidelinks]{hyperref}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\setlength{\emergencystretch}{3em}

\definecolor{passgreen}{RGB}{34,139,34}
\definecolor{failred}{RGB}{200,0,0}
\definecolor{warnorange}{RGB}{200,100,0}
\definecolor{skipgray}{RGB}{120,120,120}
"""


def generate_tex(sec: int, data: dict) -> str:
    meta   = SECTION_META[sec]
    title_en = meta["title_en"]
    header   = meta["header"]

    results = data["results"]
    counts  = {s: sum(1 for r in results if r["status"] == s)
               for s in ("PASS", "FAIL", "WARN", "SKIP")}
    total   = len(results)
    ts      = data.get("timestamp", "")[:16].replace("T", " ")

    summary_line = (
        f"\\textbf{{Summary: {counts['PASS']} PASS "
        f"\\quad {counts['FAIL']} FAIL "
        f"\\quad {counts['WARN']} WARN "
        f"\\quad {counts['SKIP']} SKIP "
        f"\\quad ({total} total)}}"
    )

    rows = "\n".join(table_row(r) for r in results)

    notes = SECTION_NOTES.get(sec, r"\noindent No additional notes.")

    tex = PREAMBLE
    tex += f"""
\\title{{\\textbf{{MEA OCPP 1.6 Compliance Test Report}}\\\\[0.3em]
\\large {header}: {title_en}\\\\[0.3em]
\\normalsize Vector vSECC.single Board vs MEA CSMS (rddQC4000001)}}
\\author{{MEA-V2G Project --- Automated Test}}
\\date{{{ts} UTC}}

\\pagestyle{{fancy}}
\\fancyhf{{}}
\\lhead{{MEA OCPP 1.6 -- {header}}}
\\rhead{{Page \\thepage}}

\\begin{{document}}
\\maketitle
\\tableofcontents
\\newpage

\\section{{Test Configuration}}
\\begin{{tabular}}{{ll}}
\\textbf{{Device Under Test}} & Vector vSECC.single Board \\\\
\\textbf{{Device IP}}         & 192.168.1.166 \\\\
\\textbf{{OCPP Version}}      & 1.6 (JSON) \\\\
\\textbf{{CP ID}}             & rddQC4000001 \\\\
\\textbf{{CSMS}}              & wss://ocpp.measandbox.com:2930 \\\\
\\textbf{{Connection}}        & Direct TLS (Security Profile 1, no proxy) \\\\
\\textbf{{Test Date}}         & {ts} UTC \\\\
\\textbf{{Results file}}      & tex/vsecc\\_section{sec}\\_results.json \\\\
\\end{{tabular}}

\\subsection*{{Test Architecture}}
The vSECC connects directly to the MEA CSMS at
\\texttt{{wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6/rddQC4000001}}
over TLS with no intermediate proxy.
OCPP traffic is observed via the vSECC's \\texttt{{GET /api/logging/files/ocpplib.log}}
REST endpoint (TRACE-level log, raw \\texttt{{>>>}} sent / \\texttt{{<<<}} received frames)
and via MQTT at \\texttt{{192.168.1.166:1883}} (topic \\texttt{{vsecc/\\#}}).
The test PC NAT-routes internet access for the vSECC subnet (192.168.1.x via enp3s0).

\\section{{{header} Results}}
{summary_line}

\\renewcommand{{\\arraystretch}}{{1.35}}
\\newcolumntype{{L}}[1]{{>{{\\raggedright\\arraybackslash}}p{{#1}}}}
\\newcolumntype{{M}}[1]{{>{{\\small\\ttfamily\\raggedright\\arraybackslash}}p{{#1}}}}
\\newcolumntype{{C}}[1]{{>{{\\centering\\arraybackslash}}p{{#1}}}}
\\begin{{longtable}}{{|L{{1cm}}|L{{5.5cm}}|M{{7cm}}|C{{1.8cm}}|}}
\\hline
\\rowcolor{{gray!25}}
\\textbf{{Item}} & \\small\\textbf{{Test Description}} & \\small\\textbf{{Detail / Evidence}} & \\small\\textbf{{Result}} \\\\
\\hline
\\endfirsthead
\\hline
\\rowcolor{{gray!25}}
\\textbf{{Item}} & \\small\\textbf{{Test Description}} & \\small\\textbf{{Detail / Evidence}} & \\small\\textbf{{Result}} \\\\
\\hline
\\endhead
\\hline
\\endfoot
{rows}
\\end{{longtable}}
\\normalsize

\\section{{Notes}}
{notes}

\\end{{document}}
"""
    return tex


def compile_pdf(tex_path: str):
    tex_dir = os.path.dirname(tex_path)
    result = subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", os.path.basename(tex_path)],
        cwd=tex_dir, capture_output=True
    )
    # second pass for TOC
    subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", os.path.basename(tex_path)],
        cwd=tex_dir, capture_output=True
    )
    out = result.stdout.decode("latin-1", errors="replace")
    errors = [l for l in out.splitlines() if l.startswith("!")]
    return errors


def generate_summary_tex(all_data: dict[int, dict]) -> str:
    """Generate a full summary report combining all sections."""

    # ── Grand totals ──────────────────────────────────────────────────────────
    grand = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
    for data in all_data.values():
        for r in data["results"]:
            grand[r["status"]] += 1
    grand_total = sum(grand.values())

    # ── Section overview table ────────────────────────────────────────────────
    overview_rows = []
    for sec in sorted(all_data.keys()):
        data   = all_data[sec]
        meta   = SECTION_META[sec]
        counts = {s: sum(1 for r in data["results"] if r["status"] == s)
                  for s in ("PASS", "FAIL", "WARN", "SKIP")}
        total  = len(data["results"])
        ts     = data.get("timestamp", "")[:10]
        p = r"\textcolor{passgreen}{\textbf{" + str(counts["PASS"]) + "}}"
        f = (r"\textcolor{failred}{\textbf{" + str(counts["FAIL"]) + "}}"
             if counts["FAIL"] else str(counts["FAIL"]))
        w = (r"\textcolor{warnorange}{\textbf{" + str(counts["WARN"]) + "}}"
             if counts["WARN"] else str(counts["WARN"]))
        sk = (r"\textcolor{skipgray}{" + str(counts["SKIP"]) + "}"
              if counts["SKIP"] else str(counts["SKIP"]))
        overview_rows.append(
            rf"\small {sec} & \small {meta['title_en']} & "
            rf"{p} & {f} & {w} & {sk} & \small {total} & \small {ts} \\"
            + r" \hline"
        )

    # grand total row
    gp = r"\textcolor{passgreen}{\textbf{" + str(grand["PASS"]) + "}}"
    gf = (r"\textcolor{failred}{\textbf{" + str(grand["FAIL"]) + "}}"
          if grand["FAIL"] else str(grand["FAIL"]))
    gw = (r"\textcolor{warnorange}{\textbf{" + str(grand["WARN"]) + "}}"
          if grand["WARN"] else str(grand["WARN"]))
    gsk = (r"\textcolor{skipgray}{" + str(grand["SKIP"]) + "}"
           if grand["SKIP"] else str(grand["SKIP"]))
    overview_rows.append(
        r"\rowcolor{gray!15}\small \textbf{All} & \small \textbf{Grand Total} & "
        rf"{gp} & {gf} & {gw} & {gsk} & \small \textbf{{{grand_total}}} & \\ \hline"
    )

    overview_table = "\n".join(overview_rows)

    # ── Per-section detail blocks ─────────────────────────────────────────────
    section_blocks = []
    for sec in sorted(all_data.keys()):
        data   = all_data[sec]
        meta   = SECTION_META[sec]
        counts = {s: sum(1 for r in data["results"] if r["status"] == s)
                  for s in ("PASS", "FAIL", "WARN", "SKIP")}
        total  = len(data["results"])
        rows   = "\n".join(table_row(r) for r in data["results"])
        notes  = SECTION_NOTES.get(sec, "")
        summary_line = (
            f"{counts['PASS']} PASS \\quad "
            f"{counts['FAIL']} FAIL \\quad "
            f"{counts['WARN']} WARN \\quad "
            f"{counts['SKIP']} SKIP \\quad "
            f"({total} total)"
        )
        block = rf"""
\newpage
\section{{Section {sec}: {meta['title_en']}}}
\textbf{{{summary_line}}}

\renewcommand{{\arraystretch}}{{1.35}}
\begin{{longtable}}{{|L{{1cm}}|L{{5.5cm}}|M{{7cm}}|C{{1.8cm}}|}}
\hline
\rowcolor{{gray!25}}
\textbf{{Item}} & \small\textbf{{Test Description}} & \small\textbf{{Detail / Evidence}} & \small\textbf{{Result}} \\
\hline
\endfirsthead
\hline
\rowcolor{{gray!25}}
\textbf{{Item}} & \small\textbf{{Test Description}} & \small\textbf{{Detail / Evidence}} & \small\textbf{{Result}} \\
\hline
\endhead
\hline
\endfoot
{rows}
\end{{longtable}}
\normalsize
\subsection*{{Notes}}
{notes}
"""
        section_blocks.append(block)

    detail_blocks = "\n".join(section_blocks)

    # ── Full document ─────────────────────────────────────────────────────────
    today = __import__("datetime").date.today().isoformat()
    tex = PREAMBLE + rf"""
\title{{\textbf{{MEA OCPP 1.6 Compliance Test Report}}\\[0.3em]
\large Full Summary --- All Sections (1--11)\\[0.3em]
\normalsize Vector vSECC.single Board vs MEA CSMS (rddQC4000001)}}
\author{{MEA-V2G Project --- Automated Test}}
\date{{{today}}}

\pagestyle{{fancy}}
\fancyhf{{}}
\lhead{{MEA OCPP 1.6 -- Full Summary}}
\rhead{{Page \thepage}}

\begin{{document}}
\maketitle
\tableofcontents
\newpage

\section{{Test Configuration}}
\begin{{tabular}}{{ll}}
\textbf{{Device Under Test}} & Vector vSECC.single Board \\
\textbf{{Device IP}}         & 192.168.1.166 \\
\textbf{{OCPP Version}}      & 1.6 (JSON) \\
\textbf{{CP ID}}             & rddQC4000001 \\
\textbf{{CSMS}}              & wss://ocpp.measandbox.com:2930 \\
\textbf{{Connection}}        & Direct TLS (Security Profile 1, no proxy) \\
\textbf{{Report Generated}}  & {today} \\
\end{{tabular}}

\subsection*{{Test Architecture}}
The vSECC connects directly to the MEA CSMS over TLS with no intermediate proxy.
OCPP traffic is observed via \texttt{{GET /api/logging/files/ocpplib.log}} (TRACE-level,
raw \texttt{{>>>}} sent / \texttt{{<<<}} received frames) and via MQTT at
\texttt{{192.168.1.166:1883}} (\texttt{{vsecc/\#}}).
The test PC NAT-routes internet access for the vSECC subnet (192.168.1.x via enp3s0).

\section{{Overall Summary}}
\textbf{{Grand total: {grand['PASS']} PASS \quad {grand['FAIL']} FAIL \quad {grand['WARN']} WARN \quad {grand['SKIP']} SKIP \quad ({grand_total} items across all sections)}}

\renewcommand{{\arraystretch}}{{1.3}}
\newcolumntype{{L}}[1]{{>{{\raggedright\arraybackslash}}p{{#1}}}}
\newcolumntype{{M}}[1]{{>{{\small\ttfamily\raggedright\arraybackslash}}p{{#1}}}}
\newcolumntype{{C}}[1]{{>{{\centering\arraybackslash}}p{{#1}}}}
\begin{{longtable}}{{|C{{0.8cm}}|L{{5.5cm}}|C{{1.2cm}}|C{{1.2cm}}|C{{1.2cm}}|C{{1.2cm}}|C{{1.0cm}}|C{{1.5cm}}|}}
\hline
\rowcolor{{gray!25}}
\small\textbf{{Sec}} & \small\textbf{{Section Title}} &
\small\textbf{{PASS}} & \small\textbf{{FAIL}} & \small\textbf{{WARN}} & \small\textbf{{SKIP}} &
\small\textbf{{Total}} & \small\textbf{{Date}} \\
\hline
\endfirsthead
\hline
\rowcolor{{gray!25}}
\small\textbf{{Sec}} & \small\textbf{{Section Title}} &
\small\textbf{{PASS}} & \small\textbf{{FAIL}} & \small\textbf{{WARN}} & \small\textbf{{SKIP}} &
\small\textbf{{Total}} & \small\textbf{{Date}} \\
\hline
\endhead
\hline
\endfoot
{overview_table}
\end{{longtable}}
\normalsize

{detail_blocks}

\end{{document}}
"""
    return tex


def main():
    sections = sorted(SECTION_META.keys())
    if len(sys.argv) > 1:
        sections = [int(a) for a in sys.argv[1:]]

    all_data = {}

    for sec in sections:
        json_path = os.path.join(TEX_DIR, f"vsecc_section{sec}_results.json")
        tex_path  = os.path.join(TEX_DIR, f"vsecc_section{sec}_report.tex")
        pdf_path  = os.path.join(TEX_DIR, f"vsecc_section{sec}_report.pdf")

        if not os.path.exists(json_path):
            print(f"  SKIP  Section {sec}: no results JSON")
            continue

        with open(json_path) as f:
            data = json.load(f)

        all_data[sec] = data

        tex = generate_tex(sec, data)
        with open(tex_path, "w") as f:
            f.write(tex)

        print(f"  Compiling Section {sec}...", end=" ", flush=True)
        errors = compile_pdf(tex_path)
        if errors:
            print(f"ERRORS: {errors}")
        else:
            size = os.path.getsize(pdf_path) // 1024
            print(f"OK ({size} KB)")

        for ext in (".aux", ".log", ".toc", ".out"):
            aux = tex_path.replace(".tex", ext)
            if os.path.exists(aux):
                os.remove(aux)

    # ── Summary report ────────────────────────────────────────────────────────
    if all_data:
        summary_tex_path = os.path.join(TEX_DIR, "vsecc_summary_report.tex")
        summary_pdf_path = os.path.join(TEX_DIR, "vsecc_summary_report.pdf")
        tex = generate_summary_tex(all_data)
        with open(summary_tex_path, "w") as f:
            f.write(tex)
        print("  Compiling Summary report...", end=" ", flush=True)
        errors = compile_pdf(summary_tex_path)
        if errors:
            print(f"ERRORS: {errors}")
        else:
            size = os.path.getsize(summary_pdf_path) // 1024
            print(f"OK ({size} KB)")
        for ext in (".aux", ".log", ".toc", ".out"):
            aux = summary_tex_path.replace(".tex", ext)
            if os.path.exists(aux):
                os.remove(aux)

    print("Done.")


if __name__ == "__main__":
    main()
