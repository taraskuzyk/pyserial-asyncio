#!/usr/bin/env python
#
# This file is part of pySerial-asyncio - Cross platform serial port support for Python
# (C) 2016 pySerial-team
#
# SPDX-License-Identifier:    BSD-3-Clause
"""\
Test asyncio related functionality.

To run from the command line with a specific port with a loop-back,
device connected, use:

  $ cd pyserial-asyncio
  $ python -m test.test_asyncio SERIALDEVICE

"""

import os
from typing import List
import unittest
import asyncio

from serial_asyncio import open_transport_and_protocol, Data, SerialTransport

HOST = "127.0.0.1"
_PORT = 8888

# on which port should the tests be performed:
PORT = "socket://%s:%s" % (HOST, _PORT)


@unittest.skipIf(os.name != "posix", "asyncio not supported on platform")
class Test_asyncio(unittest.TestCase):
    """Test asyncio related functionality"""

    def setUp(self):
        self.loop = asyncio.get_event_loop()
        # create a closed serial port

    def tearDown(self):
        self.loop.close()

    def test_asyncio(self):
        TEXT = b"Hello, World!\n"
        received: List[Data] = []
        actions: List[str] = []
        done = asyncio.Event()

        class Input(asyncio.Protocol):

            def __init__(self):
                super().__init__()
                self._transport: None | SerialTransport = None

            def connection_made(self, transport: SerialTransport):  # type: ignore
                self._transport = transport

            def data_received(self, data: Data):
                assert self._transport
                self._transport.write(data)

        class Output(asyncio.Protocol):

            def __init__(self):
                super().__init__()
                self._transport = None

            def connection_made(self, transport: SerialTransport):
                assert self._transport
                self._transport = transport
                actions.append("open")
                transport.write(TEXT)

            def data_received(self, data: Data):
                received.append(data)
                if b"\n" in data:
                    self._transport.close()

            def connection_lost(self, exc: Exception | None):
                actions.append("close")
                done.set()

            def pause_writing(self):
                actions.append("pause")
                assert self._transport
                print(self._transport.get_write_buffer_size())

            def resume_writing(self):
                actions.append("resume")
                assert self._transport
                print(self._transport.get_write_buffer_size())

        if PORT.startswith("socket://"):
            coro = self.loop.create_server(Input, HOST, _PORT)
            self.loop.run_until_complete(coro)

        client = open_transport_and_protocol(self.loop, Output, PORT)
        self.loop.run_until_complete(client)
        self.loop.run_until_complete(done.wait())
        pending = asyncio.all_tasks(self.loop)
        self.loop.run_until_complete(asyncio.gather(*pending))
        self.assertEqual(b"".join(received), TEXT)
        self.assertEqual(actions, ["open", "close"])


if __name__ == "__main__":
    import sys

    sys.stdout.write(__doc__)
    if len(sys.argv) > 1:
        PORT = sys.argv[1]
    sys.stdout.write("Testing port: %r\n" % PORT)
    sys.argv[1:] = ["-v"]
    # When this module is executed from the command-line, it runs all its tests
    unittest.main()
