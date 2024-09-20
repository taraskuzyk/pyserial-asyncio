Original Project Homepage: https://github.com/pyserial/pyserial-asyncio

## Main differences in this fork

- Explicit typing

- Removed creating connections via URL. Instead, the originial pyserial Serial object must be created by user.
I believe this is desirable because it preserves the type information for the user, otherwise this repo would have
to basically copy paste all type or use some typing util I'm anaware of to achieve proper type hinting.
Also, I was burnt by the original repo implicitly expecting arguments, so screw that.

- Buffer is no longer stored as an array of bytearrays (lol), but just a bytearray.

- `call_soon` was replaced with `call_soon_threadsafe`

- It's better because it follows by very objectively correct opinions. Sorry not sorry.

## Info

- Tested on Python 3.8.20 and 3.12.6 with pyserial==3.5 on both Ubuntu 24.04 and Windows 10
- "Tested" doesn't really mean anything. It ran with a very basic example. YMMV.
