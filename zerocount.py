"""
ZERO-COUNT ENCODING SYSTEM
━━━━━━━━━━━━━━━━━━━━━━━━━━
Your idea: zeros are grammar (free to count), data is completely separate.

Format:   [GRAMMAR] 00000000 [DATA]

Grammar token = N zeros + terminating 1:
  N=1  value-reference  → unary pointer follows (K ones + zero = data[K])
  N=2  instruction      → instruction-code token follows
  N=3  unit  open/close (paired by nesting context)
  N=4  block open/close
  N=5  section open/close
  N=7  document open/close

Instruction codes (zero-count token after N=2):
  N=1 → ADD,  N=2 → SUB,  N=3 → MUL,  N=4 → STORE

Separator: 00000000 (8 zeros — never appears in grammar since max token is N=7,
           and pointers are all-ones+zero so add no zero-runs)
"""

INSTR_CODES  = {1:"ADD", 2:"SUB", 3:"MUL", 4:"STORE"}
INSTR_LOOKUP = {v:k for k,v in INSTR_CODES.items()}
SEPARATOR    = "00000000"

def ztok(n):  return "0"*n + "1"           # grammar token: N zeros + 1
def uptr(n):  return "1"*(n+1) + "0"       # unary pointer: N+1 ones + 0
def raw8(n):  return format(n&0xFF,"08b")  # 8-bit data value

# ── ENCODER ───────────────────────────────────────────────────────────────────
def encode(operations, values):
    g = [ztok(7), ztok(5)]
    for op_name, *idxs in operations:
        g += [ztok(4), ztok(3)]
        g += [ztok(2), ztok(INSTR_LOOKUP[op_name])]
        for idx in idxs:
            g += [ztok(1), uptr(idx)]
        g += [ztok(3), ztok(4)]
    g += [ztok(5), ztok(7)]
    grammar = "".join(g)
    assert SEPARATOR not in grammar, "Separator collision in grammar!"
    data = "".join(raw8(v) for v in values)
    return grammar + SEPARATOR + data

# ── DECODER ───────────────────────────────────────────────────────────────────
def decode(bitstream):
    sep_pos     = bitstream.index(SEPARATOR)
    grammar     = bitstream[:sep_pos]
    data_raw    = bitstream[sep_pos+8:]
    data_values = [int(data_raw[i:i+8], 2)
                   for i in range(0, len(data_raw), 8)
                   if len(data_raw[i:i+8]) == 8]

    print(f"  Data values : {data_values}")
    print("\n── DECODER TRACE ─────────────────────────────────────────")

    pos, ops, cur, ctx = 0, [], None, []

    while pos < len(grammar):
        # read zero-count token
        zc = 0
        while pos < len(grammar) and grammar[pos] == "0":
            zc += 1; pos += 1
        if pos < len(grammar) and grammar[pos] == "1": pos += 1
        if zc == 0: continue

        if zc == 7:
            tag = "doc"
            if tag not in ctx: ctx.append(tag); print("  [document open]")
            else:               ctx.remove(tag); print("  [document close]")

        elif zc == 5:
            tag = "sec"
            if tag not in ctx: ctx.append(tag); print("  [section open]")
            else:               ctx.remove(tag); print("  [section close]")

        elif zc == 4:
            tag = "blk"
            if tag not in ctx:
                ctx.append(tag); print("  [block open]")
            else:
                ctx.remove(tag); print("  [block close]")
                if cur: ops.append(cur); cur = None

        elif zc == 3:
            tag = "unt"
            if tag not in ctx: ctx.append(tag); print("  [unit open]")
            else:               ctx.remove(tag); print("  [unit close]")

        elif zc == 2:
            # read instruction code token
            izc = 0
            while pos < len(grammar) and grammar[pos] == "0": izc += 1; pos += 1
            if pos < len(grammar) and grammar[pos] == "1": pos += 1
            name = INSTR_CODES.get(izc, f"UNKNOWN({izc})")
            cur  = {"op": name, "args": [], "values": []}
            print(f"  [instruction: {name}]")

        elif zc == 1:
            # read unary pointer: count ones until we hit a zero
            ptr = 0
            while pos < len(grammar) and grammar[pos] == "1": ptr += 1; pos += 1
            if pos < len(grammar) and grammar[pos] == "0": pos += 1  # consume zero
            ptr -= 1  # uptr(n) = (n+1) ones, so ones_count - 1 = index
            val  = data_values[ptr] if 0 <= ptr < len(data_values) else None
            if cur: cur["args"].append(ptr); cur["values"].append(val)
            print(f"  [value ref → data[{ptr}] = {val}]")

    print("──────────────────────────────────────────────────────────")
    return ops, data_values

# ── EXECUTOR ──────────────────────────────────────────────────────────────────
def execute(ops):
    print("\n── EXECUTION ─────────────────────────────────────────────")
    results = []
    for op in ops:
        name, vals, args = op["op"], op["values"], op["args"]
        if   name=="ADD"   and len(vals)>=2: r=vals[0]+vals[1]; print(f"  ADD   {vals[0]} + {vals[1]} = {r}");   results.append(r)
        elif name=="SUB"   and len(vals)>=2: r=vals[0]-vals[1]; print(f"  SUB   {vals[0]} - {vals[1]} = {r}");   results.append(r)
        elif name=="MUL"   and len(vals)>=2: r=vals[0]*vals[1]; print(f"  MUL   {vals[0]} × {vals[1]} = {r}");   results.append(r)
        elif name=="STORE" and len(vals)>=1: print(f"  STORE data[{args[0]}] = {vals[0]}"); results.append(vals[0])
        else: print(f"  {name} — skipped")
    print("──────────────────────────────────────────────────────────")
    return results

# ── STATS ─────────────────────────────────────────────────────────────────────
def stats(bitstream, operations, values):
    sep   = bitstream.index(SEPARATOR)
    total = len(bitstream)
    naive = sum(8 + len(op[1:])*8 for op in operations) + len(values)*8
    diff  = total - naive
    note  = "(overhead — normal at small scale)" if diff>0 else "(your system wins!)"
    print(f"\n── STATS ─────────────────────────────────────────────────")
    print(f"  Grammar bits : {sep}")
    print(f"  Data bits    : {len(bitstream)-sep-8}")
    print(f"  Total bits   : {total}")
    print(f"  Naive binary : {naive} bits")
    print(f"  Difference   : {diff:+d} bits  {note}")
    print(f"──────────────────────────────────────────────────────────\n")

# ── TEST RUNNER ───────────────────────────────────────────────────────────────
def run_test(name, operations, values, expected=None):
    print(f"\n{'═'*56}\n  TEST: {name}\n{'═'*56}")
    bs = encode(operations, values)
    print(f"  Bitstream : {bs}")
    print(f"  Length    : {len(bs)} bits\n")
    decoded_ops, decoded_vals = decode(bs)
    results = execute(decoded_ops)
    stats(bs, operations, values)
    ok = results == expected
    print(f"  Expected  : {expected}")
    print(f"  Got       : {results}")
    print(f"  {'✓ PASS' if ok else '✗ FAIL'}\n")
    return ok

# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "█"*56)
    print("  ZERO-COUNT ENCODING SYSTEM")
    print("  Zeros = grammar.  Data = separate.  No tables.")
    print("█"*56)

    passed = sum([
        run_test("ADD 3 + 5",    [("ADD",0,1)],             [3,5],     expected=[8]),
        run_test("SUB 10 - 4",   [("SUB",0,1)],             [10,4],    expected=[6]),
        run_test("MUL 7 × 6",    [("MUL",0,1)],             [7,6],     expected=[42]),
        run_test("ADD then MUL", [("ADD",0,1),("MUL",2,3)], [2,3,7,6], expected=[5,42]),
        run_test("STORE 42",     [("STORE",0)],              [42],      expected=[42]),
    ])
    total = 5
    print(f"{'█'*56}")
    print(f"  {passed}/{total} tests passed {'✓' if passed==total else '✗'}")
    print(f"{'█'*56}\n")