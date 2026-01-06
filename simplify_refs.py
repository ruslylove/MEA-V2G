import re

def main():
    tex_path = "tex/mea_compliance_report.tex"
    log_path = "tex/mea_full_test.log"
    
    # 1. Build map of MockSystemTest headers: TestID -> LineNumber
    header_map = {}
    # Pattern: INFO:MockSystemTest:<ID> <Description>
    # e.g. INFO:MockSystemTest:1.3 Trigger...
    # We strip trailing text to just get ID
    header_pattern = re.compile(r'INFO:MockSystemTest:\s*(\d+(\.\d+)+)\b')
    
    with open(log_path, 'r') as f:
        log_lines = f.readlines()
        
    for i, line in enumerate(log_lines):
        match = header_pattern.search(line)
        if match:
            t_id = match.group(1)
            # Store 1-based line number replacement
            # Note: If duplicate IDs exist (e.g. 7.2.1 appears twice in log), 
            # we generally want the first one or we need to be smart.
            # In the grep output, 7.2.1 appeared at 1046 and 1059. 
            # The report likely refers to them sequentially? 
            # Actually, standard behavior for dictionary is last-write wins or first?
            # Let's keep a list if multiple.
            if t_id not in header_map:
                header_map[t_id] = []
            header_map[t_id].append(i + 1)

    # 2. Update TeX file
    with open(tex_path, 'r') as f:
        lines = f.readlines()
        
    updates = 0
    # Pattern to find test case rows in TeX
    # 7.2.1 & ... & ... & ... & ... [Log: #XXX] or [Log: \#XXX]
    row_pattern = re.compile(r'^\s*(\d+(\.\d+)+)\s*&')
    # Handle optional backslash before #
    # Match generic [Log: ...] content, capturing the first number if present
    log_tag_pattern = re.compile(r'\\textbf\{\[Log: .*?#(\d+).*?\]\}')
    
    new_lines = []
    
    # Track occurrence count for duplicate IDs in TeX?
    # Usually TeX IDs are unique per row (except maybe table split? No).
    # But if log has multiple 7.2.1s, we need to know which one.
    # Heuristic: Match usage order? 
    # Let's check 7.2.1 duplication in grep:
    # 1046:INFO:MockSystemTest:7.2.1 Status: Preparing
    # 1059:INFO:MockSystemTest:7.2.1 Status: Unplugged
    # In table, 7.2.1 is usually "Available" or "Preparing"?
    # Table 7.2.1 is "StatusNotification | Available". 
    # Table 7.2.2 is "StatusNotification | Preparing".
    # Wait, the log 7.2.1 at 1046 says Preparing.
    # This implies the Log ID might not perfectly match the Table ID semantics 1:1 in all cases,
    # or the test runner re-uses IDs.
    # However, the user asked to use "MockSystemTest:X.X.X" as ref.
    # Safe bet: Use the header line. If multiple, maybe closest to current ref?
    # Or just the first one if unsure.
    
    for line in lines:
        row_match = row_pattern.search(line)
        if row_match:
            t_id = row_match.group(1)
            
            if t_id in header_map:
                candidates = header_map[t_id]
                
                # If only one candidate, easy.
                if len(candidates) == 1:
                    new_ref = candidates[0]
                else:
                    # Multiple headers in log. Find closest to current ref if existing ref exists.
                    curr_ref_match = log_tag_pattern.search(line)
                    if curr_ref_match:
                        curr_ref = int(curr_ref_match.group(1))
                        # Find candidate with min dist
                        new_ref = min(candidates, key=lambda x: abs(x - curr_ref))
                    else:
                        new_ref = candidates[0]
                
                if log_tag_pattern.search(line):
                     # Replace with unescaped # or escaped \# depending on style? 
                     # Pattern to replace entire Log block including multi-refs
                     # [Log: \#123, \#456] -> [Log: \#New]
                     # Regex: \\textbf\{\[Log: .*?\]\}
                     new_line = re.sub(r'\\textbf\{\[Log: .*?\]\}', f"\\\\textbf{{[Log: \\\\#{new_ref}]}}", line)
                     if new_line != line:
                         updates += 1
                     new_lines.append(new_line)
                else:
                    new_lines.append(line)
            else:
                # No specific header found, keep existing
                new_lines.append(line)
        else:
            new_lines.append(line)
            
    with open(tex_path, 'w') as f:
        f.writelines(new_lines)
        
    print(f"Simplified {updates} references to point to MockSystemTest headers.")

if __name__ == "__main__":
    main()
