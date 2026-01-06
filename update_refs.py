import re
import os

def process_logs_and_create_tex_log(source_log, target_log):
    """
    Reads source_log, finds markers, injects labels, writes to target_log.
    Returns map {test_id: line_number}.
    """
    log_map = {}
    # Pattern to match the Test ID in the log
    pattern = re.compile(r'INFO:MockSystemTest:\s*(\d+\.\d+(\.\d+)?)\b')
    
    print(f"Processing {source_log} -> {target_log}")
    
    try:
        with open(source_log, 'r') as f_in, open(target_log, 'w') as f_out:
            for i, line in enumerate(f_in, 1):
                match = pattern.search(line)
                if match:
                    test_id = match.group(1)
                    if test_id not in log_map:
                        log_map[test_id] = i
                        # Inject label. Using | as escape char defined in tex.
                        # We append it to the end of the line.
                        # Note: line already has \n at end usually.
                        line = line.rstrip() + f" |\\label{{log:{test_id}}}|\n"
                f_out.write(line)
        print(f"Generated {target_log} with {len(log_map)} labeled anchor points.")
    except FileNotFoundError:
        print(f"Error: Log file not found at {source_log}")
        return {}
        
    return log_map

def update_tex_file(tex_path, log_map):
    print(f"Updating references in {tex_path}")
    new_lines = []
    # Regex for start of row in the table, e.g. "1.1 &"
    row_start_pattern = re.compile(r'^\s*(\d+\.\d+(\.\d+)?)\s*&')
    
    # Regexs for replacing the Log reference
    # 1. Existing Hyperref: \hyperref[log:1.1]{\textbf{[Log: #123]}}
    # 2. Plain Bold: \textbf{[Log: #123]}
    # We use non-greedy matches for content inside [] and {}
    
    hyperref_regex = r'\\hyperref\[log:.*?\]\{\\textbf\{\[Log:.*?\]\}\}'
    bold_regex = r'\\textbf\{\[Log:.*?\]\}'
    
    # Combine them, prioritizing the longer hyperref match suitable for "sub"
    combined_pattern = re.compile(f"({hyperref_regex}|{bold_regex})")
    
    current_test_id = None
    
    try:
        with open(tex_path, 'r') as f:
            lines = f.readlines()
            
        updated_count = 0
        for line in lines:
            # Check if this line starts a new test case row
            match = row_start_pattern.match(line)
            if match:
                current_test_id = match.group(1)
            
            # If we are inside a test case row (or multiline), check for Log ref
            # We look for '[Log:' to be sure we should attempt replacement
            if current_test_id and '[Log:' in line:
                if current_test_id in log_map:
                    new_line_num = log_map[current_test_id]
                    
                    # Construct the new reference string
                    replacement = f"\\\\hyperref[log:{current_test_id}]{{\\\\textbf{{[Log: \\#{new_line_num}]}}}}"
                    
                    # Perform substitution
                    # This replaces either the existing hyperref or the plain bold text
                    new_line = combined_pattern.sub(replacement, line)
                    if new_line != line:
                        updated_count += 1
                    line = new_line
                else:
                    # Optional: Print warning only if it looks like it EXPECTS a log
                    # e.g. contains [Log: ?]
                    pass
            
            new_lines.append(line)
            
        with open(tex_path, 'w') as f:
            f.writelines(new_lines)
            print(f"Successfully updated {updated_count} references in {tex_path}")
            
    except FileNotFoundError:
        print(f"Error: Tex file not found at {tex_path}")

if __name__ == "__main__":
    log_src = "test_output.log"
    log_dst = "tex/mea_full_test.log"
    tex_src = "tex/mea_compliance_report.tex"
    
    print("Step 1: Processing Logs...")
    logs = process_logs_and_create_tex_log(log_src, log_dst)
    
    if logs:
        print("Step 2: Updating TeX Report...")
        update_tex_file(tex_src, logs)
    else:
        print("Skipping TeX update due to empty log map.")
