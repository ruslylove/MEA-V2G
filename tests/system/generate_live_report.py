import subprocess
import re
import os
import datetime

# Configuration
TEST_SCRIPT = "tests/system/test_mea_live.py"
LOG_FILE = "tex/mea_live_test.log"
OUTPUT_TEX = "tex/mea_live_report.tex"
TEMPLATE_HEADER = r"""\documentclass[10pt, a4paper, landscape]{article}
\usepackage[left=1cm, right=1cm, top=2cm, bottom=2cm]{geometry}
\usepackage{longtable}
\usepackage{array}
\usepackage{xcolor}
\usepackage{colortbl}
\usepackage{fancyhdr}
\usepackage{lscape}
\usepackage{verbatim}
\usepackage{fancyvrb}
\usepackage{listings}
\usepackage[hidelinks]{hyperref}

\title{\textbf{MEA Live Test Report}}
\author{MEA OCPP Certification (Automated)}
\date{\today}

\pagestyle{fancy}
\fancyhf{}
\lhead{MEA Compliance Test Report - Live System}
\rhead{Page \thepage}

\begin{document}

\maketitle

\section*{MEA Live Test Execution Results}

\renewcommand{\arraystretch}{1.3}
\begin{longtable}{|p{1.0cm}|p{2.5cm}|p{4.0cm}|p{5.0cm}|p{10.0cm}|c|}
\hline
\rowcolor{gray!30}
\textbf{Item} & \textbf{Direction} & \textbf{Message} & \textbf{Requirement / Include} & \textbf{Log Message / Proof} & \textbf{Result} \\
\hline
\endfirsthead

\hline
\hline
\rowcolor{gray!30}
\textbf{Item} & \textbf{Direction} & \textbf{Message} & \textbf{Requirement / Include} & \textbf{Log Message / Proof} & \textbf{Result} \\
\hline
\endhead

\hline
\endfoot
"""

TEMPLATE_FOOTER = r"""
\end{longtable}
\newpage
\appendix
\section{Raw Test Logs}
\label{sec:raw_logs}
\normalsize
\lstinputlisting[
    breaklines=true,
    basicstyle=\normalsize\ttfamily,
    columns=fullflexible,
    keepspaces=true,
    breakatwhitespace=false,
    frame=single,
    rulecolor=\color{gray!30},
    numbers=left,
    numberstyle=\tiny\color{gray},
    stepnumber=1,
    numbersep=5pt,
    escapechar=|
]{mea_live_test.log}

\end{document}
"""

def run_test():
    print(f"Running {TEST_SCRIPT}...")
    # Use unbuffered output to capture real-time, though for file writing we just capture all at once
    try:
        # Run using the virtual environment python if available
        python_exec = ".venv/bin/python3" if os.path.exists(".venv/bin/python3") else "python3"
        
        result = subprocess.run(
            [python_exec, TEST_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        
        # Strip ANSI codes from the output before writing/processing
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        clean_stdout = ansi_escape.sub('', result.stdout)
        
        with open(LOG_FILE, "w") as f:
            f.write(clean_stdout)
            if result.stderr:
                f.write("\n--- STDERR ---\n")
                f.write(result.stderr)
        
        return clean_stdout.splitlines()
    except Exception as e:
        print(f"Error running test: {e}")
        return []

def parse_logs(log_lines):
    rows = []
    current_section = "General"
    
    # Buffer to hold logs between steps
    log_buffer = []
    
    # Regex for test steps: "X.Y Step Name: Result" 
    step_pattern = re.compile(r"^(?:(PASS|FAIL|WARN): )?(\d+\.\d+)\s+(.+?):\s+(.+)$")
    section_pattern = re.compile(r"^---\s+(\d+\.\s+.*?)\s+---$")
    # Regex for OCPP messages
    ocpp_pattern = re.compile(r"INFO:ocpp:.*?: (send|receive message) (\[.*\])")

    for i, raw_line in enumerate(log_lines):
        line_num = i + 1
        line = raw_line.strip() # ANSI codes already stripped in run_test
        
        # Check for Section Header
        sec_match = section_pattern.match(line)
        if sec_match:
            current_section = sec_match.group(1)
            rows.append({
                "type": "section",
                "title": current_section
            })
            log_buffer = [] # Clear buffer on new section
            continue

        # Check for Test Step
        step_match = step_pattern.match(line)
        if step_match:
            prefix, item, message, result_text = step_match.groups()
            
            # Determine overall pass/fail
            status = "Pass"
            if prefix == "FAIL" or "FAIL" in line:
                status = "Fail"
            elif prefix == "WARN" or "WARN" in line or "timeout" in line.lower():
                status = "Warn"
            
            # extract OCPP messages from buffer
            ocpp_messages = []
            for buf_line in log_buffer:
                ocpp_match = ocpp_pattern.search(buf_line)
                if ocpp_match:
                    direction_grp = ocpp_match.group(1) # "send" or "receive message"
                    json_payload = ocpp_match.group(2)
                    
                    # Determine Role
                    role = "[CS]" if "send" in direction_grp else "[CSMS]"
                    role_fmt = f"\\textbf{{{role}}}"
                    
                    # Format JSON for wrapping (Insert separate spaces)
                    # Escape LaTeX special characters first
                    safe_json = json_payload.replace("\\", "\\\\").replace("{", r"\{").replace("}", r"\}").replace("_", r"\_").replace("#", r"\#").replace("&", r"\&").replace("%", r"\%").replace("$", r"\$")
                    
                    # Improve wrapping by adding spaces after commas and braces
                    wrapped_json = safe_json.replace(",", ", ").replace("\{", "\\{ ").replace("\}", " \\}")
                    
                    ocpp_messages.append(f"{role_fmt} {wrapped_json}")
            
            formatted_ocpp = ""
            if ocpp_messages:
                # Join with newline
                formatted_ocpp = r" \newline ".join(ocpp_messages)
                formatted_ocpp = r"\newline " + formatted_ocpp
            
            # Clean up message and ESCAPE LATEX SPECIAL CHARACTERS
            display_message = message.strip().replace("_", r"\_")
            
            # Construct log link
            log_link = f"\\hyperref[lines.{line_num}]{{\\textbf{{[Log: \\#{line_num}]}}}}"
            
            # Direction (Inferred)
            direction = "CS <-> CSMS" # Default
            if "BootNotification" in message or "StatusNotification" in message or "Heartbeat" in message or "MeterValues" in message or "StartTransaction" in message or "StopTransaction" in message:
                 direction = r"CS $\to$ CSMS"
            elif "Remote" in message or "Reserve" in message or "Cancel" in message or "ChangeConfiguration" in message or "GetConfiguration" in message or "Reset" in message or "SetChargingProfile" in message or "Trigger" in message or "Unlock" in message:
                 direction = r"CSMS $\to$ CS"

            rows.append({
                "type": "row",
                "item": item,
                "direction": direction,
                "message": display_message,
                "requirement": result_text,
                "log_message": f"\\footnotesize\\texttt{{{formatted_ocpp} \\newline {log_link}}}",
                "result": status
            })
            
            log_buffer = [] # Clear buffer after attaching logs to this step
        else:
            # If not a step or section, check if it looks like an OCPP log to save
            # Or just save everything? Saving everything is safer but might get noisy.
            # Just save lines that match ocpp_pattern?
            # Or just save all lines to ensure we don't miss anything relevant in context?
            # Let's save all lines but limit buffer size to prevent memory issues (though small logs here)
            log_buffer.append(line)

    return rows

def generate_tex(rows):
    content = TEMPLATE_HEADER
    
    for row in rows:
        if row["type"] == "section":
             content += f"\\multicolumn{{6}}{{|l|}}{{\\cellcolor{{blue!10}}\\textbf{{{row['title']}}}}} \\\\\n\\hline\n"
        else:
            # Color code result
            result_display = row["result"]
            if row["result"] == "Fail":
                result_display = r"\textcolor{red}{Fail}"
            elif row["result"] == "Warn":
                 result_display = r"\textcolor{orange}{Warn}"
            
            content += f"{row['item']} & {row['direction']} & {row['message']} & {row['requirement']} & {row['log_message']} & {result_display} \\\\\n\\hline\n"

    content += TEMPLATE_FOOTER
    
    with open(OUTPUT_TEX, "w") as f:
        f.write(content)
    print(f"Generated report at {OUTPUT_TEX}")

def main():
    print("Starting MEA Live Report Generation...")
    log_lines = run_test()
    if not log_lines:
        print("No log lines captured. Exiting.")
        return

    rows = parse_logs(log_lines)
    generate_tex(rows)
    print("Done.")

if __name__ == "__main__":
    main()
