"""
ZC-LANG v3 — Zero-Count Encoding System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
v3 adds: functions with parameters and return values, MOD operator

Bugs fixed from first v3 attempt:
  1. Condition sides need _expr() not _resolve() — so 'n % 2 == 0' works
  2. Python marshal measurement was broken (nested function call)
  3. Fibonacci test expectations were off-by-one (loop semantics)
  4. Function local vars properly isolated via offset into data array
"""

import marshal

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PRIMITIVES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INSTR_CODES = {
    1:"ADD",2:"SUB",3:"MUL",4:"DIV",5:"LOAD",6:"PRINT",
    7:"CMP_LT",8:"CMP_GT",9:"CMP_EQ",10:"CMP_NEQ",11:"CMP_LTE",12:"CMP_GTE",
    13:"MOD",
}
INSTR_LOOKUP = {v:k for k,v in INSTR_CODES.items()}

def ztok(n):  return "0"*n+"1"
def uptr(n):  return "1"*(n+1)+"0"
def raw16(n): return format(n&0xFFFF,"016b")
def raw16i(b): return int(b,2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMPILER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Compiler:
    def __init__(self):
        self.data      = []
        self.instrs    = []
        self.funcs     = {}        # name → {params, body_lines, start_instr}
        self.vars      = {}        # current scope: name → data idx
        self._scope_stack = []
        self._func_ids    = {}     # func name → small int id

    def alloc(self, value=0):
        idx = len(self.data); self.data.append(int(value)&0xFFFF); return idx

    def var(self, name):
        if name not in self.vars: self.vars[name] = self.alloc(0)
        return self.vars[name]

    def emit(self, op, args=None, jump_to=None, extra=None):
        instr = {"op":op,"args":args or []}
        if jump_to is not None: instr["jump_to"] = jump_to
        if extra   is not None: instr["extra"]   = extra
        self.instrs.append(instr); return len(self.instrs)-1

    def patch(self, idx, jt): self.instrs[idx]["jump_to"] = jt
    def here(self): return len(self.instrs)

    def _func_id(self, name):
        if name not in self._func_ids:
            self._func_ids[name] = len(self._func_ids)+1
        return self._func_ids[name]

    # ── main compile ─────────────────────────────────────────
    def compile(self, source):
        lines = [l.rstrip() for l in source.strip().splitlines()]

        # collect function signatures first
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            if s.startswith("def "):
                name, params, body, next_i = self._parse_def(lines, i)
                self.funcs[name] = {"params":params,"body":body,"start_instr":-1}
                i = next_i
            else:
                i += 1

        # compile main (non-def lines)
        self._compile_block(lines, 0, len(lines))
        self.emit("HALT")

        # compile function bodies after main
        for fname, finfo in self.funcs.items():
            self._push_scope()
            for p in finfo["params"]:
                self.var(p)
            finfo["start_instr"] = self.here()
            param_slots = [self.vars[p] for p in finfo["params"]]
            self.emit("FUNC_ENTRY", extra={"name":fname,"param_slots":param_slots,
                                            "fid":self._func_id(fname)})
            self._compile_block(finfo["body"], 0, len(finfo["body"]))
            # implicit return 0
            r = self.alloc(0); self.emit("RETURN",[r])
            self._pop_scope()

        return self._encode()

    def _parse_def(self, lines, i):
        s      = lines[i].strip()
        head   = s[4:].rstrip(":")
        name   = head[:head.index("(")].strip()
        pstr   = head[head.index("(")+1:head.index(")")].strip()
        params = [p.strip() for p in pstr.split(",") if p.strip()]
        body, next_i = self._find_block(lines, i, len(lines))
        return name, params, body, next_i

    def _push_scope(self):
        self._scope_stack.append(self.vars.copy())
        self.vars = {}

    def _pop_scope(self):
        self.vars = self._scope_stack.pop()

    # ── block compilation ────────────────────────────────────
    def _compile_block(self, lines, start, end):
        i = start
        while i < end:
            raw = lines[i]; line = raw.strip()
            if not line or line.startswith("#"):
                i += 1; continue
            if line.startswith("def "):
                _, next_i = self._find_block(lines, i, end); i = next_i; continue
            if line.startswith("if "):
                i = self._compile_if(lines, i, end); continue
            if line.startswith("while "):
                i = self._compile_while(lines, i, end); continue
            if line.startswith("return "):
                self.emit("RETURN",[self._expr(line[7:].strip())]); i += 1; continue
            if line.startswith("print "):
                arg = line[6:].strip()
                # inline call like print fib(5)
                if "(" in arg:
                    idx = self._compile_call(arg)
                else:
                    idx = self._resolve(arg)
                self.emit("PRINT",[idx]); i += 1; continue
            if "=" in line and not any(op in line for op in ["==","!=","<=",">="]):
                lhs, rhs = line.split("=",1)
                res = self._expr(rhs.strip())
                self.emit("LOAD",[res, self.var(lhs.strip())])
                i += 1; continue
            i += 1

    def _find_block(self, lines, hi, end):
        if hi+1 >= end: return [], hi+1
        base = len(lines[hi]) - len(lines[hi].lstrip())
        block = []; i = hi+1
        while i < end:
            raw = lines[i]; s = raw.strip()
            if not s: i += 1; continue
            if len(raw)-len(raw.lstrip()) <= base: break
            block.append(raw); i += 1
        return block, i

    def _compile_if(self, lines, i, end):
        cond = self._condition(lines[i].strip()[3:].rstrip(":").strip())
        jif  = self.emit("JIF",[cond],jump_to=0)
        tb, ni = self._find_block(lines, i, end)
        self._compile_block(tb, 0, len(tb))
        if ni < end and lines[ni].strip() == "else:":
            jmp = self.emit("JMP",jump_to=0)
            self.patch(jif, self.here())
            eb, ni = self._find_block(lines, ni, end)
            self._compile_block(eb, 0, len(eb))
            self.patch(jmp, self.here())
        else:
            self.patch(jif, self.here())
        return ni

    def _compile_while(self, lines, i, end):
        top  = self.here()
        cond = self._condition(lines[i].strip()[6:].rstrip(":").strip())
        jif  = self.emit("JIF",[cond],jump_to=0)
        body, ni = self._find_block(lines, i, end)
        self._compile_block(body, 0, len(body))
        self.emit("JMP",jump_to=top)
        self.patch(jif, self.here())
        return ni

    # ── expressions ──────────────────────────────────────────
    def _condition(self, s):
        """Compile condition expression. Both sides go through _expr."""
        for op_str, op_name in [("<=","CMP_LTE"),(">=","CMP_GTE"),
                                  ("==","CMP_EQ"),("!=","CMP_NEQ"),
                                  ("<","CMP_LT"),(">","CMP_GT")]:
            if op_str in s:
                l, r = s.split(op_str, 1)
                li = self._expr(l.strip())   # ← _expr not _resolve (the key fix)
                ri = self._expr(r.strip())
                res = self.alloc(0)
                self.emit(op_name,[li,ri,res])
                return res
        return self._expr(s.strip())

    def _expr(self, expr):
        expr = expr.strip()
        if not expr: return self.alloc(0)
        # function call
        if "(" in expr and not expr.startswith("-"):
            return self._compile_call(expr)
        # binary ops — check longer ops first to avoid splitting on sub-ops
        for op_str, op_name in [("*","MUL"),("/","DIV"),("%","MOD"),("+","ADD"),("-","SUB")]:
            if op_str in expr:
                # avoid splitting negative literals like -1
                idx = expr.index(op_str)
                if op_str == "-" and idx == 0: continue
                l, r = expr[:idx].strip(), expr[idx+len(op_str):].strip()
                if l and r:
                    li, ri = self._resolve(l), self._resolve(r)
                    res = self.alloc(0)
                    self.emit(op_name,[li,ri,res]); return res
        return self._resolve(expr)

    def _resolve(self, token):
        token = token.strip()
        if token.lstrip("-").isdigit():
            return self.alloc(int(token)&0xFFFF)
        if "(" in token:
            return self._compile_call(token)
        return self.var(token)

    def _compile_call(self, expr):
        expr     = expr.strip()
        name     = expr[:expr.index("(")].strip()
        args_str = expr[expr.index("(")+1:expr.rindex(")")].strip()
        arg_idxs = [self._expr(a.strip()) for a in args_str.split(",") if a.strip()]
        res      = self.alloc(0)
        self.emit("CALL", args=arg_idxs,
                  extra={"fid":self._func_id(name),"ret_slot":res})
        return res

    # ── encoder ──────────────────────────────────────────────
    def _encode(self):
        g = [ztok(7),ztok(5)]
        for instr in self.instrs:
            op   = instr["op"]
            args = instr.get("args",[])
            extra= instr.get("extra",{})
            g += [ztok(4),ztok(3)]
            if   op=="JMP":   g += [ztok(8), uptr(instr["jump_to"])]
            elif op=="JIF":   g += [ztok(6), uptr(args[0]), uptr(instr["jump_to"])]
            elif op=="HALT":  g += [ztok(2), ztok(15)]
            elif op=="RETURN":g += [ztok(11), uptr(args[0]) if args else uptr(0)]
            elif op=="FUNC_ENTRY":
                g += [ztok(9), uptr(extra["fid"]), uptr(len(extra["param_slots"]))]
                for ps in extra["param_slots"]: g += [ztok(1),uptr(ps)]
            elif op=="CALL":
                g += [ztok(10), uptr(extra["fid"]), uptr(extra["ret_slot"])]
                for a in args: g += [ztok(1),uptr(a)]
            else:
                oc = INSTR_LOOKUP.get(op)
                if oc:
                    g += [ztok(2),ztok(oc)]
                    for a in args: g += [ztok(1),uptr(a)]
            g += [ztok(3),ztok(4)]
        g += [ztok(5),ztok(7)]
        grammar = "".join(g)
        header  = raw16(len(grammar))
        return header + grammar + "".join(raw16(v) for v in self.data)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DECODER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def read_uptr(bits, pos):
    count = 0
    while pos < len(bits) and bits[pos] == "1": count += 1; pos += 1
    if pos < len(bits) and bits[pos] == "0": pos += 1
    return count-1, pos

def _tog(ctx, t):
    if t not in ctx: ctx.append(t)
    else: ctx.remove(t)

def decode(bs):
    gl  = raw16i(bs[:16])
    g   = bs[16:16+gl]
    dr  = bs[16+gl:]
    dv  = [raw16i(dr[i:i+16]) for i in range(0,len(dr),16) if len(dr[i:i+16])==16]
    pos,ops,cur,ctx = 0,[],None,[]
    while pos < len(g):
        zc = 0
        while pos<len(g) and g[pos]=="0": zc+=1; pos+=1
        if pos<len(g) and g[pos]=="1": pos+=1
        if zc==0: continue
        if   zc==7: _tog(ctx,"doc")
        elif zc==5: _tog(ctx,"sec")
        elif zc==4:
            if "blk" not in ctx: ctx.append("blk")
            else:
                ctx.remove("blk")
                if cur: ops.append(cur); cur=None
        elif zc==3: _tog(ctx,"unt")
        elif zc==8: t,pos=read_uptr(g,pos); cur={"op":"JMP","args":[],"jump_to":t}
        elif zc==6:
            c,pos=read_uptr(g,pos); t,pos=read_uptr(g,pos)
            cur={"op":"JIF","args":[c],"jump_to":t}
        elif zc==11:
            v,pos=read_uptr(g,pos); cur={"op":"RETURN","args":[v]}
        elif zc==10:
            fid,pos=read_uptr(g,pos); rs,pos=read_uptr(g,pos)
            cur={"op":"CALL","args":[],"extra":{"fid":fid,"ret_slot":rs}}
        elif zc==9:
            fid,pos=read_uptr(g,pos); ac,pos=read_uptr(g,pos)
            cur={"op":"FUNC_ENTRY","args":[],"extra":{"fid":fid,"param_slots":[]}}
        elif zc==2:
            izc=0
            while pos<len(g) and g[pos]=="0": izc+=1; pos+=1
            if pos<len(g) and g[pos]=="1": pos+=1
            cur={"op":INSTR_CODES.get(izc,f"UNK{izc}"),"args":[]}
        elif zc==1:
            p,pos=read_uptr(g,pos)
            if cur:
                if cur["op"]=="FUNC_ENTRY": cur["extra"]["param_slots"].append(p)
                else: cur["args"].append(p)
    return ops, dv


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VM
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class VM:
    def __init__(self, max_steps=10_000_000):
        self.output = []; self.max_steps = max_steps

    def run(self, bs):
        ops, data = decode(bs)
        data = list(data)

        # build function table: fid → instr index of FUNC_ENTRY
        ftable = {}
        for i,op in enumerate(ops):
            if op["op"]=="FUNC_ENTRY":
                ftable[op["extra"]["fid"]] = i

        pc, steps, cstack = 0, 0, []

        while pc < len(ops) and steps < self.max_steps:
            steps += 1
            op = ops[pc]; name = op["op"]; args = op["args"]

            if   name=="ADD"     and len(args)>=3: data[args[2]]=(data[args[0]]+data[args[1]])&0xFFFF
            elif name=="SUB"     and len(args)>=3: data[args[2]]=(data[args[0]]-data[args[1]])&0xFFFF
            elif name=="MUL"     and len(args)>=3: data[args[2]]=(data[args[0]]*data[args[1]])&0xFFFF
            elif name=="DIV"     and len(args)>=3: data[args[2]]=(data[args[0]]//data[args[1]]) if data[args[1]] else 0
            elif name=="MOD"     and len(args)>=3: data[args[2]]=(data[args[0]]% data[args[1]]) if data[args[1]] else 0
            elif name=="LOAD"    and len(args)>=2: data[args[1]]=data[args[0]]
            elif name=="PRINT"   and len(args)>=1:
                v=data[args[0]]; self.output.append(v); print(f"  → {v}")
            elif name=="CMP_LT"  and len(args)>=3: data[args[2]]=1 if data[args[0]]< data[args[1]] else 0
            elif name=="CMP_GT"  and len(args)>=3: data[args[2]]=1 if data[args[0]]> data[args[1]] else 0
            elif name=="CMP_EQ"  and len(args)>=3: data[args[2]]=1 if data[args[0]]==data[args[1]] else 0
            elif name=="CMP_NEQ" and len(args)>=3: data[args[2]]=1 if data[args[0]]!=data[args[1]] else 0
            elif name=="CMP_LTE" and len(args)>=3: data[args[2]]=1 if data[args[0]]<=data[args[1]] else 0
            elif name=="CMP_GTE" and len(args)>=3: data[args[2]]=1 if data[args[0]]>=data[args[1]] else 0
            elif name=="JMP":  pc=op["jump_to"]; continue
            elif name=="JIF":
                if data[args[0]]==0: pc=op["jump_to"]; continue
            elif name=="HALT": break
            elif name=="FUNC_ENTRY":
                # reached in normal flow — skip over body to after matching RETURN
                d=1; sk=pc+1
                while sk<len(ops) and d>0:
                    if ops[sk]["op"]=="FUNC_ENTRY": d+=1
                    if ops[sk]["op"]=="RETURN":     d-=1
                    sk+=1
                pc=sk; continue
            elif name=="CALL":
                ex=op.get("extra",{}); fid=ex.get("fid",0); rs=ex.get("ret_slot",0)
                if fid not in ftable: pc+=1; continue
                fpc    = ftable[fid]
                pslots = ops[fpc]["extra"]["param_slots"]
                for i,a in enumerate(args):
                    if i<len(pslots): data[pslots[i]]=data[a]
                cstack.append((pc+1, rs))
                pc = fpc+1; continue
            elif name=="RETURN":
                rv = data[args[0]] if args else 0
                if cstack:
                    rpc, rs = cstack.pop()
                    data[rs] = rv; pc=rpc; continue
                else: break

            pc += 1

        if steps >= self.max_steps: print("  [VM: step limit]")
        return data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SIZE MEASUREMENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def py_bytecode_size(source):
    """Pure bytecode bytes only (co_code), recursively for nested functions."""
    code = compile(source, "<string>", "exec")
    def total_bytecode(c):
        total = len(c.co_code)
        for const in c.co_consts:
            if hasattr(const, "co_code"):
                total += total_bytecode(const)
        return total
    return total_bytecode(code)

def py_marshal_size(source):
    code = compile(source, "<string>", "exec")
    return len(marshal.dumps(code))

def zce_size(source):
    c = Compiler()
    bc = c.compile(source)
    return len(bc)//8, len(bc), len(c.instrs), len(c.data)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST RUNNER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_test(title, source, expected=None):
    print(f"\n{'═'*64}")
    print(f"  {title}")
    print(f"{'═'*64}")
    for l in source.strip().splitlines():
        if l.strip(): print(f"    {l.rstrip()}")
    print()
    try:
        c  = Compiler()
        bc = c.compile(source)
        print(f"  Bytecode : {len(bc)} bits ({len(bc)//8} bytes)  "
              f"Instrs:{len(c.instrs)}  Data:{len(c.data)}")
        print(f"  Output:")
        vm = VM(); vm.run(bc)
        ok = (expected is None) or (vm.output == expected)
        print(f"\n  Expected : {expected}")
        print(f"  Got      : {vm.output}")
        print(f"  {'✓ PASS' if ok else '✗ FAIL'}")
        return ok
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ✗ ERROR: {e}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PROGRAMS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PROGS = {

"factorial": ("""
def factorial(n):
    result = 1
    while n > 0:
        result = result * n
        n = n - 1
    return result

print factorial(0)
print factorial(1)
print factorial(5)
print factorial(7)
""", [1, 1, 120, 5040]),

"gcd": ("""
def gcd(a, b):
    while b != 0:
        temp = b
        b = a % b
        a = temp
    return a

print gcd(48, 18)
print gcd(100, 75)
print gcd(17, 5)
print gcd(0, 5)
""", [6, 25, 1, 5]),

"max_min": ("""
def max(a, b):
    if a > b:
        return a
    return b

def min(a, b):
    if a < b:
        return a
    return b

print max(10, 20)
print min(10, 20)
print max(99, 3)
print min(0, 100)
""", [20, 10, 99, 0]),

"square_sum": ("""
def square(n):
    return n * n

i = 1
total = 0
while i <= 5:
    sq = square(i)
    total = total + sq
    i = i + 1
print total
""", [55]),

"fibonacci": ("""
def fib(n):
    a = 0
    b = 1
    i = 0
    while i < n:
        c = a + b
        a = b
        b = c
        i = i + 1
    return a

print fib(0)
print fib(1)
print fib(6)
print fib(10)
""", [0, 1, 8, 55]),

"collatz": ("""
def collatz(n):
    steps = 0
    while n != 1:
        r = n % 2
        if r == 0:
            n = n / 2
        else:
            n = n * 3
            n = n + 1
        steps = steps + 1
    return steps

print collatz(1)
print collatz(6)
print collatz(27)
""", [0, 8, 111]),

"bubble_sort": ("""
a = 64
b = 34
c = 25
d = 12
e = 22

i = 0
while i < 4:
    if a > b:
        t=a
        a=b
        b=t
    if b > c:
        t=b
        b=c
        c=t
    if c > d:
        t=c
        c=d
        d=t
    if d > e:
        t=d
        d=e
        e=t
    i = i + 1

print a
print b
print c
print d
print e
""", [12, 22, 25, 34, 64]),

"primes": ("""
n = 2
while n <= 30:
    i = 2
    is_prime = 1
    while i < n:
        r = n % i
        if r == 0:
            is_prime = 0
        i = i + 1
    if is_prime == 1:
        print n
    n = n + 1
""", [2,3,5,7,11,13,17,19,23,29]),

"power": ("""
def power(base, exp):
    result = 1
    while exp > 0:
        result = result * base
        exp = exp - 1
    return result

print power(2, 8)
print power(3, 4)
print power(10, 3)
""", [256, 81, 1000]),

"is_even_odd": ("""
def is_even(n):
    r = n % 2
    if r == 0:
        return 1
    return 0

print is_even(4)
print is_even(7)
print is_even(0)
print is_even(100)
""", [1, 0, 1, 1]),

}

SIZE_PROGRAMS = ["factorial","gcd","primes","fibonacci","bubble_sort","collatz","power"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    print("\n"+"█"*64)
    print("  ZC-LANG v3 — FUNCTIONS + REAL PROGRAMS + SIZE COMPARISON")
    print("█"*64)

    # ── tests ──────────────────────────────────────────────
    print("\n"+"▓"*64)
    print("  SECTION 1 — CORRECTNESS TESTS")
    print("▓"*64)

    results = []
    for name, (src, exp) in PROGS.items():
        ok = run_test(name, src, exp)
        results.append(ok)

    # ── size comparison ────────────────────────────────────
    print("\n\n"+"▓"*64)
    print("  SECTION 2 — SIZE COMPARISON: ZCE vs PYTHON BYTECODE")
    print("▓"*64)
    print(f"\n  {'Program':<28} {'ZCE bytes':>10} {'Py bytecode':>12} {'Marshal':>10} {'ZCE/Py':>8}")
    print(f"  {'─'*28} {'─'*10} {'─'*12} {'─'*10} {'─'*8}")

    total_zce = total_py = total_marshal = 0
    for name in SIZE_PROGRAMS:
        src, _ = PROGS[name]
        zb, _, ni, ds = zce_size(src)
        pb   = py_bytecode_size(src)
        mb   = py_marshal_size(src)
        ratio= f"{zb/pb:.2f}x" if pb else "n/a"
        print(f"  {name:<28} {zb:>10} {pb:>12} {mb:>10} {ratio:>8}")
        total_zce += zb; total_py += pb; total_marshal += mb

    print(f"  {'─'*28} {'─'*10} {'─'*12} {'─'*10} {'─'*8}")
    ratio = f"{total_zce/total_py:.2f}x" if total_py else "n/a"
    print(f"  {'TOTAL':<28} {total_zce:>10} {total_py:>12} {total_marshal:>10} {ratio:>8}")
    print(f"""
  Columns:
    ZCE bytes    = full ZCE bitstream size in bytes
    Py bytecode  = sum of co_code bytes (pure opcodes only, no metadata)
    Marshal      = full Python .pyc object with all metadata
    ZCE/Py       = ratio of ZCE to Python pure bytecode

  Context:
    Python bytecode is heavily optimized over 30+ years.
    ZCE is unoptimized v1 — no constant folding, no dead code elimination.
    ZCE grammar overhead is the price of self-description.
    Python marshal includes names/lnotab/constants on top.
    At scale, ZCE overhead would shrink as a fraction of total size.
""")

    # ── summary ────────────────────────────────────────────
    passed = sum(results); total = len(results)
    print("▓"*64)
    print(f"  {passed}/{total} tests passed {'✓ ALL PASS' if passed==total else ''}")
    print("▓"*64+"\n")
