import re

def main():
    tex_path = "tex/mea_compliance_report.tex"
    
    with open(tex_path, 'r') as f:
        lines = f.readlines()
        
    current_ref = -1
    last_id = "Start"
    
    # Regex to capture ID and Log Ref
    # We might find the ID on one line and the Log Ref on the same or subsequent lines
    # But usually they are in the same block.
    # Let's iterate line by line.
    
    id_pattern = re.compile(r'^\s*(\d+(\.\d+)+)\s*&')
    log_pattern = re.compile(r'Log: \\\\?#(\d+)')
    
    test_id = "Unknown"
    
    issues = []
    
    for line_num, line in enumerate(lines, 1):
        # Check for Test ID
        m_id = id_pattern.search(line)
        if m_id:
            test_id = m_id.group(1)
            
        # Check for Log Ref
        m_log = log_pattern.search(line)
        if m_log:
            ref = int(m_log.group(1))
            if ref < current_ref:
                issues.append(f"Details: {test_id} (Ref #{ref}) < {last_id} (Ref #{current_ref}) at line {line_num}")
            
            current_ref = ref
            last_id = test_id
            
    if issues:
        print(f"Found {len(issues)} out-of-order references:")
        for issue in issues:
            print(issue)
    else:
        print("All references are in order.")

if __name__ == "__main__":
    main()
