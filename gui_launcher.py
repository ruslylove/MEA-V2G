import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import subprocess
import threading
import queue
import sys
import os

class GuiLauncher(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("V2G Application Launcher")
        self.geometry("800x600")

        self.process = None
        self.process_thread = None

        # --- Main Frames ---
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        config_frame = ttk.LabelFrame(main_frame, text="Configuration", padding="10")
        config_frame.pack(fill=tk.X, pady=5)
        config_frame.columnconfigure(1, weight=1)

        control_frame = ttk.Frame(main_frame, padding="5 0")
        control_frame.pack(fill=tk.X)

        log_frame = ttk.LabelFrame(main_frame, text="Application Output", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # --- Configuration Widgets ---
        self.role_var = tk.StringVar(value="EVSE")
        self.if_type_var = tk.StringVar(value="eth")
        self.if_name_var = tk.StringVar(value="enp3s0")
        self.mac_var = tk.StringVar(value="c4:93:00:48:ac:f0")
        self.ev_config_var = tk.StringVar(value="./ev.json")
        self.evse_config_var = tk.StringVar(value="./evse.json")
        self.portmirror_var = tk.BooleanVar()
        self.sudo_password_var = tk.StringVar()
        self.auto_auth_var = tk.BooleanVar()
        self.api_port_var = tk.StringVar()

        # Role
        ttk.Label(config_frame, text="Role:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Radiobutton(config_frame, text="EVSE", variable=self.role_var, value="EVSE", command=self._on_role_change).grid(row=0, column=1, sticky=tk.W)
        ttk.Radiobutton(config_frame, text="EV", variable=self.role_var, value="EV", command=self._on_role_change).grid(row=0, column=2, sticky=tk.W)

        # Interface Type
        ttk.Label(config_frame, text="Interface Type:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Radiobutton(config_frame, text="Ethernet (eth)", variable=self.if_type_var, value="eth").grid(row=1, column=1, sticky=tk.W)
        ttk.Radiobutton(config_frame, text="SPI (spi)", variable=self.if_type_var, value="spi").grid(row=1, column=2, sticky=tk.W)

        # Interface Name
        ttk.Label(config_frame, text="Interface Name:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(config_frame, textvariable=self.if_name_var).grid(row=2, column=1, columnspan=3, sticky=tk.EW, padx=5)

        # MAC Address
        ttk.Label(config_frame, text="MAC Address:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(config_frame, textvariable=self.mac_var).grid(row=3, column=1, columnspan=3, sticky=tk.EW, padx=5)

        # EV Config
        ttk.Label(config_frame, text="EV Config:").grid(row=4, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(config_frame, textvariable=self.ev_config_var).grid(row=4, column=1, columnspan=2, sticky=tk.EW, padx=5)
        ttk.Button(config_frame, text="Browse...", command=lambda: self.browse_file(self.ev_config_var)).grid(row=4, column=3)

        # EVSE Config
        ttk.Label(config_frame, text="EVSE Config:").grid(row=5, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(config_frame, textvariable=self.evse_config_var).grid(row=5, column=1, columnspan=2, sticky=tk.EW, padx=5)
        ttk.Button(config_frame, text="Browse...", command=lambda: self.browse_file(self.evse_config_var)).grid(row=5, column=3)

        # Checkboxes
        ttk.Checkbutton(config_frame, text="Enable Port Mirror", variable=self.portmirror_var).grid(row=6, column=1, columnspan=2, sticky=tk.W, pady=5)
        ttk.Checkbutton(config_frame, text="Auto-authorize EV (EVSE mode)", variable=self.auto_auth_var).grid(row=6, column=2, columnspan=2, sticky=tk.W, pady=5)

        # Sudo Password (for Linux/macOS)
        ttk.Label(config_frame, text="Sudo Password:").grid(row=7, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(config_frame, textvariable=self.sudo_password_var, show="*").grid(row=7, column=1, columnspan=3, sticky=tk.EW, padx=5)

        # API Port
        ttk.Label(config_frame, text="API Port:").grid(row=8, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(config_frame, textvariable=self.api_port_var).grid(row=8, column=1, columnspan=3, sticky=tk.EW, padx=5)

        # --- Control Widgets ---
        self.start_button = ttk.Button(control_frame, text="Start", command=self.start_application)
        self.start_button.pack(side=tk.LEFT, padx=(0, 5))

        self.stop_button = ttk.Button(control_frame, text="Stop", command=self.stop_application, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT)

        # --- Log Widgets ---
        self.log_area = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state=tk.DISABLED)
        self.log_area.pack(fill=tk.BOTH, expand=True)

        # Queue for subprocess output
        self.output_queue = queue.Queue()

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def browse_file(self, path_var):
        filename = filedialog.askopenfilename(
            initialdir=".",
            title="Select a File",
            filetypes=(("JSON files", "*.json"), ("all files", "*.*"))
        )
        if filename:
            path_var.set(filename)

    def _on_role_change(self):
        role = self.role_var.get()
        if role == "EVSE":
            self.if_name_var.set("enp3s0")
            self.mac_var.set("c4:93:00:48:ac:f0")
        elif role == "EV":
            self.if_name_var.set("enx00e09909a99b")
            self.mac_var.set("c4:93:00:47:cd:e7")

    def start_application(self):
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.log_area.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.log_area.config(state=tk.DISABLED)

        # Determine the path to the venv python executable
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if os.name == 'nt': # Windows
            python_executable = os.path.join(script_dir, '.venv', 'Scripts', 'python.exe')
            command_prefix = []
        else: # Linux/macOS
            python_executable = os.path.join(script_dir, '.venv', 'bin', 'python3')
            if not os.path.exists(python_executable):
                python_executable = os.path.join(script_dir, '.venv', 'bin', 'python')
            command_prefix = ['sudo', '-S']

        # Build command from GUI fields
        command = command_prefix + [
            python_executable,
            '-u',
            'Application.py',
            self.if_type_var.get(),
            '-i', self.if_name_var.get(),
            '-r', self.role_var.get()
        ]

        if self.mac_var.get():
            command.extend(['-m', self.mac_var.get()])
        if self.role_var.get() == 'EV' and self.ev_config_var.get():
            command.extend(['-c', self.ev_config_var.get()])
        if self.role_var.get() == 'EVSE' and self.evse_config_var.get():
            command.extend(['-ec', self.evse_config_var.get()])
        if self.portmirror_var.get():
            command.append('-p')
        if self.auto_auth_var.get():
            command.append('--auto')
        if self.api_port_var.get().strip():
            command.extend(['--api-port', self.api_port_var.get().strip()])

        self.log_message(f"Starting command: {' '.join(command)}\n")

        # Run the subprocess in a separate thread
        self.process_thread = threading.Thread(target=self.run_process, args=(command,), daemon=True)
        self.process_thread.start()

        # Start polling the output queue
        self.after(100, self.process_output_queue)

    def run_process(self, command):
        try:
            password = self.sudo_password_var.get()
            # Add a newline to the password, as sudo -S expects it
            password_input = (password + '\n').encode('utf-8') if 'sudo' in command[0] else None

            # Using Popen to get real-time output
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            # Write password to stdin if needed
            self.process.stdin.write(
                (password + '\n') if password_input else ''
            )
            # Read output line by line and put it in the queue
            for line in iter(self.process.stdout.readline, ''):
                self.output_queue.put(line)
            self.process.stdout.close()
            self.process.wait()
        except FileNotFoundError:
            self.output_queue.put("Error: 'Application.py' not found. Make sure this script is in the same directory.\n")
        except Exception as e:
            self.output_queue.put(f"An error occurred: {e}\n")
        finally:
            self.output_queue.put(None) # Signal that the process has ended

    def process_output_queue(self):
        try:
            while True:
                line = self.output_queue.get_nowait()
                if line is None: # End of process signal
                    self.on_process_end()
                    return
                self.log_message(line)
        except queue.Empty:
            pass # No new output
        
        if self.process and self.process.poll() is None:
             self.after(100, self.process_output_queue)
        else:
             self.on_process_end()

    def log_message(self, message):
        self.log_area.config(state=tk.NORMAL)
        self.log_area.insert(tk.END, message)
        self.log_area.see(tk.END)
        self.log_area.config(state=tk.DISABLED)

    def stop_application(self):
        if self.process:
            self.log_message("\n--- Sending termination signal to process ---\n")
            self.process.terminate() # or .kill() for a more forceful stop
            self.on_process_end()

    def on_process_end(self):
        if self.process and self.process.poll() is not None:
            self.log_message("\n--- Application finished ---\n")
            self.process = None
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)

    def on_closing(self):
        if self.process:
            if messagebox.askokcancel("Quit", "The application is still running. Do you want to stop it and quit?"):
                self.stop_application()
                self.destroy()
        else:
            self.destroy()

if __name__ == "__main__":
    app = GuiLauncher()
    app.mainloop()