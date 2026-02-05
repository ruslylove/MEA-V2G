import socket
import struct
import multiprocessing
import time
import sys
from platform import system as system_type

from SUTAdapter import *
from FramingAPIDef import *

# Helper to get MAC address without Scapy on Linux
def get_mac_by_ip_linux(ip_address, interface):
    try:
        # 1. Try to find it in the ARP cache (/proc/net/arp)
        with open("/proc/net/arp", "r") as f:
            lines = f.readlines()[1:] # Skip header
            for line in lines:
                parts = line.split()
                if len(parts) >= 4 and parts[0] == ip_address:
                    return parts[3]
        
        # 2. If not found, try to trigger an ARP request by pinging briefly or using arping if available
        # However, for performance we might just rely on the system already having it if it just connected.
        # Alternatively, we could use a raw socket to send an ARP request, but reading /proc/net/arp is faster if it's there.
        # In this environment, we'll assume the user has already communicated with the device.
    except Exception as e:
        print(f"Error reading ARP cache: {e}")
    return None

def get_if_hwaddr_linux(interface):
    try:
        with open(f"/sys/class/net/{interface}/address", "r") as f:
            return f.read().strip()
    except Exception as e:
        print(f"Error reading interface MAC: {e}")
    return "00:00:00:00:00:00"

class EthernetAdapterRaw(SUTAdapter):
    def __init__(self):
        self.recv_process = None
        self.queue_rx = multiprocessing.Manager().Queue()
        self.queue_tx = multiprocessing.Manager().Queue()

        self.sut_ip = ""
        self.sut_interface = ""
        self.dut_mac = None
        self.src_mac = None
        self.socket = None

    def send(self, data):
        if len(data) > 1450:
            print("Alert: Sending large frame")

        if system_type() != "Linux":
            print("Raw sockets only supported on Linux")
            return

        # Construction of the Ethernet frame
        # Dest MAC (6), Src MAC (6), Type (2)
        dst_mac_bytes = bytes.fromhex(self.dut_mac.replace(":", ""))
        src_mac_bytes = bytes.fromhex(self.src_mac.replace(":", ""))
        eth_header = struct.pack("!6s6sH", dst_mac_bytes, src_mac_bytes, 0x6003)
        
        # Payload wrapper b"\x00\x04" + len(data).to_bytes(2, "big") + data
        payload_wrapper = struct.pack("!BBH", 0x00, 0x04, len(data))
        
        full_frame = eth_header + payload_wrapper + data
        try:
            self.socket.send(full_frame)
        except Exception as e:
            print(f"Error sending raw frame: {e}")

    def receive(self):
        if not self.queue_rx.empty():
            frame = self.queue_rx.get_nowait()
            return frame
        else:
            return None

    def pkt_callback(self, packet):
        # packet is raw bytes
        # Ethernet header is 14 bytes: DST(6), SRC(6), TYPE(2)
        if len(packet) < 14:
            return

        # Check type (bytes 12-14)
        eth_type = struct.unpack("!H", packet[12:14])[0]
        if eth_type != 0x6003:
            return

        # Src MAC (bytes 6-12)
        src_mac_bytes = packet[6:12]
        src_mac = ":".join(f"{b:02x}" for b in src_mac_bytes)
        
        if src_mac.lower() != self.dut_mac.lower():
            return

        # Payload starts at index 14
        payload = packet[14:]
        
        # Original logic: payload = Ether(packet)[Ether].load[4:]
        # Which skips 4 bytes of the "load"
        if len(payload) < 4:
            return
            
        load = payload[4:]
        
        # The rest of the logic from EthernetAdapter.py
        marker = load[0]
        if not marker or marker != START_OF_FRAME:
            return

        pheader = load[1:6]
        pbytes = int.from_bytes(pheader[3:5], 'big')

        if pbytes > 0:
            pbytedata = load[6:6+pbytes]
        else:
            pbytedata = b""

        if not pbytedata and pbytes > 0:
            print("Had to cancel data reception mid frame")
            return

        pdata = (pheader + pbytedata) if pbytes > 0 else pheader
        pbytes_total = pbytes + 5

        crc = int.from_bytes(load[pbytes_total+1:pbytes_total+2], "big")
        marker = int.from_bytes(load[pbytes_total+2:pbytes_total+3], "big")

        if marker == END_OF_FRAME:
            frame = self.pack_and_parse_frame(
                b"\xc0" + pdata + crc.to_bytes(1, "big") + b"\xc1")

            self.queue_rx.put_nowait(frame)
        else:
            print("Could not catch end of frame")

    def process_receive(self):
        while True:
            try:
                packet = self.socket.recv(2048)
                if packet:
                    self.pkt_callback(packet)
            except Exception as e:
                # Handle timeout or interrupt
                time.sleep(0.001)

    def start(self):
        if system_type() != "Linux":
             raise AssertionError("Raw socket adapter only supported on Linux")

        # Determine target MAC
        if self.dut_mac == None:
            end_time = time.time() + 10
            while self.dut_mac == None and time.time() < end_time:
                self.dut_mac = get_mac_by_ip_linux(self.sut_ip, self.sut_interface)
                if self.dut_mac == None:
                    # Try to trigger ARP by opening a socket briefly or just wait
                    time.sleep(0.5)

        if self.dut_mac == None:
            raise AssertionError("[-] Could not determine target MAC address from IP")

        self.src_mac = get_if_hwaddr_linux(self.sut_interface)
        
        # Create raw socket
        # 0x6003 in network byte order is htons(0x6003)
        self.socket = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x6003))
        self.socket.bind((self.sut_interface, socket.htons(0x6003)))
        self.socket.settimeout(0.1)

        self.recv_process = multiprocessing.Process(target=self.process_receive)
        self.recv_process.start()
        
        time.sleep(1)

    def stop(self):
        if self.recv_process:
            self.recv_process.terminate()
        if self.socket:
            self.socket.close()

    def holding_data(self):
        return not self.queue_rx.empty()

    def clear_queues(self):
        while not self.queue_rx.empty():
            self.queue_rx.get_nowait()
