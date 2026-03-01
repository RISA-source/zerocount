"""
ZC-LANG — A tiny language that compiles to Zero-Count Bytecode
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You write:
    x = 3 + 5
    y = x * 2
    z = y - 4

Compiler turns it into zero-count bytecode.
VM runs it. Done.

Zero-Count Bytecode grammar tokens (N zeros + 1):
  N=1  value-ref  → unary pointer (K ones + 0 = data[K])
  N=2  instruction
  N=3  unit open/close
  N=4  block open/close
  N=5  section open/close
  N=7  document open/close

Instruction codes:
  N=1 ADD, N=2 SUB, N=3 MUL, N=4 STORE, N=5 LOAD, N=6 PRINT
"""

import re

# ── BYTECODE LAYER (from before, cleaned up) ──────────────────────────────────

INSTR_CODES  = {1:"ADD",2:"SUB",3:"MUL",4:"STORE",5:"LOAD",6:"PRINT"}
INSTR_LOOKUP = {v:k for k,v in INSTR_CODES.items()}
SEPARATOR    = "00000000"

def ztok(n):  return "0"*n+"1"
def uptr(n):  return "1"*(n+1)+"0"
def raw8(n):  return format(n&0xFF,"08b")

def encode_bytecode(instructions, data_values):
    g = [ztok(7), ztok(5)]
    for instr in instructions:
        op   = instr["op"]
        args = instr["args"]
        g += [ztok(4), ztok(3)]
        g += [ztok(2), ztok(INSTR_LOOKUP[op])]
        for a in args:
            g += [ztok(1), uptr(a)]
        g += [ztok(3), ztok(4)]
    g += [ztok(5), ztok(7)]
    grammar = "".join(g)
    assert SEPARATOR not in grammar
    return grammar + SEPARATOR + "".join(raw8(v) for v in data_values)

def decode_bytecode(bitstream):
    sep         = bitstream.index(SEPARATOR)
    grammar     = bitstream[:sep]
    data_raw    = bitstream[sep+8:]
    data_values = [int(data_raw[i:i+8],2)
                   for i in range(0,len(data_raw),8)
                   if len(data_raw[i:i+8])==8]
    pos,ops,cur,ctx = 0,[],None,[]
    while pos < len(grammar):
        zc = 0
        while pos<len(grammar) and grammar[pos]=="0": zc+=1; pos+=1
        if pos<len(grammar) and grammar[pos]=="1": pos+=1
        if zc==0: continue
        if zc==7:
            tag="doc"
            if tag not in ctx: ctx.append(tag)
            else: ctx.remove(tag)
        elif zc==5:
            tag="sec"
            if tag not in ctx: ctx.append(tag)
            else: ctx.remove(tag)
        elif zc==4:
            tag="blk"
            if tag not in ctx: ctx.append(tag)
            else:
                ctx.remove(tag)
                if cur: ops.append(cur); cur=None
        elif zc==3:
            tag="unt"
            if tag not in ctx: ctx.append(tag)
            else: ctx.remove(tag)
        elif zc==2:
            izc=0
            while pos<len(grammar) and grammar[pos]=="0": izc+=1; pos+=1
            if pos<len(grammar) and grammar[pos]=="1": pos+=1
            cur={"op":INSTR_CODES.get(izc,f"UNK{izc}"),"args":[],"vals":[]}
        elif zc==1:
            ptr=0
            while pos<len(grammar) and grammar[pos]=="1": ptr+=1; pos+=1
            if pos<len(grammar) and grammar[pos]=="0": pos+=1
            ptr-=1
            val=data_values[ptr] if 0<=ptr<len(data_values) else None
            if cur: cur["args"].append(ptr); cur["vals"].append(val)
    return ops, data_values

# ── COMPILER ──────────────────────────────────────────────────────────────────

class Compiler:
    def __init__(self):
        self.data    = []   # data section values
        self.vars    = {}   # var_name → data index
        self.instrs  = []   # instruction list

    def alloc(self, value):
        """Store a value in data section, return its index."""
        idx = len(self.data)
        self.data.append(value & 0xFF)
        return idx

    def get_or_alloc_var(self, name):
        if name not in self.vars:
            self.vars[name] = self.alloc(0)  # allocate slot
        return self.vars[name]

    def compile(self, source):
        lines = [l.strip() for l in source.strip().splitlines()]
        lines = [l for l in lines if l and not l.startswith("#")]

        for line in lines:
            self._compile_line(line)

        return encode_bytecode(self.instrs, self.data)

    def _compile_line(self, line):
        # print statement:  print x  or  print 42
        if line.startswith("print "):
            arg = line[6:].strip()
            idx = self._resolve_value(arg)
            self.instrs.append({"op":"PRINT","args":[idx]})
            return

        # assignment:  x = expr
        if "=" in line:
            lhs, rhs = line.split("=", 1)
            lhs = lhs.strip(); rhs = rhs.strip()
            result_idx = self._compile_expr(rhs)
            # STORE result into variable slot
            var_idx = self.get_or_alloc_var(lhs)
            # copy: store the result index as the var's value via LOAD
            self.instrs.append({"op":"LOAD","args":[result_idx, var_idx]})
            return

        raise SyntaxError(f"Cannot parse: {line}")

    def _compile_expr(self, expr):
        """Compile an expression, return data index of result."""
        expr = expr.strip()

        # binary ops
        for op_str, op_name in [("*","MUL"),("/","DIV"),("+","ADD"),("-","SUB")]:
            if op_str in expr:
                parts = expr.split(op_str, 1)
                if len(parts) == 2:
                    lhs = parts[0].strip(); rhs = parts[1].strip()
                    if lhs and rhs:
                        l_idx = self._resolve_value(lhs)
                        r_idx = self._resolve_value(rhs)
                        res_idx = self.alloc(0)  # result slot
                        self.instrs.append({"op":op_name,"args":[l_idx, r_idx, res_idx]})
                        return res_idx

        # single value
        return self._resolve_value(expr)

    def _resolve_value(self, token):
        token = token.strip()
        if token.lstrip("-").isdigit():
            return self.alloc(int(token) & 0xFF)
        else:
            return self.get_or_alloc_var(token)

# ── VM ────────────────────────────────────────────────────────────────────────

class VM:
    def __init__(self):
        self.output = []

    def run(self, bitstream):
        ops, data = decode_bytecode(bitstream)
        # data is a mutable list
        data = list(data)

        for op in ops:
            name = op["op"]
            args = op["args"]

            if name == "ADD" and len(args) >= 3:
                data[args[2]] = data[args[0]] + data[args[1]]
            elif name == "SUB" and len(args) >= 3:
                data[args[2]] = data[args[0]] - data[args[1]]
            elif name == "MUL" and len(args) >= 3:
                data[args[2]] = data[args[0]] * data[args[1]]
            elif name == "LOAD" and len(args) >= 2:
                data[args[1]] = data[args[0]]
            elif name == "PRINT" and len(args) >= 1:
                val = data[args[0]]
                self.output.append(val)
                print(f"  → {val}")
            else:
                pass  # unimplemented, skip

        return data

# ── TEST PROGRAMS ─────────────────────────────────────────────────────────────

def run_program(title, source, expected_output=None):
    print(f"\n{'═'*56}")
    print(f"  PROGRAM: {title}")
    print(f"{'═'*56}")
    print("  Source:")
    for line in source.strip().splitlines():
        print(f"    {line.strip()}")
    print()

    compiler = Compiler()
    bytecode = compiler.compile(source)

    print(f"  Bytecode  : {bytecode[:60]}{'...' if len(bytecode)>60 else ''}")
    print(f"  Size      : {len(bytecode)} bits  ({len(bytecode)//8} bytes)")
    print(f"  Data slots: {len(compiler.data)}  {compiler.data}")
    print(f"  Variables : {compiler.vars}")
    print(f"\n  Output:")

    vm = VM()
    vm.run(bytecode)

    if expected_output is not None:
        ok = vm.output == expected_output
        print(f"\n  Expected  : {expected_output}")
        print(f"  Got       : {vm.output}")
        print(f"  {'✓ PASS' if ok else '✗ FAIL'}")
        return ok
    return True

# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "█"*56)
    print("  ZC-LANG → ZERO-COUNT BYTECODE COMPILER + VM")
    print("  You write code. It compiles. It runs. No tables.")
    print("█"*56)

    results = []

    results.append(run_program(
        "Hello arithmetic",
        """
        x = 3 + 5
        print x
        """,
        expected_output=[8]
    ))

    results.append(run_program(
        "Chain of operations",
        """
        x = 3 + 5
        y = x * 2
        print y
        """,
        expected_output=[16]
    ))

    results.append(run_program(
        "Multiple variables",
        """
        a = 10 + 5
        b = a - 3
        c = b * 4
        print a
        print b
        print c
        """,
        expected_output=[15, 12, 48]
    ))

    results.append(run_program(
        "Just numbers",
        """
        x = 7 * 6
        print x
        """,
        expected_output=[42]
    ))

    passed = sum(results)
    total  = len(results)
    print(f"\n{'█'*56}")
    print(f"  {passed}/{total} programs passed {'✓' if passed==total else '✗'}")
    print(f"{'█'*56}\n")