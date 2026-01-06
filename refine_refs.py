import re

def parse_tex_test_cases(tex_path):
    """
    Parses the TeX file to find test cases, directions, and message types.
    Returns: list of dicts {id, direction, message, line_idx}
    """
    test_cases = []
    # Pattern to match table row: "1.1 & CS -> CSMS & BootNotification & ..."
    # We need to handle arrow symbols $\to$, $\leftrightarrow$ etc.
    # Group 1: ID, Group 2: Direction (raw), Group 3: Message
    pattern = re.compile(r'^\s*(\d+(\.\d+)+)\s*&\s*(.*?)\s*&\s*(.*?)\s*&')
    
    with open(tex_path, 'r') as f:
        lines = f.readlines()
        
    for i, line in enumerate(lines):
        match = pattern.search(line)
        if match:
            t_id = match.group(1)
            raw_dir = match.group(3)
            raw_msg = match.group(4)
            
            # Normalize direction
            if "CSMS" in raw_dir and "CS" in raw_dir:
                if r"\to" in raw_dir or "->" in raw_dir:
                    if raw_dir.startswith("CSMS"):
                        direction = "CSMS_TO_CS"
                    else:
                        direction = "CS_TO_CSMS"
                elif r"\leftrightarrow" in raw_dir:
                    direction = "BIDIRECTIONAL"
                else:
                    direction = "UNKNOWN"
            else:
                 direction = "UNKNOWN"
            
            # Normalize message (remove extra txt)
            # e.g. "StatusNotification (Plug)" -> "StatusNotification"
            clean_msg = re.sub(r'\s*\(.*?\)', '', raw_msg).strip()
            
            test_cases.append({
                'id': t_id,
                'direction': direction,
                'message': clean_msg,
                'tex_line_idx': i,
                'tex_line_content': line
            })
            
    return test_cases, lines

def find_exact_log_line(log_lines, test_id, direction, message, marker_line_num):
    """
    Searches around the marker_line_num (index) for the exact message log.
    Returns: line number (1-based) or None.
    """
    # Define search window around the marker (which we know exists at marker_line_num)
    # The marker is usually "INFO:MockSystemTest:1.1 ..."
    # The actual message could be before or after.
    # We'll search -20 to +20 lines.
    
    start_idx = max(0, marker_line_num - 25)
    end_idx = min(len(log_lines), marker_line_num + 25)
    
    # Target patterns
    # CS -> CSMS: "INFO:MockSystemTest:[CSMS] <- <Message>"
    # CSMS -> CS: "INFO:MockSystemTest:[CSMS] -> <Message>"
    
    target_pattern = None
    if direction == "CS_TO_CSMS":
        target_pattern = f"INFO:MockSystemTest:[CSMS] <- {message}"
    elif direction == "CSMS_TO_CS":
        target_pattern = f"INFO:MockSystemTest:[CSMS] -> {message}"
    
    if not target_pattern:
        return None
        
    # Search in window
    best_line = None
    
    # Heuristic: Find closest to marker
    min_dist = 1000
    
    for i in range(start_idx, end_idx):
        if target_pattern in log_lines[i]:
            dist = abs(i - marker_line_num)
            if dist < min_dist:
                min_dist = dist
                best_line = i + 1 # 1-based
                
    return best_line

def main():
    tex_path = "tex/mea_compliance_report.tex"
    log_path = "tex/mea_full_test.log"
    
    print("Reading files...")
    test_cases, tex_lines = parse_tex_test_cases(tex_path)
    
    with open(log_path, 'r') as f:
        log_lines = f.readlines()
        
    # Build map of MockSystemTest markers to find initial anchors
    # Map TestID -> List of line indices (0-based)
    marker_map = {}
    marker_pattern = re.compile(r'INFO:MockSystemTest:\s*(\d+(\.\d+)+)\b')
    
    for i, line in enumerate(log_lines):
        match = marker_pattern.search(line)
        if match:
            t_id = match.group(1)
            if t_id not in marker_map:
                marker_map[t_id] = []
            marker_map[t_id].append(i)
            
    # Process
    updates = 0
    for tc in test_cases:
        t_id = tc['id']
        direction = tc['direction']
        message = tc['message']
        
        if t_id not in marker_map:
            print(f"Warning: No log marker found for {t_id}")
            continue
            
        # Use the first marker found for this ID? Or closest?
        # Usually first marker is fine for distinct test cases.
        marker_idx = marker_map[t_id][0]
        
        exact_line = find_exact_log_line(log_lines, t_id, direction, message, marker_idx)
        
        if exact_line:
            # Update TeX line
            current_line = tex_lines[tc['tex_line_idx']]
            
            # Replace [Log: #XXX]
            # Pattern: \textbf{[Log: #123 (Re-run)]} or similar
            # Robust regex to capture the whole tag
            new_tag = f"\\\\textbf{{[Log: \\#{exact_line}]}}"
            
            # Check if current ref is different
            # (Simple string check might be enough to skip writes, but let's just sub)
            
            new_line = re.sub(r'\\textbf\{\[Log:.*?\]\}', new_tag, current_line)
            
            if new_line != current_line:
                tex_lines[tc['tex_line_idx']] = new_line
                print(f"Refined {t_id} ({message}): Log #{exact_line}")
                updates += 1
            else:
                # print(f"Skipping {t_id}: already correct at #{exact_line}")
                pass
        else:
            print(f"Could not find exact log line for {t_id} {message} around marker {marker_idx+1}")
            
    if updates > 0:
        with open(tex_path, 'w') as f:
            f.writelines(tex_lines)
        print(f"Saved {updates} updates to {tex_path}")
    else:
        print("No updates needed.")

if __name__ == "__main__":
    main()
