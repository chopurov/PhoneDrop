# -*- coding: utf-8 -*-
"""QR Code generator (pure Python, standard library only).

Produces a matrix of 0/1 (light/dark) for a given text. Rendered as SVG by the
caller. Supports versions 1-4, error correction level L, byte mode.
"""
def _bch_digit(data):
    digit = 0
    while data != 0:
        digit += 1
        data >>= 1
    return digit

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x
    _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]


def _gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_gen_poly(nsym):
    g = [1]
    for i in range(nsym):
        # multiply by (x - alpha^i)
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= c
            ng[j + 1] ^= _gf_mul(c, _GF_EXP[i])
        g = ng
    return g


def _rs_encode(msg, nsym):
    gen = _rs_gen_poly(nsym)
    res = list(msg) + [0] * nsym
    for i in range(len(msg)):
        coef = res[i]
        if coef:
            for j in range(1, len(gen)):
                res[i + j] ^= _gf_mul(gen[j], coef)
    return res[len(msg):]


# Capacity (data codewords) and ECC codewords per block for versions 1-4
_CAPACITY = {
    1: {'L': 19, 'M': 16, 'Q': 13, 'H': 9},
    2: {'L': 34, 'M': 28, 'Q': 22, 'H': 16},
    3: {'L': 55, 'M': 44, 'Q': 34, 'H': 26},
    4: {'L': 80, 'M': 64, 'Q': 48, 'H': 36},
}
_ECC = {
    1: {'L': 7, 'M': 10, 'Q': 13, 'H': 17},
    2: {'L': 10, 'M': 16, 'Q': 22, 'H': 28},
    3: {'L': 15, 'M': 26, 'Q': 18, 'H': 22},
    4: {'L': 20, 'M': 18, 'Q': 26, 'H': 16},
}
_ALIGN = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
}


def _choose_version(data_bytes, level='L'):
    n = len(data_bytes)
    # byte mode overhead: 4 (mode) + 8 (count for v1-9) = 12 bits + 8*n
    bits = 12 + 8 * n
    for v in range(1, 5):
        cap_bits = _CAPACITY[v][level] * 8
        if bits <= cap_bits:
            return v
    raise ValueError("data too long for supported versions")


def _encode_data(data_bytes, version, level):
    nsym = _ECC[version][level]
    cap = _CAPACITY[version][level]
    # build bit stream
    bits = []
    def push(val, n):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)
    push(0b0100, 4)          # byte mode
    push(len(data_bytes), 8)  # char count (v1-9)
    for b in data_bytes:
        push(b, 8)
    # terminator
    for _ in range(min(4, cap * 8 - len(bits))):
        bits.append(0)
    # pad to byte
    while len(bits) % 8:
        bits.append(0)
    # convert to bytes
    out = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | bits[i + j]
        out.append(byte)
    # pad codewords
    pad = [0xEC, 0x11]
    pi = 0
    while len(out) < cap:
        out.append(pad[pi % 2])
        pi += 1
    # ECC
    ecc = _rs_encode(list(out), nsym)
    return list(out) + list(ecc), cap + nsym


def _format_positions(size):
    pos = set()
    for i in range(15):
        if i < 6:
            pos.add((i, 8))
        elif i < 8:
            pos.add((i + 1, 8))
        else:
            pos.add((size - 15 + i, 8))
    for i in range(15):
        if i < 8:
            pos.add((8, size - i - 1))
        elif i < 9:
            pos.add((8, 7))
        else:
            pos.add((8, 15 - i - 1))
    pos.add((size - 8, 8))
    return pos


def _make_matrix(version):
    size = 4 * version + 17
    m = [[None] * size for _ in range(size)]

    def set_mod(r, c, val):
        if 0 <= r < size and 0 <= c < size:
            m[r][c] = val

    # finder patterns
    def finder(r0, c0):
        for r in range(7):
            for c in range(7):
                if r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4):
                    set_mod(r0 + r, c0 + c, 1)
                else:
                    set_mod(r0 + r, c0 + c, 0)
    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    # separators
    for i in range(8):
        set_mod(7, i, 0)
        set_mod(i, 7, 0)
        set_mod(7, size - 8 + i, 0)
        set_mod(i, size - 8, 0)
        set_mod(size - 8, i, 0)
    for i in range(8):
        set_mod(size - 8 + i, 7, 0)

    # timing patterns
    for i in range(8, size - 8):
        set_mod(6, i, 1 if (i % 2 == 0) else 0)
        set_mod(i, 6, 1 if (i % 2 == 0) else 0)

    # alignment patterns
    positions = _ALIGN[version]
    for r in positions:
        for c in positions:
            if m[r][c] is not None:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    if abs(dr) == 2 or abs(dc) == 2 or (dr == 0 and dc == 0):
                        set_mod(r + dr, c + dc, 1)
                    else:
                        set_mod(r + dc, c + dc, 0) if False else set_mod(r + dr, c + dc, 0)

    # dark module
    set_mod(4 * version + 9, 8, 1)

    # reserve format info positions (so data placement skips them)
    for (r, c) in _format_positions(size):
        if m[r][c] is None:
            m[r][c] = 0

    return m


def _place_data(matrix, codewords, version):
    """Размещение данных по алгоритму qrcode.map_data."""
    size = len(matrix)
    inc = -1
    row = size - 1
    bit_index = 7
    byte_index = 0
    data_len = len(codewords)

    mask_func = None  # маска применяется позже; здесь только данные

    for col in range(size - 1, 0, -2):
        if col <= 6:
            col -= 1
        col_range = (col, col - 1)
        while True:
            for c in col_range:
                if matrix[row][c] is None:
                    dark = False
                    if byte_index < data_len:
                        dark = ((codewords[byte_index] >> bit_index) & 1) == 1
                    matrix[row][c] = 1 if dark else 0
                    bit_index -= 1
                    if bit_index == -1:
                        byte_index += 1
                        bit_index = 7
            row += inc
            if row < 0 or size <= row:
                row -= inc
                inc = -inc
                break
    return matrix


def _mask_func(mask, r, c):
    if mask == 0:
        return (r + c) % 2 == 0
    if mask == 1:
        return r % 2 == 0
    if mask == 2:
        return c % 3 == 0
    if mask == 3:
        return (r + c) % 3 == 0
    if mask == 4:
        return ((r // 2) + (c // 3)) % 2 == 0
    if mask == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if mask == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    if mask == 7:
        return ((r + c) % 2 + (r * c) % 3) % 2 == 0
    return False


def _is_functional(matrix, r, c, version):
    size = len(matrix)
    # finder + separators
    if r < 9 and c < 9:
        return True
    if r < 9 and c >= size - 8:
        return True
    if r >= size - 8 and c < 9:
        return True
    # timing
    if r == 6 or c == 6:
        return True
    # alignment
    for ar in _ALIGN[version]:
        for ac in _ALIGN[version]:
            if ar == 6 or ac == 6:
                continue
            if abs(r - ar) <= 2 and abs(c - ac) <= 2:
                return True
    # dark module
    if r == 4 * version + 9 and c == 8:
        return True
    # format info
    if (r, c) in _format_positions(size):
        return True
    return False


def _penalty(matrix, mask, version):
    size = len(matrix)
    # apply mask virtually
    m = [row[:] for row in matrix]
    for r in range(size):
        for c in range(size):
            if _is_functional(m, r, c, version):
                continue
            if _mask_func(mask, r, c):
                m[r][c] ^= 1
    # rule 1: runs of same color >=5
    score = 0
    for r in range(size):
        run = 1
        for c in range(1, size):
            if m[r][c] == m[r][c - 1]:
                run += 1
            else:
                if run >= 5:
                    score += run - 2  # n-2? simplified: 3 + (run-5)
                run = 1
        if run >= 5:
            score += run - 2
    for c in range(size):
        run = 1
        for r in range(1, size):
            if m[r][c] == m[r - 1][c]:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run = 1
        if run >= 5:
            score += run - 2
    # rule 2: 2x2 blocks
    for r in range(size - 1):
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    # rule 3: finder-like patterns
    for r in range(size):
        for c in range(size - 6):
            if (m[r][c] == 1 and m[r][c+1] == 0 and m[r][c+2] == 1 and
                    m[r][c+3] == 1 and m[r][c+4] == 1 and m[r][c+5] == 0 and m[r][c+6] == 1):
                score += 40
    for c in range(size):
        for r in range(size - 6):
            if (m[r][c] == 1 and m[r+1][c] == 0 and m[r+2][c] == 1 and
                    m[r+3][c] == 1 and m[r+4][c] == 1 and m[r+5][c] == 0 and m[r+6][c] == 1):
                score += 40
    # rule 4: balance
    dark = sum(sum(row) for row in m)
    total = size * size
    pct = dark * 100 // total
    prev5 = (pct // 5) * 5
    score += abs(prev5 - 50) // 5 * 10
    return score


def _format_info(ecc_level, mask):
    # ecc level bits: L=1, M=0, Q=3, H=2
    ecc_bits = {'L': 1, 'M': 0, 'Q': 3, 'H': 2}[ecc_level]
    fmt = (ecc_bits << 3) | mask
    # BCH (15,5)
    g = 0x537
    rem = fmt << 10
    for i in range(4, -1, -1):
        if rem & (1 << (i + 10)):
            rem ^= g << i
    fmt = ((fmt << 10) | rem) ^ 0x5412
    return fmt


def _place_format(matrix, ecc_level, mask):
    size = len(matrix)
    fmt = _format_info(ecc_level, mask)
    bits = [(fmt >> i) & 1 for i in range(14, -1, -1)]
    # positions around top-left finder
    positions1 = []
    for i in range(6):
        positions1.append((i, 8))
    positions1.append((7, 8))
    positions1.append((8, 8))
    positions1.append((8, 7))
    for i in range(7, -1, -1):
        positions1.append((8, i))
    # top-right
    positions2 = []
    for i in range(7, -1, -1):
        positions2.append((8, size - 8 + i))  # careful
    # Actually standard: second copy along top-right and bottom-left.
    # Simpler: place in two known regions.
    # Region A (top-right): column size-8, rows 0..7 and 8? We'll use standard layout.
    # Let's do robust standard placement:
    # Clear any existing
    # Top-left vertical+horizontal done above (15 bits): positions1 has 9+6=15? Let's count: 6+1+1+1+7=16? recount.
    pass


def _lost_point(modules, modules_count):
    lost = 0
    lost += _lost_point_level1(modules, modules_count)
    lost += _lost_point_level2(modules, modules_count)
    lost += _lost_point_level3(modules, modules_count)
    lost += _lost_point_level4(modules, modules_count)
    return lost


def _lost_point_level1(modules, modules_count):
    lost_point = 0
    modules_range = range(modules_count)
    container = [0] * (modules_count + 1)
    for row in modules_range:
        this_row = modules[row]
        previous_color = this_row[0]
        length = 0
        for col in modules_range:
            if this_row[col] == previous_color:
                length += 1
            else:
                if length >= 5:
                    container[length] += 1
                length = 1
                previous_color = this_row[col]
        if length >= 5:
            container[length] += 1
    for col in modules_range:
        previous_color = modules[0][col]
        length = 0
        for row in modules_range:
            if modules[row][col] == previous_color:
                length += 1
            else:
                if length >= 5:
                    container[length] += 1
                length = 1
                previous_color = modules[row][col]
        if length >= 5:
            container[length] += 1
    lost_point += sum(container[each_length] * (each_length - 2) for each_length in range(5, modules_count + 1))
    return lost_point


def _lost_point_level2(modules, modules_count):
    lost_point = 0
    modules_range = range(modules_count - 1)
    for row in modules_range:
        this_row = modules[row]
        next_row = modules[row + 1]
        modules_range_iter = iter(modules_range)
        for col in modules_range_iter:
            top_right = this_row[col + 1]
            if top_right != next_row[col + 1]:
                next(modules_range_iter, None)
            elif top_right != this_row[col]:
                continue
            elif top_right != next_row[col]:
                continue
            else:
                lost_point += 3
    return lost_point


def _lost_point_level3(modules, modules_count):
    modules_range = range(modules_count)
    modules_range_short = range(modules_count - 10)
    lost_point = 0
    for row in modules_range:
        this_row = modules[row]
        modules_range_short_iter = iter(modules_range_short)
        for col in modules_range_short_iter:
            if (not this_row[col + 1] and this_row[col + 4] and not this_row[col + 5] and
                    this_row[col + 6] and not this_row[col + 9] and
                    ((this_row[col + 0] and this_row[col + 2] and this_row[col + 3] and
                      not this_row[col + 7] and not this_row[col + 8] and not this_row[col + 10]) or
                     (not this_row[col + 0] and not this_row[col + 2] and not this_row[col + 3] and
                      this_row[col + 7] and this_row[col + 8] and this_row[col + 10]))):
                lost_point += 40
            if this_row[col + 10]:
                next(modules_range_short_iter, None)
    for col in modules_range:
        modules_range_short_iter = iter(modules_range_short)
        for row in modules_range_short_iter:
            if (not modules[row + 1][col] and modules[row + 4][col] and not modules[row + 5][col] and
                    modules[row + 6][col] and not modules[row + 9][col] and
                    ((modules[row + 0][col] and modules[row + 2][col] and modules[row + 3][col] and
                      not modules[row + 7][col] and not modules[row + 8][col] and not modules[row + 10][col]) or
                     (not modules[row + 0][col] and not modules[row + 2][col] and not modules[row + 3][col] and
                      modules[row + 7][col] and modules[row + 8][col] and modules[row + 10][col]))):
                lost_point += 40
            if modules[row + 10][col]:
                next(modules_range_short_iter, None)
    return lost_point


def _lost_point_level4(modules, modules_count):
    dark_count = sum(map(sum, modules))
    percent = float(dark_count) / (modules_count ** 2)
    rating = int(abs(percent * 100 - 50) / 5)
    return rating * 10


def make_qr(data, level='L'):
    data_bytes = data.encode('utf-8')
    version = _choose_version(data_bytes, level)
    codewords, total = _encode_data(data_bytes, version, level)
    matrix = _make_matrix(version)
    matrix = _place_data(matrix, codewords, version)
    size = len(matrix)
    # for mask evaluation, fixed module should be 0 (test mode)
    fm_pos = (size - 8, 8)
    saved_fm = matrix[fm_pos[0]][fm_pos[1]]
    matrix[fm_pos[0]][fm_pos[1]] = 0
    best_mask = 0
    best_score = None
    for mask in range(8):
        mcopy = [row[:] for row in matrix]
        for r in range(size):
            for c in range(size):
                if _is_functional(mcopy, r, c, version):
                    continue
                if _mask_func(mask, r, c):
                    mcopy[r][c] ^= 1
        sc = _lost_point(mcopy, size)
        if best_score is None or sc < best_score:
            best_score = sc
            best_mask = mask
    # restore fixed module
    matrix[fm_pos[0]][fm_pos[1]] = saved_fm
    # apply best mask
    for r in range(size):
        for c in range(size):
            if _is_functional(matrix, r, c, version):
                continue
            if _mask_func(best_mask, r, c):
                matrix[r][c] ^= 1
    # format info (type info) placement — по образцу qrcode.setup_type_info
    data = ({'L': 1, 'M': 0, 'Q': 3, 'H': 2}[level] << 3) | best_mask
    # BCH
    d = data << 10
    while _bch_digit(d) - _bch_digit(1335) >= 0:
        d ^= 1335 << (_bch_digit(d) - _bch_digit(1335))
    bits = ((data << 10) | d) ^ 21522

    size = len(matrix)
    # vertical
    for i in range(15):
        mod = ((bits >> i) & 1) == 1
        if i < 6:
            matrix[i][8] = 1 if mod else 0
        elif i < 8:
            matrix[i + 1][8] = 1 if mod else 0
        else:
            matrix[size - 15 + i][8] = 1 if mod else 0
    # horizontal
    for i in range(15):
        mod = ((bits >> i) & 1) == 1
        if i < 8:
            matrix[8][size - i - 1] = 1 if mod else 0
        elif i < 9:
            matrix[8][7] = 1 if mod else 0
        else:
            matrix[8][15 - i - 1] = 1 if mod else 0
    # fixed (dark) module
    matrix[size - 8][8] = 1
    return matrix


def matrix_to_svg(matrix, module_size=8, quiet=4):
    size = len(matrix)
    total = (size + 2 * quiet) * module_size
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{total}" viewBox="0 0 {total} {total}" shape-rendering="crispEdges">']
    parts.append(f'<rect width="{total}" height="{total}" fill="#ffffff"/>')
    for r in range(size):
        for c in range(size):
            if matrix[r][c]:
                x = (c + quiet) * module_size
                y = (r + quiet) * module_size
                parts.append(f'<rect x="{x}" y="{y}" width="{module_size}" height="{module_size}" fill="#000000"/>')
    parts.append('</svg>')
    return ''.join(parts)
