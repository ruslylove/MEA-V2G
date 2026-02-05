import unittest
import struct
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from EthernetAdapterRaw import EthernetAdapterRaw
from FramingAPIDef import *

class TestEthernetAdapterRaw(unittest.TestCase):
    def setUp(self):
        self.adapter = EthernetAdapterRaw()
        self.adapter.sut_interface = "eth0"
        self.adapter.dut_mac = "01:02:03:04:05:06"
        self.adapter.src_mac = "00:0c:29:ab:cd:ef"
        # Mock the socket
        self.adapter.socket = MagicMock()

    @patch('EthernetAdapterRaw.system_type', return_value='Linux')
    def test_send_frame(self, mock_system):
        data = b"\x01\x02\x03"
        self.adapter.send(data)
        
        # Verify socket.send was called
        self.assertTrue(self.adapter.socket.send.called)
        sent_packet = self.adapter.socket.send.call_args[0][0]
        
        # Verify Ethernet header
        dst_mac, src_mac, eth_type = struct.unpack("!6s6sH", sent_packet[:14])
        self.assertEqual(dst_mac, bytes.fromhex("010203040506"))
        self.assertEqual(eth_type, 0x6003)
        
        # Verify payload wrapper
        marker1, marker2, plen = struct.unpack("!BBH", sent_packet[14:18])
        self.assertEqual(marker1, 0x00)
        self.assertEqual(marker2, 0x04)
        self.assertEqual(plen, 3)
        self.assertEqual(sent_packet[18:], data)

    def test_pkt_callback_valid(self):
        # Construct a valid packet
        dst_mac = bytes.fromhex("000c29abcdef")
        src_mac = bytes.fromhex("010203040506")
        eth_type = 0x6003
        
        # Load starts at index 14 of payload, so index 28 of full frame?
        # No, index 14 is start of payload.
        # Payload starts with a 4-byte wrapper (00 04 LEN_HI LEN_LO)
        # Then the "load" starts.
        
        # Let's see: load = payload[4:]
        # So packet[14:18] is the skip
        
        load = b"\xc0" + b"\x00\x00\x00" + b"\x00\x01" + b"\x01" + b"\x00" + b"\xc1"
        # marker(1), pheader(5), pbytedata(1), crc(1), marker(1)
        # pheader[3:5] is len. 00 01
        
        payload_wrapper = b"\x00\x04" + len(load).to_bytes(2, "big")
        
        full_packet = struct.pack("!6s6sH", dst_mac, src_mac, eth_type) + payload_wrapper + load
        
        # We need to mock pack_and_parse_frame or just let it run if SUTAdapter is matched
        # Since we are in unit test, let's mock it to see if it's called with correct data
        self.adapter.pack_and_parse_frame = MagicMock(return_value="MOCKED_FRAME")
        
        self.adapter.pkt_callback(full_packet)
        
        self.assertTrue(self.adapter.pack_and_parse_frame.called)
        self.assertFalse(self.adapter.queue_rx.empty())
        self.assertEqual(self.adapter.queue_rx.get(), "MOCKED_FRAME")

if __name__ == '__main__':
    unittest.main()
