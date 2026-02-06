import os
import time

def get_pru_path():
    """Returns the sysfs path for the PRU remoteproc device."""
    # On most modern BeagleBone kernels, PRU0 is remoteproc1 and PRU1 is remoteproc2
    # We use PRU0 for the Whitebeet SPI offloading.
    return "/sys/class/remoteproc/remoteproc1"

def ensure_pru_running(fw_name="spi_whitebeet.out"):
    """Ensures the PRU firmware is loaded and the PRU is running."""
    rproc_path = get_pru_path()

    if not os.path.exists(rproc_path):
        print(f"Warning: PRU remoteproc path {rproc_path} not found. PRU may be disabled in device tree.")
        return False

    try:
        # 1. Set firmware name
        with open(f"{rproc_path}/firmware", "w") as f:
            f.write(fw_name)

        # 2. Check state and start if necessary
        with open(f"{rproc_path}/state", "r") as f:
            state = f.read().strip()

        if state != "running":
            print(f"PRU state is {state}. Attempting to start...")
            if state != "offline":
                try:
                    with open(f"{rproc_path}/state", "w") as f:
                        f.write("stop")
                    time.sleep(0.1)
                except:
                    pass
            
            # Start PRU
            with open(f"{rproc_path}/state", "w") as f:
                f.write("start")
            
            # Verify start
            time.sleep(0.1)
            with open(f"{rproc_path}/state", "r") as f:
                if f.read().strip() != "running":
                    print("Warning: Failed to start PRU - check dmesg for details.")
                    return False
                else:
                    print("PRU started successfully.")
        else:
            print("PRU is already running.")
        return True
    except PermissionError:
        print("Error: Permission denied managing PRU. Please run with sudo.")
        return False
    except Exception as e:
        print(f"Warning: Error management PRU state: {e}")
        return False

def ensure_pru_stopped():
    """Safely stops the PRU to release hardware resources."""
    rproc_path = get_pru_path()

    if not os.path.exists(rproc_path):
        return True

    try:
        with open(f"{rproc_path}/state", "r") as f:
            state = f.read().strip()

        if state == "running":
            print("PRU is running. Stopping to prevent resource conflict...")
            with open(f"{rproc_path}/state", "w") as f:
                f.write("stop")
            time.sleep(0.2)
            print("PRU stopped.")
        return True
    except PermissionError:
        print("Error: Permission denied stopping PRU. Please run with sudo.")
        return False
    except Exception as e:
        print(f"Warning: Could not stop PRU: {e}")
        return False
