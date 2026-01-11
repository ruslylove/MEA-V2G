import xml.etree.ElementTree as ET
import glob
import sys

def merge_xmls(pattern, output_file):
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"No files found for pattern: {pattern}")
        return

    root = ET.Element('testsuites')
    combined_time = 0.0
    
    for f in files:
        try:
            tree = ET.parse(f)
            r = tree.getroot()
            # If root is testsuite, append it
            if r.tag == 'testsuite':
                combined_time += float(r.attrib.get('time', '0'))
                root.append(r)
            elif r.tag == 'testsuites':
                for s in r.findall('testsuite'):
                    combined_time += float(s.attrib.get('time', '0'))
                    root.append(s)
        except Exception as e:
            print(f"Error parsing {f}: {e}")

    root.attrib['time'] = str(combined_time)
    tree = ET.ElementTree(root)
    tree.write(output_file, encoding='utf-8', xml_declaration=True)
    print(f"Merged {len(files)} files into {output_file}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 merge_xml.py <glob_pattern> <output_file>")
        sys.exit(1)
    
    merge_xmls(sys.argv[1], sys.argv[2])
