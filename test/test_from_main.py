import asyncio
import threading
from typing import Optional

import serial

from serial_asyncio import Data, SerialTransport, open_transport_and_protocol

if __name__ == "__main__":

    class Output(asyncio.Protocol):

        def __init__(self):
            super().__init__()
            self._transport: SerialTransport | None = None

        def connection_made(self, transport: SerialTransport):  # type: ignore
            self._transport = transport
            print("port opened", self._transport)
            self._transport.serial.rts = False
            self._transport.write(b"Hello, World!\n")

        def data_received(self, data: Data):
            assert self._transport, "Data received before transport was set"
            print("data received", repr(data))

        def connection_lost(self, exc: Optional[Exception]):
            assert self._transport, "Data received before transport was set"
            self._transport.loop.stop()

        def pause_writing(self):
            assert self._transport
            print(self._transport.get_write_buffer_size())

        def resume_writing(self):
            assert self._transport
            print(self._transport.get_write_buffer_size())
            print("resume writing")

    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    asyncio.set_event_loop(loop)

    transport, protocol = open_transport_and_protocol(
        serial_instance=serial.Serial(baudrate=921600, port="/dev/ttyUSB0"),
        loop=loop,
        protocol=Output(),
    )
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    import time

    time.sleep(3)
    transport, protocol = open_transport_and_protocol(
        serial_instance=serial.Serial(baudrate=921600, port="/dev/ttyUSB0"),
        loop=loop,
        protocol=Output(),
    )
    loop.stop()
