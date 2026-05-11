"""Win32 named-mapping helper shared by the AC1 and AC Evo readers.

Python's stdlib ``mmap.mmap(-1, size, tagname=name)`` will *create* a mapping
under that name if one doesn't exist, instead of failing. That makes it
impossible to detect "game not running" — we'd silently attach to a fresh
empty mapping and read zeros. The Win32 ``OpenFileMappingW`` call returns
NULL when the name is not present, which is what we want.
"""
from __future__ import annotations

import ctypes
import sys

_FILE_MAP_READ = 0x0004

if sys.platform != "win32":
    raise OSError("AC Telemetry Overlay is Windows-only")

_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

_OpenFileMappingW = _KERNEL32.OpenFileMappingW
_OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_wchar_p]
_OpenFileMappingW.restype = ctypes.c_void_p

_MapViewOfFile = _KERNEL32.MapViewOfFile
_MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                           ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
_MapViewOfFile.restype = ctypes.c_void_p

_UnmapViewOfFile = _KERNEL32.UnmapViewOfFile
_UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_UnmapViewOfFile.restype = ctypes.c_int32

_CloseHandle = _KERNEL32.CloseHandle
_CloseHandle.argtypes = [ctypes.c_void_p]
_CloseHandle.restype = ctypes.c_int32


class NamedMapping:
    """Read-only view of an existing Windows named file-mapping.

    Raises FileNotFoundError when the name does not exist (i.e. the game is
    not running or hasn't loaded yet).
    """

    def __init__(self, name: str, size: int) -> None:
        handle = _OpenFileMappingW(_FILE_MAP_READ, False, name)
        if not handle:
            err = ctypes.get_last_error()
            # ERROR_FILE_NOT_FOUND == 2: the named mapping doesn't exist.
            if err == 2:
                raise FileNotFoundError(f"named mapping not found: {name}")
            raise OSError(err, ctypes.FormatError(err), name)
        view = _MapViewOfFile(handle, _FILE_MAP_READ, 0, 0, size)
        if not view:
            err = ctypes.get_last_error()
            _CloseHandle(handle)
            raise OSError(err, ctypes.FormatError(err), name)
        self._handle = handle
        self._view = view
        self._size = size

    def read(self) -> bytes:
        return ctypes.string_at(self._view, self._size)

    def close(self) -> None:
        if self._view:
            _UnmapViewOfFile(self._view)
            self._view = None
        if self._handle:
            _CloseHandle(self._handle)
            self._handle = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass


__all__ = ["NamedMapping"]
