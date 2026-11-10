"""Real shared-library calls must leave regular-file I/O untouched even inside the SSD offset range."""
import ctypes
import os
from pathlib import Path
import sys

root = Path(sys.argv[1])
library = ctypes.CDLL(str(root / 'fault_io.so'))
library.rrzc_fault_fired.restype = ctypes.c_uint64
library.rrzc_fault_arm()
fd = os.open(root / 'fault-scope.bin', os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
try:
    for name in ('pwrite', 'pwrite64'):
        write = getattr(library, name)
        write.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_longlong]
        write.restype = ctypes.c_ssize_t
        data = ctypes.create_string_buffer(b'guard')
        assert write(fd, data, 5, 3 * (128 << 20)) == 5
        assert os.pread(fd, 5, 3 * (128 << 20)) == b'guard'
    assert library.rrzc_fault_fired() == 0
finally:
    os.ftruncate(fd, 0)
    os.close(fd)
print('fault scope: regular files preserved; no simulated-device fault exercised')
