from __future__ import annotations

import os
import threading
import unittest
import uuid

from workbuddy_bridge.pipe_transport import exchange


@unittest.skipUnless(os.name == "nt", "Windows named pipes")
class PipeTests(unittest.TestCase):
    def serve(self, reply: bytes | None):
        import win32file
        import win32pipe
        name = rf"\\.\pipe\wb-bridge-test-{uuid.uuid4().hex}"
        handle = win32pipe.CreateNamedPipe(
            name, win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            1, 65536, 65536, 0, None,
        )
        release = threading.Event()
        errors = []

        def server():
            try:
                win32pipe.ConnectNamedPipe(handle, None)
                _, request = win32file.ReadFile(handle, 1024)
                if request != b"request\n":
                    errors.append(repr(request))
                if reply is not None:
                    win32file.WriteFile(handle, reply)
                release.wait(3)
            except Exception as exc:
                errors.append(str(exc))
            finally:
                handle.Close()

        thread = threading.Thread(target=server, daemon=True)
        thread.start()

        def cleanup():
            release.set()
            thread.join(4)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
        self.addCleanup(cleanup)
        return name

    def test_real_pipe_roundtrip_across_read_buffers(self):
        reply = b'{"result":"' + b"x" * 100000 + b'"}\n'
        name = self.serve(reply)
        self.assertEqual(exchange(name, b"request\n", 2), reply[:-1])

    def test_silent_pipe_has_deadline(self):
        name = self.serve(None)
        with self.assertRaises(TimeoutError):
            exchange(name, b"request\n", 0.15)


if __name__ == "__main__":
    unittest.main()
