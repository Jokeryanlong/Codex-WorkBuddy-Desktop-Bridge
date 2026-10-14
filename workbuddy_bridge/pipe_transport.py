"""Deadline-bound Windows named-pipe exchange, including silent peers."""
from __future__ import annotations

import time


def exchange(path: str, data: bytes, timeout_seconds: float = 10) -> bytes:
    import pywintypes
    import win32con
    import win32event
    import win32file
    import win32pipe

    deadline = time.monotonic() + timeout_seconds

    def remaining_ms() -> int:
        return max(1, int((deadline - time.monotonic()) * 1000))

    win32pipe.WaitNamedPipe(path, remaining_ms())
    handle = win32file.CreateFile(
        path, win32con.GENERIC_READ | win32con.GENERIC_WRITE, 0, None,
        win32con.OPEN_EXISTING, win32con.FILE_FLAG_OVERLAPPED, None,
    )
    event = win32event.CreateEvent(None, True, False, None)

    def perform(operation):
        if time.monotonic() >= deadline:
            raise TimeoutError("WorkBuddy sidecar pipe deadline exceeded")
        win32event.ResetEvent(event)
        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = event
        status, buffer = operation(overlapped)
        if status not in (0, 997):
            raise OSError(f"Unexpected pipe I/O status: {status}")
        if status == 997 and win32event.WaitForSingleObject(event, remaining_ms()) != win32event.WAIT_OBJECT_0:
            # Reap the cancelled operation before its Python buffer is released.
            # All operations on this handle are issued by this same thread.
            win32file.CancelIo(handle)
            try:
                win32file.GetOverlappedResult(handle, overlapped, True)
            except pywintypes.error:
                pass
            raise TimeoutError("WorkBuddy sidecar pipe deadline exceeded")
        return win32file.GetOverlappedResult(handle, overlapped, True), buffer

    try:
        count, _ = perform(lambda ov: win32file.WriteFile(handle, data, ov))
        if count != len(data):
            raise OSError("Incomplete WorkBuddy sidecar request write")
        chunks = bytearray()
        while len(chunks) <= 2 * 1024 * 1024:
            count, buffer = perform(lambda ov: win32file.ReadFile(handle, 65536, ov))
            if not count:
                raise OSError("WorkBuddy sidecar closed without a complete response")
            chunks.extend(bytes(buffer[:count]))
            if b"\n" in chunks:
                return bytes(chunks).split(b"\n", 1)[0]
        raise OSError("WorkBuddy sidecar response exceeds 2 MiB")
    finally:
        event.Close()
        handle.Close()
