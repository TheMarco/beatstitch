"""Read only the pinned official Spleeter numeric TensorBundle checkpoint.

The archive and both files are verified before decoding. This bounded reader
understands the LevelDB table and protobuf fields used by this one checkpoint;
it never loads TensorFlow graphs, pickle, Python code, or arbitrary checkpoints.
"""
from __future__ import annotations
from io import BytesIO
import hashlib
import math
from pathlib import Path
import struct
import zipfile

import numpy as np

FILES = {
    'model.index': (10368, '99ea41b3ef9bfbc6a34520409427af5ba7a883173e57ea25d6c487584b891b18'),
    'model.data-00000-of-00001': (157228152, '3823e0ef50835cca3907c386dc9667a6a9af77b40a20c3ab845a8d6c9592255d'),
}
CONVERTED_SHA256 = 'a8b3fb520dc13f47c40e5b5d7df88c682a409da56c8bf03b048290e27543617c'
CONVERTED_BYTES = 157306998


def varint(data, position):
    value = 0
    for shift in range(0, 70, 7):
        if position >= len(data): raise ValueError('Truncated checkpoint integer.')
        byte = data[position]; position += 1
        value |= (byte & 127) << shift
        if byte < 128: return value, position
    raise ValueError('Oversized checkpoint integer.')


def fields(data):
    position = 0
    while position < len(data):
        tag, position = varint(data, position)
        number, wire = tag >> 3, tag & 7
        if not number: raise ValueError('Invalid checkpoint field.')
        if wire == 0:
            value, position = varint(data, position)
        elif wire in (1, 5):
            length = 8 if wire == 1 else 4
            value = data[position:position+length]; position += length
            if len(value) != length: raise ValueError('Truncated checkpoint field.')
        elif wire == 2:
            length, position = varint(data, position)
            if length > len(data) - position: raise ValueError('Invalid checkpoint field length.')
            value = data[position:position+length]; position += length
        else:
            raise ValueError('Unsupported checkpoint field.')
        yield number, value


def handle(data, position=0):
    offset, position = varint(data, position)
    size, position = varint(data, position)
    return (offset, size), position


def block(table, item):
    offset, size = item
    if not 0 <= offset < len(table) or not 4 <= size <= len(table)-offset-5:
        raise ValueError('Checkpoint block is outside the index.')
    if table[offset+size] != 0:
        raise ValueError('The pinned checkpoint uses uncompressed index blocks.')
    data = table[offset:offset+size]
    count = struct.unpack('<I', data[-4:])[0]
    end = len(data) - 4 * (count+1)
    if not 1 <= count or end < 0: raise ValueError('Invalid checkpoint block restarts.')
    position = 0; previous = b''
    while position < end:
        shared, position = varint(data, position)
        added, position = varint(data, position)
        length, position = varint(data, position)
        if shared > len(previous) or added+length > end-position:
            raise ValueError('Invalid checkpoint index entry.')
        key = previous[:shared] + data[position:position+added]; position += added
        value = data[position:position+length]; position += length
        yield key, value
        previous = key


def read_tensors(folder):
    folder = Path(folder)
    for name, (size, expected) in FILES.items():
        path = folder/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            raise ValueError('The official checkpoint file is missing or changed: '+name)
        with path.open('rb') as stream: actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected: raise ValueError('The official checkpoint checksum failed: '+name)
    table = (folder/'model.index').read_bytes()
    if struct.unpack('<Q', table[-8:])[0] != 0xdb4775248b80fb57:
        raise ValueError('Invalid checkpoint table footer.')
    footer = table[-48:-8]
    _, position = handle(footer)
    index, _ = handle(footer, position)
    data = np.memmap(folder/'model.data-00000-of-00001', mode='r', dtype='uint8')
    result = {}
    for _, value in block(table, index):
        data_handle, _ = handle(value)
        for key, value in block(table, data_handle):
            if not key:
                header = dict(fields(value))
                if header.get(1) != 1 or header.get(2, 0) != 0:
                    raise ValueError('Unsupported checkpoint shard or endianness.')
                continue
            name = key.decode('ascii')
            entry = dict(fields(value))
            if name in result or entry.get(3, 0) != 0 or 7 in entry:
                raise ValueError('Unsupported duplicate, sliced or sharded tensor.')
            dtype = {1: '<f4', 9: '<i8'}.get(entry.get(1))
            if dtype is None: raise ValueError('Unexpected checkpoint tensor type.')
            shape = []
            for number, dimension in fields(entry.get(2, b'')):
                if number != 2: raise ValueError('Unexpected tensor shape field.')
                size = dict(fields(dimension)).get(1, 0)
                if not 1 <= size <= 512: raise ValueError('Unexpected tensor dimension.')
                shape.append(size)
            if len(shape) > 4: raise ValueError('Unexpected tensor rank.')
            count = math.prod(shape)
            offset, size = entry.get(4, 0), entry.get(5, 0)
            if size != count*np.dtype(dtype).itemsize or not 0 <= offset <= len(data)-size:
                raise ValueError('Invalid tensor size or offset.')
            result[name] = np.frombuffer(data, dtype=dtype, count=count, offset=offset).reshape(tuple(shape))
    if len(result) != 297 or 'global_step' not in result:
        raise ValueError('Unexpected official checkpoint tensor count.')
    return result


def convert_checkpoint(folder, destination):
    destination = Path(destination)
    if destination.exists(): raise FileExistsError('Conversion destination already exists.')
    tensors = read_tensors(folder)
    try:
        with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_STORED) as archive:
            for name in sorted(tensors):
                if name == 'global_step': continue
                array = tensors[name]
                if array.dtype != np.dtype('<f4') or not np.isfinite(array).all():
                    raise ValueError('The checkpoint contains invalid numeric tensors.')
                buffer = BytesIO(); np.lib.format.write_array(buffer, array, allow_pickle=False)
                entry = zipfile.ZipInfo(name+'.npy', date_time=(1980,1,1,0,0,0))
                # ZipInfo otherwise records DOS on Windows and Unix on macOS/
                # Linux, changing the pinned model hash despite identical arrays.
                entry.create_system = 3
                entry.external_attr = 0o600 << 16
                archive.writestr(entry, buffer.getvalue())
        with destination.open('rb') as stream: actual = hashlib.file_digest(stream,'sha256').hexdigest()
        if destination.stat().st_size != CONVERTED_BYTES or actual != CONVERTED_SHA256:
            raise ValueError('Converted model differs from the TensorFlow-verified weights.')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return {'sha256': actual, 'size': destination.stat().st_size}
