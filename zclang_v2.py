"""
ZC-LANG v2 — Zero-Count Encoding System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Full language: arithmetic, comparisons, if/else, while loops.
Turing complete. No lookup tables in bytecode.

BITSTREAM FORMAT:
  [16-bit grammar length] [grammar bits] [data bits]

  The first 16 bits tell the decoder exactly how long the grammar section is.
  No separator pattern needed. Zero collision risk.

GRAMMAR TOKENS (N zeros + terminating 1):
  N=1   value reference  → unary pointer follows
  N=2   instruction      → instruction-code token follows
  N=3   unit  open/close (paired)
  N=4   block open/close (paired)
  N=5   section open/close (paired)
  N=6   jump-if-false    → uptr(cond_data_idx) + uptr(instr_target)
  N=7   document open/close (paired)
  N=8   unconditional jump → uptr(instr_target)

INSTRUCTION CODES (N after instruction token):
  N=1  ADD      N=2  SUB      N=3  MUL      N=4  DIV
  N=5  LOAD     N=6  PRINT
  N=7  CMP_LT   N=8  CMP_GT   N=9  CMP_EQ
  N=10 CMP_NEQ  N=11 CMP_LTE  N=12 CMP_GTE

UNARY POINTERS: (index+1) ones + zero. Zero-free, no grammar ambiguity.
DATA: flat array of 16-bit values (0-65535), each as 16 raw bits.
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PRIMITIVES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INSTR_CODES = {
    1:"ADD",   2:"SUB",    3:"MUL",    4:"DIV",
    5:"LOAD",  6:"PRINT",
    7:"CMP_LT",8:"CMP_GT", 9:"CMP_EQ",
    10:"CMP_NEQ",11:"CMP_LTE",12:"CMP_GTE"
}
INSTR_LOOKUP = {v:k for k,v in INSTR_CODES.items()}

def ztok(n):  return "0"*n + "1"
def uptr(n):  return "1"*(n+1) + "0"
def raw16(n): return format(n & 0xFFFF, "016b")
def raw16_to_int(bits): return int(bits, 2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENCODER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def encode_bytecode(instructions, data_values):
    g = [ztok(7), ztok(5)]

    for instr in instructions:
        op   = instr["op"]
        args = instr.get("args", [])

        g += [ztok(4), ztok(3)]

        if op == "JMP":
            g += [ztok(8), uptr(instr["jump_to"])]
        elif op == "JIF":
            g += [ztok(6), uptr(args[0]), uptr(instr["jump_to"])]
        else:
            g += [ztok(2), ztok(INSTR_LOOKUP[op])]
            for a in args:
                g += [ztok(1), uptr(a)]

        g += [ztok(3), ztok(4)]

    g += [ztok(5), ztok(7)]
    grammar = "".join(g)
    data    = "".join(raw16(v) for v in data_values)

    # Header: 16-bit grammar length
    header = raw16(len(grammar))
    return header + grammar + data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DECODER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def read_uptr(bits, pos):
    count = 0
    while pos < len(bits) and bits[pos] == "1":
        count += 1; pos += 1
    if pos < len(bits) and bits[pos] == "0":
        pos += 1
    return count - 1, pos

def decode_bytecode(bitstream):
    # read 16-bit header
    grammar_len = raw16_to_int(bitstream[:16])
    grammar     = bitstream[16 : 16 + grammar_len]
    data_raw    = bitstream[16 + grammar_len:]
    data_values = [raw16_to_int(data_raw[i:i+16])
                   for i in range(0, len(data_raw), 16)
                   if len(data_raw[i:i+16]) == 16]

    pos, ops, cur, ctx = 0, [], None, []

    while pos < len(grammar):
        zc = 0
        while pos < len(grammar) and grammar[pos] == "0":
            zc += 1; pos += 1
        if pos < len(grammar) and grammar[pos] == "1":
            pos += 1
        if zc == 0:
            continue

        if zc == 7:
            tag = "doc"
            if tag not in ctx: ctx.append(tag)
            else: ctx.remove(tag)

        elif zc == 5:
            tag = "sec"
            if tag not in ctx: ctx.append(tag)
            else: ctx.remove(tag)

        elif zc == 4:
            tag = "blk"
            if tag not in ctx:
                ctx.append(tag)
            else:
                ctx.remove(tag)
                if cur: ops.append(cur); cur = None

        elif zc == 3:
            tag = "unt"
            if tag not in ctx: ctx.append(tag)
            else: ctx.remove(tag)

        elif zc == 8:
            target, pos = read_uptr(grammar, pos)
            cur = {"op": "JMP", "args": [], "jump_to": target}

        elif zc == 6:
            cond, pos   = read_uptr(grammar, pos)
            target, pos = read_uptr(grammar, pos)
            cur = {"op": "JIF", "args": [cond], "jump_to": target}

        elif zc == 2:
            izc = 0
            while pos < len(grammar) and grammar[pos] == "0":
                izc += 1; pos += 1
            if pos < len(grammar) and grammar[pos] == "1":
                pos += 1
            cur = {"op": INSTR_CODES.get(izc, f"UNK{izc}"), "args": []}

        elif zc == 1:
            ptr, pos = read_uptr(grammar, pos)
            if cur: cur["args"].append(ptr)

    return ops, data_values


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMPILER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Compiler:
    def __init__(self):
        self.data   = []
        self.vars   = {}
        self.instrs = []

    def alloc(self, value=0):
        idx = len(self.data)
        self.data.append(int(value) & 0xFFFF)
        return idx

    def var(self, name):
        if name not in self.vars:
            self.vars[name] = self.alloc(0)
        return self.vars[name]

    def emit(self, op, args=None, jump_to=None):
        instr = {"op": op, "args": args or []}
        if jump_to is not None:
            instr["jump_to"] = jump_to
        self.instrs.append(instr)
        return len(self.instrs) - 1

    def patch(self, idx, jump_to):
        self.instrs[idx]["jump_to"] = jump_to

    def here(self):
        return len(self.instrs)

    def compile(self, source):
        lines = source.strip().splitlines()
        lines = [l.rstrip() for l in lines]
        self._compile_block(lines, 0, len(lines))
        return encode_bytecode(self.instrs, self.data)

    def _get_indent(self, line):
        return len(line) - len(line.lstrip())

    def _compile_block(self, lines, start, end):
        i = start
        while i < end:
            raw  = lines[i]
            line = raw.strip()
            if not line or line.startswith("#"):
                i += 1; continue
            if line.startswith("if "):
                i = self._compile_if(lines, i, end)
            elif line.startswith("while "):
                i = self._compile_while(lines, i, end)
            elif line.startswith("print "):
                idx = self._resolve(line[6:].strip())
                self.emit("PRINT", [idx])
                i += 1
            elif "=" in line and not any(op in line for op in ["==","!=","<=",">="]):
                lhs, rhs = line.split("=", 1)
                res = self._expr(rhs.strip())
                dst = self.var(lhs.strip())
                self.emit("LOAD", [res, dst])
                i += 1
            else:
                i += 1

    def _find_block(self, lines, header_i, end):
        """Return (block_lines_with_original_indent, next_i_after_block)."""
        if header_i + 1 >= end:
            return [], header_i + 1
        base_indent = self._get_indent(lines[header_i])
        block = []
        i = header_i + 1
        while i < end:
            raw = lines[i]
            stripped = raw.strip()
            if not stripped:
                i += 1; continue
            curr = self._get_indent(raw)
            if curr <= base_indent:
                break
            block.append(raw)
            i += 1
        return block, i

    def _compile_if(self, lines, i, end):
        line = lines[i].strip()
        cond_str = line[3:].rstrip(":").strip()
        cond_idx = self._condition(cond_str)
        jif_idx  = self.emit("JIF", [cond_idx], jump_to=0)

        then_block, next_i = self._find_block(lines, i, end)
        self._compile_block(then_block, 0, len(then_block))

        # peek for else
        if next_i < end and lines[next_i].strip() == "else:":
            jmp_idx = self.emit("JMP", jump_to=0)
            self.patch(jif_idx, self.here())
            else_block, next_i = self._find_block(lines, next_i, end)
            self._compile_block(else_block, 0, len(else_block))
            self.patch(jmp_idx, self.here())
        else:
            self.patch(jif_idx, self.here())

        return next_i

    def _compile_while(self, lines, i, end):
        line     = lines[i].strip()
        cond_str = line[6:].rstrip(":").strip()

        loop_top = self.here()
        cond_idx = self._condition(cond_str)
        jif_idx  = self.emit("JIF", [cond_idx], jump_to=0)

        body, next_i = self._find_block(lines, i, end)
        self._compile_block(body, 0, len(body))
        self.emit("JMP", jump_to=loop_top)
        self.patch(jif_idx, self.here())
        return next_i

    def _condition(self, s):
        for op_str, op_name in [("<=","CMP_LTE"),(">=","CMP_GTE"),
                                  ("==","CMP_EQ"),("!=","CMP_NEQ"),
                                  ("<","CMP_LT"),(">","CMP_GT")]:
            if op_str in s:
                l, r = s.split(op_str, 1)
                li, ri = self._resolve(l.strip()), self._resolve(r.strip())
                res = self.alloc(0)
                self.emit(op_name, [li, ri, res])
                return res
        return self._resolve(s.strip())

    def _expr(self, expr):
        for op_str, op_name in [("*","MUL"),("/","DIV"),("+","ADD"),("-","SUB")]:
            if op_str in expr:
                l, r = expr.split(op_str, 1)
                l, r = l.strip(), r.strip()
                if l and r:
                    li, ri = self._resolve(l), self._resolve(r)
                    res = self.alloc(0)
                    self.emit(op_name, [li, ri, res])
                    return res
        return self._resolve(expr)

    def _resolve(self, token):
        token = token.strip()
        if token.lstrip("-").isdigit():
            return self.alloc(int(token) & 0xFFFF)
        return self.var(token)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VIRTUAL MACHINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class VM:
    def __init__(self, max_steps=1_000_000):
        self.output    = []
        self.max_steps = max_steps

    def run(self, bitstream):
        ops, data = decode_bytecode(bitstream)
        data  = list(data)
        pc, steps = 0, 0

        while pc < len(ops):
            if steps > self.max_steps:
                print("  [VM: step limit]"); break
            steps += 1
            op, args = ops[pc]["op"], ops[pc]["args"]

            if   op=="ADD"     and len(args)>=3: data[args[2]]=(data[args[0]]+data[args[1]])&0xFFFF
            elif op=="SUB"     and len(args)>=3: data[args[2]]=(data[args[0]]-data[args[1]])&0xFFFF
            elif op=="MUL"     and len(args)>=3: data[args[2]]=(data[args[0]]*data[args[1]])&0xFFFF
            elif op=="DIV"     and len(args)>=3: data[args[2]]=(data[args[0]]//data[args[1]]) if data[args[1]] else 0
            elif op=="LOAD"    and len(args)>=2: data[args[1]]=data[args[0]]
            elif op=="PRINT"   and len(args)>=1: v=data[args[0]]; self.output.append(v); print(f"  → {v}")
            elif op=="CMP_LT"  and len(args)>=3: data[args[2]]=1 if data[args[0]]<data[args[1]]  else 0
            elif op=="CMP_GT"  and len(args)>=3: data[args[2]]=1 if data[args[0]]>data[args[1]]  else 0
            elif op=="CMP_EQ"  and len(args)>=3: data[args[2]]=1 if data[args[0]]==data[args[1]] else 0
            elif op=="CMP_NEQ" and len(args)>=3: data[args[2]]=1 if data[args[0]]!=data[args[1]] else 0
            elif op=="CMP_LTE" and len(args)>=3: data[args[2]]=1 if data[args[0]]<=data[args[1]] else 0
            elif op=="CMP_GTE" and len(args)>=3: data[args[2]]=1 if data[args[0]]>=data[args[1]] else 0
            elif op=="JMP":
                pc = ops[pc]["jump_to"]; continue
            elif op=="JIF":
                if data[args[0]]==0:
                    pc = ops[pc]["jump_to"]; continue
            pc += 1

        return data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST RUNNER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_program(title, source, expected=None):
    print(f"\n{'═'*60}")
    print(f"  {title}")
    print(f"{'═'*60}")
    for line in source.strip().splitlines():
        if line.strip(): print(f"    {line.rstrip()}")
    print()
    try:
        c  = Compiler()
        bc = c.compile(source)
        print(f"  Bytecode : {len(bc)} bits ({len(bc)//8} bytes)")
        print(f"  Instrs   : {len(c.instrs)}   Data slots: {len(c.data)}")
        print(f"  Output:")
        vm = VM()
        vm.run(bc)
        ok = (expected is None) or (vm.output == expected)
        print(f"\n  Expected : {expected}")
        print(f"  Got      : {vm.output}")
        print(f"  {'✓ PASS' if ok else '✗ FAIL'}")
        return ok
    except Exception as e:
        import traceback
        print(f"  ✗ ERROR: {e}")
        traceback.print_exc()
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST SUITE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TESTS = [
("Basic arithmetic","""
x = 3 + 5
print x
""",[8]),

("Chained ops","""
x = 3 + 5
y = x * 2
print y
""",[16]),

("Multiple variables","""
a = 10 + 5
b = a - 3
c = b * 4
print a
print b
print c
""",[15,12,48]),

("Division","""
x = 20 / 4
print x
""",[5]),

("Large numbers 16-bit","""
x = 1000 + 2000
print x
""",[3000]),

("If true branch","""
x = 10
if x > 5:
    print x
""",[10]),

("If false branch","""
x = 3
if x > 5:
    print x
""",[]),

("If-else true","""
x = 10
if x > 5:
    y = 1
else:
    y = 0
print y
""",[1]),

("If-else false","""
x = 2
if x > 5:
    y = 1
else:
    y = 0
print y
""",[0]),

("Equality check","""
x = 7
if x == 7:
    print x
""",[7]),

("Not equal","""
x = 5
if x != 3:
    print x
""",[5]),

("While — count up","""
i = 0
while i < 5:
    i = i + 1
print i
""",[5]),

("While — sum 1 to 10","""
i = 1
s = 0
while i <= 10:
    s = s + i
    i = i + 1
print s
""",[55]),

("While — countdown","""
x = 5
while x > 0:
    x = x - 1
print x
""",[0]),

("Factorial 5","""
n = 5
result = 1
while n > 0:
    result = result * n
    n = n - 1
print result
""",[120]),

("Fibonacci 10th","""
a = 0
b = 1
i = 0
while i < 9:
    c = a + b
    a = b
    b = c
    i = i + 1
print b
""",[55]),

("Max of two numbers","""
x = 17
y = 42
if x > y:
    max = x
else:
    max = y
print max
""",[42]),

("Absolute difference","""
a = 30
b = 50
if a > b:
    diff = a - b
else:
    diff = b - a
print diff
""",[20]),

("If inside while","""
i = 0
count = 0
while i < 10:
    i = i + 1
    if i == 5:
        count = count + 1
print count
""",[1]),

("Multiple prints in order","""
a = 1
b = 2
c = 3
print a
print b
print c
""",[1,2,3]),

("Nested arithmetic","""
x = 2 + 3
y = x * x
z = y - x
print z
""",[20]),

("Division remainder simulation","""
x = 17
y = 5
q = x / y
print q
""",[3]),

("Power of 2 loop","""
x = 1
i = 0
while i < 8:
    x = x * 2
    i = i + 1
print x
""",[256]),
]

if __name__ == "__main__":
    print("\n" + "█"*60)
    print("  ZC-LANG v2 — ZERO-COUNT BYTECODE SYSTEM")
    print("  Turing complete. No tables. Self-describing.")
    print("  Header stores grammar length → zero collision risk.")
    print("█"*60)

    results = [run_program(t,s,e) for t,s,e in TESTS]
    passed  = sum(results)
    total   = len(results)

    print(f"\n{'█'*60}")
    print(f"  {passed}/{total} tests passed {'✓ ALL PASS' if passed==total else ''}")
    print(f"{'█'*60}\n")