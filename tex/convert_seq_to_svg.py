import os
import glob
import subprocess
import shutil

# Configuration
TEX_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(os.path.dirname(TEX_DIR), 'img')

# Ensure img directory exists
os.makedirs(IMG_DIR, exist_ok=True)

# Patterns for diagrams
patterns = [
    'seq_*.tex',
    'ocpp_state_v2g.tex',
    'v2g_extension_sequence.tex',
    'evse_ocpp_fsm.tex',
    'evse_state_diagram.tex',
    'ocpp_state_diagram.tex',
    'system_architecture.tex',
    'white_beet_block.tex',
    'charger_interface_class_diagram.tex',
    'evse_class_diagram.tex',
    'mea_csms_mock_setup.tex',
    'mea_ocpp_compliance_setup.tex',
    'mea_uat_setup.tex'
]

tex_files = []
for p in patterns:
    tex_files.extend(glob.glob(os.path.join(TEX_DIR, p)))

# Filter duplicates and ensure files exist
tex_files = sorted(list(set(tex_files)))

print(f"Found {len(tex_files)} diagram files to process.")

for tex_file in tex_files:
    filename = os.path.basename(tex_file)
    basename = os.path.splitext(filename)[0]
    
    print(f"Processing {filename}...")
    
    try:
        # Check if uses tikz-uml (prefers latex -> dvi) or standalone/pdf (prefers pdflatex)
        with open(tex_file, 'r') as f:
            content = f.read()
            use_pdflatex = 'tikz-uml' not in content and 'DVI' not in content

        if use_pdflatex:
            # pdflatex -> pdf -> svg
            subprocess.run(['pdflatex', '-interaction=nonstopmode', f'-output-directory={TEX_DIR}', tex_file], 
                           check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            pdf_path = os.path.join(TEX_DIR, f"{basename}.pdf")
            if os.path.exists(pdf_path):
                svg_path = os.path.join(IMG_DIR, f"{basename}.svg")
                subprocess.run(['dvisvgm', '--pdf', '--no-fonts', pdf_path, '-o', svg_path], 
                               check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                print(f"  [OK] Produced {basename}.svg (via pdflatex)")
            else:
                print(f"  [FAIL] PDF not generated for {filename}")
        else:
            # latex -> dvi -> svg
            subprocess.run(['latex', '-interaction=nonstopmode', f'-output-directory={TEX_DIR}', tex_file], 
                           check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            dvi_path = os.path.join(TEX_DIR, f"{basename}.dvi")
            if os.path.exists(dvi_path):
                svg_path = os.path.join(IMG_DIR, f"{basename}.svg")
                subprocess.run(['dvisvgm', '--no-fonts', dvi_path, '-o', svg_path], 
                               check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                print(f"  [OK] Produced {basename}.svg (via latex)")
            else:
                print(f"  [FAIL] DVI not generated for {filename}")

    except Exception as e:
        print(f"  [ERROR] Processing {filename}: {e}")
    finally:
        # Cleanup intermediate files
        for ext in ['.aux', '.log', '.dvi', '.pdf', '.out', '.fdb_latexmk', '.fls']:
            temp_file = os.path.join(TEX_DIR, f"{basename}{ext}")
            if os.path.exists(temp_file):
                os.remove(temp_file)

print("\nBatch conversion completed.")
