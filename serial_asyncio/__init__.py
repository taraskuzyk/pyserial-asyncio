#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Implementation of asyncio support.
#
# This file is part of pySerial. https://github.com/pyserial/pyserial-asyncio
# (C) 2015-2020 pySerial-team
#
# SPDX-License-Identifier:    BSD-3-Clause
"""\
Support asyncio with serial ports.

Posix platforms only, Python 3.5+ only.

Windows event loops can not wait for serial ports with the current
implementation. It should be possible to get that working though.
"""
import asyncio
import logging
import os
from typing import Optional, Tuple, Union
import serial

__version__ = "0.6"

os.name = "nt"

Data = Union[bytes, bytearray, memoryview]


class SerialTransport(asyncio.Transport):
    """An asyncio transport model of a serial communication channel.

    A transport class is an abstraction of a communication channel.
    This allows protocol implementations to be developed against the
    transport abstraction without needing to know the details of the
    underlying channel, such as whether it is a pipe, a socket, or
    indeed a serial port.


    You generally won’t instantiate a transport yourself; instead, you
    will call `create_serial_connection` which will create the
    transport and try to initiate the underlying communication channel,
    calling you back when it succeeds.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        protocol: asyncio.Protocol,
        serial_instance: serial.Serial,
    ):
        super().__init__()
        self.serial = serial_instance
        self.loop = loop
        self._protocol = protocol
        self._closing = False
        self._protocol_paused = False
        self._max_read_size = 1024
        self._write_buffer: bytearray = bytearray()
        self._set_write_buffer_limits()
        self._has_reader = False
        self._has_writer = False
        self._poll_wait_time = 0.0005
        self._max_out_waiting = 1024

        # XXX how to support url handlers too

        # Asynchronous I/O requires non-blocking devices
        self.serial.timeout = 0
        self.serial.write_timeout = 0

        # These two callbacks will be enqueued in a FIFO queue by asyncio
        loop.call_soon(protocol.connection_made, self)
        loop.call_soon(self._ensure_reader)

    def close(self):
        """Close the transport gracefully.

        Any buffered data will be written asynchronously. No more data
        will be received and further writes will be silently ignored.
        After all buffered data is flushed, the protocol's
        connection_lost() method will be called with None as its
        argument.
        """
        if not self._closing:
            self._close(None)

    def _read_ready(self):
        try:
            data = self.serial.read(self._max_read_size)
        except serial.SerialException as e:
            self._close(exc=e)
        else:
            if data:
                self._protocol.data_received(data)

    def write(self, data: Data):
        """Write some data to the transport.

        This method does not block; it buffers the data and arranges
        for it to be sent out asynchronously.  Writes made after the
        transport has been closed will be ignored."""
        if self._closing:
            logging.warning("Attempted writing after closing transport, ignoring...")
            return

        if self.get_write_buffer_size() == 0:
            self._write_buffer.extend(data)
            self._ensure_writer()
        else:
            self._write_buffer.extend(data)

        self._maybe_pause_protocol()

    def can_write_eof(self):
        """Serial ports do not support the concept of end-of-file.

        Always returns False.
        """
        return False

    def pause_reading(self):
        """Pause the receiving end of the transport.

        No data will be passed to the protocol’s data_received() method
        until resume_reading() is called.
        """
        self._remove_reader()

    def resume_reading(self):
        """Resume the receiving end of the transport.

        Incoming data will be passed to the protocol's data_received()
        method until pause_reading() is called.
        """
        self._ensure_reader()

    def set_write_buffer_limits(
        self, high: Optional[int] = None, low: Optional[int] = None
    ):
        """Set the high- and low-water limits for write flow control.

        These two values control when the protocol’s
        pause_writing()and resume_writing() methods are called. If
        specified, the low-water limit must be less than or equal to
        the high-water limit. Neither high nor low can be negative.
        """
        self._set_write_buffer_limits(high=high, low=low)
        self._maybe_pause_protocol()

    def get_write_buffer_size(self) -> int:
        """The number of bytes in the write buffer.

        This buffer is unbounded, so the result may be larger than the
        the high water mark.
        """
        return len(self._write_buffer)

    def write_eof(self) -> None:
        raise NotImplementedError("Serial connections do not support end-of-file")

    def abort(self):
        """Close the transport immediately.

        Pending operations will not be given opportunity to complete,
        and buffered data will be lost. No more data will be received
        and further writes will be ignored.  The protocol's
        connection_lost() method will eventually be called.
        """
        self._abort()

    def flush(self):
        """clears output buffer and stops any more data being written"""
        self._remove_writer()
        self._write_buffer.clear()
        self._maybe_resume_protocol()

    def _maybe_pause_protocol(self):
        """To be called whenever the write-buffer size increases.

        Tests the current write-buffer size against the high water
        mark configured for this transport. If the high water mark is
        exceeded, the protocol is instructed to pause_writing().
        """
        if self.get_write_buffer_size() <= self._high_water:
            return
        if not self._protocol_paused:
            self._protocol_paused = True
            try:
                self._protocol.pause_writing()
            except Exception as exc:
                self.loop.call_exception_handler(
                    {
                        "message": "protocol.pause_writing() failed",
                        "exception": exc,
                        "transport": self,
                        "protocol": self._protocol,
                    }
                )

    def _maybe_resume_protocol(self):
        """To be called whenever the write-buffer size decreases.

        Tests the current write-buffer size against the low water
        mark configured for this transport. If the write-buffer
        size is below the low water mark, the protocol is
        instructed that is can resume_writing().
        """
        if self._protocol_paused and self.get_write_buffer_size() <= self._low_water:
            self._protocol_paused = False
            try:
                self._protocol.resume_writing()
            except Exception as exc:
                self.loop.call_exception_handler(
                    {
                        "message": "protocol.resume_writing() failed",
                        "exception": exc,
                        "transport": self,
                        "protocol": self._protocol,
                    }
                )

    def _write_ready(self):
        """Asynchronously write buffered data.

        This method is called back asynchronously as a writer
        registered with the asyncio event-loop against the
        underlying file descriptor for the serial port.

        Should the write-buffer become empty if this method
        is invoked while the transport is closing, the protocol's
        connection_lost() method will be called with None as its
        argument.
        """
        data = self._write_buffer
        assert data, "Write buffer should not be empty"

        self._write_buffer.clear()

        try:
            number_of_bytes_written = self.serial.write(data)
            assert (
                number_of_bytes_written is not None
            ), "Number of bytes written should not be None"
        except (BlockingIOError, InterruptedError):
            self._write_buffer.extend(data)
        except serial.SerialException as exc:
            self._fatal_error(exc, "Fatal write error on serial transport")
        else:
            if number_of_bytes_written == len(data):
                assert self._flushed()
                self._remove_writer()
                self._maybe_resume_protocol()  # May cause further writes
                # _write_ready may have been invoked by the event loop
                # after the transport was closed, as part of the ongoing
                # process of flushing buffered data. If the buffer
                # is now empty, we can close the connection
                if self._closing and self._flushed():
                    self._close()
                return
            assert 0 <= number_of_bytes_written < len(data)
            data = data[number_of_bytes_written:]
            self._write_buffer.extend(data)  # Try again later
            self._maybe_resume_protocol()
            assert self._has_writer

    if os.name == "nt":

        def _poll_read(self):
            if self._has_reader and not self._closing:
                try:
                    self._has_reader = self.loop.call_later(
                        self._poll_wait_time, self._poll_read
                    )
                    if self.serial.in_waiting:
                        self._read_ready()
                except serial.SerialException as exc:
                    self._fatal_error(exc, "Fatal write error on serial transport")

        def _ensure_reader(self):
            if not self._has_reader and not self._closing:
                self._has_reader = self.loop.call_later(
                    self._poll_wait_time, self._poll_read
                )

        def _remove_reader(self):
            if self._has_reader:
                self._has_reader.cancel()
            self._has_reader = False

        def _poll_write(self):
            if self._has_writer and not self._closing:
                self._has_writer = self.loop.call_later(
                    self._poll_wait_time, self._poll_write
                )
                if self.serial.out_waiting < self._max_out_waiting:
                    self._write_ready()

        def _ensure_writer(self):
            if not self._has_writer and not self._closing:
                self._has_writer = self.loop.call_soon(self._poll_write)

        def _remove_writer(self):
            if self._has_writer:
                self._has_writer.cancel()
            self._has_writer = False

    else:

        def _ensure_reader(self):
            if (not self._has_reader) and (not self._closing):
                self.loop.add_reader(self.serial.fileno(), self._read_ready)
                self._has_reader = True

        def _remove_reader(self):
            if self._has_reader:
                self.loop.remove_reader(self.serial.fileno())
                self._has_reader = False

        def _ensure_writer(self):
            if (not self._has_writer) and (not self._closing):
                self.loop.add_writer(self.serial.fileno(), self._write_ready)
                self._has_writer = True

        def _remove_writer(self):
            if self._has_writer:
                self.loop.remove_writer(self.serial.fileno())
                self._has_writer = False

    def _set_write_buffer_limits(
        self, high: Optional[int] = None, low: Optional[int] = None
    ):
        """Ensure consistent write-buffer limits."""
        if high is None:
            high = 64 * 1024 if low is None else 4 * low
        if low is None:
            low = high // 4
        if not high >= low >= 0:
            raise ValueError("high (%r) must be >= low (%r) must be >= 0" % (high, low))
        self._high_water = high
        self._low_water = low

    def _fatal_error(
        self, exc: Exception, message: str = "Fatal error on serial transport"
    ):
        """Report a fatal error to the event-loop and abort the transport."""
        self.loop.call_exception_handler(
            {
                "message": message,
                "exception": exc,
                "transport": self,
                "protocol": self._protocol,
            }
        )
        self._abort(exc)

    def _flushed(self):
        """True if the write buffer is empty, otherwise False."""
        return self.get_write_buffer_size() == 0

    def _close(self, exc: Optional[Exception] = None):
        """Close the transport gracefully.

        If the write buffer is already empty, writing will be
        stopped immediately and a call to the protocol's
        connection_lost() method scheduled.

        If the write buffer is not already empty, the
        asynchronous writing will continue, and the _write_ready
        method will call this _close method again when the
        buffer has been flushed completely.
        """
        self._closing = True
        self._remove_reader()
        if self._flushed():
            self._remove_writer()
            self.loop.create_task(self._call_connection_lost(exc))

    def _abort(self, exc: Optional[Exception] = None):
        """Close the transport immediately.

        Pending operations will not be given opportunity to complete,
        and buffered data will be lost. No more data will be received
        and further writes will be ignored.  The protocol's
        connection_lost() method will eventually be called with the
        passed exception.
        """
        self._closing = True
        self._remove_reader()
        self._remove_writer()  # Pending buffered data will not be written
        self.loop.create_task(self._call_connection_lost(exc))

    async def _call_connection_lost(self, exc: Optional[Exception] = None):
        """Close the connection.

        Informs the protocol through connection_lost() and clears
        pending buffers and closes the serial connection.
        """
        assert self._closing
        assert not self._has_writer
        assert not self._has_reader
        await self.loop.run_in_executor(None, self.serial.flush)

        try:
            self._protocol.connection_lost(exc)
        finally:
            self._write_buffer.clear()
            await self.loop.run_in_executor(None, self.serial.close)


def open_transport_and_protocol(
    loop: asyncio.AbstractEventLoop,
    protocol: asyncio.Protocol,
    serial_instance: serial.Serial,
) -> Tuple[SerialTransport, asyncio.Protocol]:
    """Create a connection to the given serial port instance.

    This function is a coroutine which will try to establish the
    connection.

    1. The protocol instance is tied to the transport

    2. This coroutine returns successfully with a (transport,
       protocol) pair.

    3. The connection_made() method of the protocol
       will be called at some point by the event loop.
    """
    transport = SerialTransport(loop, protocol, serial_instance)
    return transport, protocol


async def open_stream_reader_and_writer(
    serial: serial.Serial,
    loop: Optional[asyncio.AbstractEventLoop] = None,
    limit: Optional[int] = None,
) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """A wrapper for create_serial_connection() returning a (reader,
    writer) pair.

    The reader returned is a StreamReader instance; the writer is a
    StreamWriter instance.

    The arguments are all the usual arguments to Serial(). Additional
    optional keyword arguments are loop (to set the event loop instance
    to use) and limit (to set the buffer limit passed to the
    StreamReader.

    This function is a coroutine.
    """
    if loop is None:
        loop = asyncio.get_event_loop()

    if limit is None:
        reader = asyncio.StreamReader(loop=loop)
    else:
        reader = asyncio.StreamReader(limit=limit, loop=loop)

    protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
    transport, _ = open_transport_and_protocol(
        loop=loop, protocol=protocol, serial_instance=serial
    )
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    return reader, writer


__all__ = ["open_stream_reader_and_writer"]

if __name__ == "__main__":

    class Output(asyncio.Protocol):

        def __init__(self):
            super().__init__()
            self._transport: Union[SerialTransport, None] = None

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
    transport, protocol = open_transport_and_protocol(
        serial_instance=serial.Serial(baudrate=921600, port="/dev/ttyUSB0"),
        loop=loop,
        protocol=Output(),
    )
    loop.run_forever()
    loop.close()
