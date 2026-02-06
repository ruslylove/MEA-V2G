import mmap
import os
import time
import struct
from SUTAdapter import *
from FramingAPIDef import *

# Memory offsets for AM335x PRU Shared RAM
PRU_BASE_ADDR = 0x4A300000
PRU_SHARED_RAM_OFFSET = 0x10000  # 0x4A310000
PRU_SHARED_RAM_SIZE = 12 * 1024

class PruSpiAdapter(SUTAdapter):
    def __init__(self):
        self.started = False
        self.fd = None
        self.mem = None
        self.shared_ram = None
        
        # Shared memory protocol offsets
        self.OFF_STATUS = 0
        self.OFF_CMD = 1
        self.OFF_TX_SIZE = 2
        self.OFF_RX_SIZE = 4
        self.OFF_DATA = 6

    def start(self):
        print("Starting PruSpiAdapter...")
        try:
            # Open /dev/mem for mmap access to PRU RAM
            self.fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
            self.mem = mmap.mmap(self.fd, PRU_SHARED_RAM_SIZE, 
                                mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                                offset=PRU_BASE_ADDR + PRU_SHARED_RAM_OFFSET)
            
            # TODO: Ensure PRU firmware is loaded and running via remoteproc
            # This would typically be:
            # os.system("echo spi_whitebeet.out > /sys/class/remoteproc/remoteproc1/firmware")
            # os.system("echo start > /sys/class/remoteproc/remoteproc1/state")
            
            self.started = True
        except Exception as e:
            print(f"Failed to start PruSpiAdapter: {e}")
            if self.fd:
                os.close(self.fd)

    def stop(self):
        if self.mem:
            self.mem.close()
        if self.fd:
            os.close(self.fd)
        self.started = False

    def send(self, data):
        """
        Sends data via PRU-offloaded SPI.
        The PRU handles the handshake headers (0xAAAA and 0x5555).
        """
        if not self.started:
            return

        # 1. Wait for PRU to be READY
        while self.mem[self.OFF_STATUS] != 0:
            time.sleep(0.0001)

        # 2. Write TX data and size
        tx_size = len(data)
        self.mem[self.OFF_TX_SIZE : self.OFF_TX_SIZE + 2] = struct.pack(">H", tx_size)
        self.mem[self.OFF_DATA : self.OFF_DATA + tx_size] = data

        # 3. Trigger command
        self.mem[self.OFF_CMD] = 1

    def receive(self):
        """
        Polls the PRU for any received data.
        """
        if not self.started:
            return None

        # Check if PRU has finished a transfer and has data
        if self.mem[self.OFF_STATUS] == 0 and self.mem[self.OFF_CMD] == 0:
            rx_size = struct.unpack(">H", self.mem[self.OFF_RX_SIZE : self.OFF_RX_SIZE + 2])[0]
            if rx_size > 0:
                data = self.mem[self.OFF_DATA : self.OFF_DATA + rx_size]
                # Clear rx size so we don't read it again
                self.mem[self.OFF_RX_SIZE : self.OFF_RX_SIZE + 2] = b"\x00\x00"
                return data
        return None

    def holding_data(self):
        if not self.started: return False
        rx_size = struct.unpack(">H", self.mem[self.OFF_RX_SIZE : self.OFF_RX_SIZE + 2])[0]
        return rx_size > 0

    def clear_queues(self):
        if self.started:
            self.mem[self.OFF_RX_SIZE : self.OFF_RX_SIZE + 2] = b"\x00\x00"
