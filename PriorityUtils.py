import sys
import os

def set_high_priority():
    """
    Sets the process priority to the highest possible on Linux.
    This helps ensure the charging loop is not interrupted by other tasks.
    """
    try:
        if sys.platform == "linux":
            # Set niceness to -20 (highest priority for regular scheduler)
            try:
                os.nice(-20)
            except Exception as e:
                print(f"Note: Could not set niceness: {e}")
            
            # Use 'chrt' to set real-time priority (SCHED_FIFO, priority 99)
            # This requires root, which we normally have via sudo.
            pid = os.getpid()
            if os.system(f"chrt -f -p 99 {pid} 2>/dev/null") == 0:
                print("Process priority set to REAL-TIME (SCHED_FIFO 99)")
            else:
                print("Note: Could not set SCHED_FIFO priority (is chrt available and are we root?)")
    except Exception as e:
        print(f"Note: Could not set high priority: {e}")
