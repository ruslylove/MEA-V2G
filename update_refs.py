import re

def parse_log_file(log_path):
    """
    Parses the log file and returns a dictionary mapping Test Case ID (str) to Line Number (int).
    """
    log_map = {}
    # Regex to find test case headers like "2.14 ", "10.1 " in MockSystemTest logs
    # Pattern: INFO:MockSystemTest:<ID> <Description>
    # or just look for the ID followed by space
    pattern = re.compile(r'INFO:MockSystemTest:\s*(\d+\.\d+(\.\d+)?)\b')
    
    try:
        with open(log_path, 'r') as f:
            for i, line in enumerate(f, 1):
                match = pattern.search(line)
                if match:
                    test_id = match.group(1)
                    # We only take the first occurrence for each test ID?
                    # Some might be repeated (e.g. status updates). 
                    # Usually the first one is the start/main event or the validation PASS/FAIL.
                    # Let's take the first one found, or if it says "PASS" or "FAIL" prioritize that?
                    # Refined strategy: If we find a PASS/FAIL/Accepted line with ID, update it. 
                    # Otherwise keep the first one.
                    # Actually, usually the log marker "X.Y Description" is the best anchor.
                    if test_id not in log_map:
                         log_map[test_id] = i
                    else:
                         # If we already have it, do we overwrite?
                         # Only if this line looks "better" e.g. contains "PASS" or "Accepted"?
                         # For compliance, pointing to the *Command Sent* or *Response Received* is key.
                         # The MockSystemTest logs are usually summaries *after* the event or *at* the event.
                         # Let's just keep the FIRST occurrence for now, as it usually marks the start of the step action.
                         pass
    except FileNotFoundError:
        print(f"Error: Log file not found at {log_path}")
    
    return log_map

def update_tex_file(tex_path, log_map):
    """
    Reads the LaTeX file, updates [Log: #XXX] references with new line numbers from log_map.
    """
    new_lines = []
    # Regex to capture the table row start: "X.Y &"
    row_start_pattern = re.compile(r'^\s*(\d+\.\d+(\.\d+)?)\s*&')
    # Regex to replace [Log: #...]
    log_ref_pattern = re.compile(r'\\textbf\{\[Log: (.*\?)?\#\d+.*?\]\}')
    
    current_test_id = None
    
    try:
        with open(tex_path, 'r') as f:
            lines = f.readlines()
            
        for line in lines:
            # Check if this line starts a new test case row
            match = row_start_pattern.match(line)
            if match:
                current_test_id = match.group(1)
            
            # If we are inside a test case row (or multiline), check for Log ref
            if current_test_id and r'\textbf{[Log:' in line:
                if current_test_id in log_map:
                    new_line_num = log_map[current_test_id]
                    # Replace using regex to preserve surrounding formatting matches if complex
                    # We want to replace "[Log: #...]" with "[Log: #<new_line>]"
                    # But wait, existing format is `\textbf{[Log: \#123]}` or `\textbf{[Log: Verified...]}`
                    
                    # Construct replacement string
                    replacement = f"\\\\textbf{{[Log: \\#{new_line_num}]}}"
                    
                    # Perform substitution
                    # We need to be careful not to replace OTHER numbers if regex is loose
                    # The pattern `\\textbf\{\[Log: .*?\]\}` should catch the whole bold block.
                    # Inside that block we want `[Log: #<new_num>]`
                    
                    # Let's simplify: replace the whole \textbf{[Log: ...]} block
                    line = re.sub(r'\\textbf\{\[Log:.*?\]\}', replacement, line)
                    
                    print(f"Updated {current_test_id} to Log #{new_line_num}")
                else:
                    print(f"Warning: No log line found for Test Case {current_test_id}")
            
            new_lines.append(line)
            
        # Write back
        with open(tex_path, 'w') as f:
            f.writelines(new_lines)
            print(f"Successfully updated {tex_path}")
            
    except FileNotFoundError:
        print(f"Error: Tex file not found at {tex_path}")

if __name__ == "__main__":
    log_file = "test_output.log"
    tex_file = "tex/mea_compliance_report.tex"
    
    print("Parsing log file...")
    logs = parse_log_file(log_file)
    print(f"Found {len(logs)} test markers.")
    
    print("Updating Report...")
    update_tex_file(tex_file, logs)
