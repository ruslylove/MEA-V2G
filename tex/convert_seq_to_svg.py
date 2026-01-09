import os
import glob
import subprocess
import shutil

# Configuration
TEX_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(os.path.dirname(TEX_DIR), 'img')

# Ensure img directory exists
os.makedirs(IMG_DIR, exist_ok=True)

# Find all seq_*.tex files
tex_files = glob.glob(os.path.join(TEX_DIR, 'seq_*.tex'))

print(f"Found {len(tex_files)} sequence diagram files to process.")

for tex_file in tex_files:
    filename = os.path.basename(tex_file)
    basename = os.path.splitext(filename)[0]
    
    print(f"Processing {filename}...")
    
    # 1. Compile to DVI using latex
    # Output to IMG_DIR directly to keep build artifacts out of tex/
    try:
        subprocess.run(
            ['latex', '-interaction=nonstopmode', f'-output-directory={IMG_DIR}', tex_file],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as e:
        print(f"Error compiling {filename} to DVI: {e}")
        # LateX often returns error even on warnings, check if DVI was created
        dvi_check_path = os.path.join(IMG_DIR, f"{basename}.dvi")
        if not os.path.exists(dvi_check_path):
             continue

    # 2. Convert DVI to SVG using dvisvgm
    dvi_path = os.path.join(IMG_DIR, f"{basename}.dvi")
    svg_path = os.path.join(IMG_DIR, f"{basename}.svg")
    
    if os.path.exists(dvi_path):
        try:
            # --no-fonts creates paths instead of using fonts (better for web embedding compatibility)
            # DVI mode is default, so no --pdf needed
            subprocess.run(
                ['dvisvgm', '--no-fonts', dvi_path, '-o', svg_path],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            print(f"Converted to {basename}.svg")
            
            # Cleanup intermediate files
            for ext in ['.dvi', '.aux', '.log']:
                temp_file = os.path.join(IMG_DIR, f"{basename}{ext}")
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                    
        except subprocess.CalledProcessError as e:
            print(f"Error converting {basename}.dvi to SVG: {e}")
    else:
        print(f"DVI file not found for {basename}")

print("Batch conversion completed.")
