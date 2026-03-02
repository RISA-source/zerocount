"""
ZC-LANG v4 — Zero-Count Encoding System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Changes from v3:
  1. Elias gamma coding for data section values (variable-length, ~2x smaller)
  2. Arrays: array(n), arr[i]=val, x=arr[i], len(arr)

BITSTREAM FORMAT:
  [16-bit grammar length] [16-bit data count] [grammar bits] [elias-coded data]

ELIAS GAMMA CODING (for data values):
  Encode value V ≥ 0 as Elias(V+1):
    k = floor(log2(V+1))
    emit k zeros, then V+1 in binary (k+1 bits)
  Examples:  0→'1'  1→'010'  2→'011'  3→'00100'  7→'00111'  15→'0001111'
  Self-delimiting: decoder counts leading zeros to know how many bits to read.

GRAMMAR TOKENS (N zeros + terminating 1) — unchanged from v3:
  N=1  value ref   N=2  instruction  N=3  unit    N=4  block
  N=5  section     N=6  JIF          N=7  document N=8  JMP
  N=10 CALL        N=11 RET          N=12 FUNC

INSTRUCTION CODES (N after N=2 token):
  N=1  ADD    N=2  SUB    N=3  MUL    N=4  DIV
  N=5  LOAD   N=6  PRINT  N=7  CMP_LT N=8  CMP_GT
  N=9  CMP_EQ N=10 CMP_NEQ N=11 CMP_LTE N=12 CMP_GTE
  N=13 MOD
  N=14 ARR_NEW  data[result] = new array of data[size] elements
  N=15 ARR_GET  data[result] = arrays[data[arr_ref]][data[idx]]
  N=16 ARR_SET  arrays[data[arr_ref]][data[idx]] = data[val]
  N=17 ARR_LEN  data[result] = len(arrays[data[arr_ref]])
"""

import math, marshal

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ELIAS GAMMA CODING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def elias_encode(v):
    """Encode non-negative integer v. Stores v+1 in elias gamma."""
    n = v + 1  # shift so 0 is encodable
    k = int(math.log2(n))
    return '0' * k + format(n, f'0{k+1}b')

def elias_decode(bits, pos):
    """Decode one Elias-coded value at pos. Returns (value, new_pos)."""
    k = 0
    while pos < len(bits) and bits[pos] == '0':
        k += 1; pos += 1
    n = int(bits[pos:pos+k+1], 2)
    return n - 1, pos + k + 1  # shift back

def elias_bits(v):
    """How many bits does Elias(v) take?"""
    n = v + 1
    k = int(math.log2(n)) if n > 0 else 0
    return 2 * k + 1

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GRAMMAR PRIMITIVES (unchanged)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INSTR_CODES = {
    1:"ADD",  2:"SUB",    3:"MUL",    4:"DIV",
    5:"LOAD", 6:"PRINT",
    7:"CMP_LT", 8:"CMP_GT", 9:"CMP_EQ",
    10:"CMP_NEQ", 11:"CMP_LTE", 12:"CMP_GTE",
    13:"MOD",
    14:"ARR_NEW", 15:"ARR_GET", 16:"ARR_SET", 17:"ARR_LEN",
}
INSTR_LOOKUP = {v:k for k,v in INSTR_CODES.items()}

def ztok(n):  return "0"*n + "1"
def uptr(n):  return "1"*(n+1) + "0"
def raw16(n): return format(n & 0xFFFF, "016b")
def r16(b):   return int(b, 2)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENCODER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def encode_bytecode(instructions, data_values):
    g = [ztok(7), ztok(5)]
    for instr in instructions:
        op   = instr["op"]
        args = instr.get("args", [])
        g   += [ztok(4), ztok(3)]
        if op == "JMP":
            g += [ztok(8), uptr(instr["jump_to"])]
        elif op == "JIF":
            g += [ztok(6), uptr(args[0]), uptr(instr["jump_to"])]
        elif op == "FUNC":
            g += [ztok(12), uptr(args[0]), uptr(args[1])]
            for ps in args[2:]: g += [uptr(ps)]
        elif op == "CALL":
            g += [ztok(10), uptr(args[0]), uptr(args[1]), uptr(args[2])]
            for a in args[3:]: g += [uptr(a)]
        elif op == "RET":
            g += [ztok(11), uptr(args[0])]
        else:
            g += [ztok(2), ztok(INSTR_LOOKUP[op])]
            for a in args: g += [ztok(1), uptr(a)]
        g += [ztok(3), ztok(4)]
    g += [ztok(5), ztok(7)]

    grammar  = "".join(g)
    # Elias-code all data values
    data_enc = "".join(elias_encode(v) for v in data_values)
    # Header: grammar length (16 bits) + data count (16 bits)
    header   = raw16(len(grammar)) + raw16(len(data_values))
    return header + grammar + data_enc

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DECODER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def read_uptr(bits, pos):
    c = 0
    while pos < len(bits) and bits[pos] == "1": c += 1; pos += 1
    if pos < len(bits) and bits[pos] == "0": pos += 1
    return c - 1, pos

def decode_bytecode(bs):
    gl   = r16(bs[:16])          # grammar length in bits
    dc   = r16(bs[16:32])        # data count
    gram = bs[32:32+gl]
    draw = bs[32+gl:]

    # decode Elias-coded data values
    pos2 = 0
    data = []
    for _ in range(dc):
        v, pos2 = elias_decode(draw, pos2)
        data.append(v)

    pos, ops, cur, ctx = 0, [], None, []
    while pos < len(gram):
        zc = 0
        while pos < len(gram) and gram[pos] == "0": zc += 1; pos += 1
        if pos < len(gram) and gram[pos] == "1":    pos += 1
        if zc == 0: continue

        if   zc == 7: t="doc";  (ctx.append(t) if t not in ctx else ctx.remove(t))
        elif zc == 5: t="sec";  (ctx.append(t) if t not in ctx else ctx.remove(t))
        elif zc == 4:
            t = "blk"
            if t not in ctx: ctx.append(t)
            else:
                ctx.remove(t)
                if cur: ops.append(cur); cur = None
        elif zc == 3: t="unt"; (ctx.append(t) if t not in ctx else ctx.remove(t))
        elif zc == 8:
            tgt,pos = read_uptr(gram,pos)
            cur = {"op":"JMP","args":[],"jump_to":tgt}
        elif zc == 6:
            cd,pos  = read_uptr(gram,pos)
            tgt,pos = read_uptr(gram,pos)
            cur = {"op":"JIF","args":[cd],"jump_to":tgt}
        elif zc == 12:
            fid,pos = read_uptr(gram,pos)
            npr,pos = read_uptr(gram,pos)
            pslots  = []
            for _ in range(npr):
                s,pos = read_uptr(gram,pos)
                pslots.append(s)
            cur = {"op":"FUNC","args":[fid,npr]+pslots}
        elif zc == 10:
            fid,pos  = read_uptr(gram,pos)
            na,pos   = read_uptr(gram,pos)
            res,pos  = read_uptr(gram,pos)
            cargs    = []
            for _ in range(na):
                a,pos = read_uptr(gram,pos); cargs.append(a)
            cur = {"op":"CALL","args":[fid,na,res]+cargs}
        elif zc == 11:
            rv,pos = read_uptr(gram,pos)
            cur = {"op":"RET","args":[rv]}
        elif zc == 2:
            ic = 0
            while pos < len(gram) and gram[pos] == "0": ic += 1; pos += 1
            if pos < len(gram) and gram[pos] == "1": pos += 1
            cur = {"op":INSTR_CODES.get(ic, f"UNK{ic}"), "args":[]}
        elif zc == 1:
            p,pos = read_uptr(gram,pos)
            if cur: cur["args"].append(p)
    return ops, data

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMPILER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Compiler:
    def __init__(self):
        self.data     = []
        self.globals  = {}
        self.instrs   = []
        self.funcs    = {}
        self._fid     = 0

    def alloc(self, v=0):
        i = len(self.data); self.data.append(int(v) & 0xFFFF); return i

    def gvar(self, name):
        if name not in self.globals: self.globals[name] = self.alloc(0)
        return self.globals[name]

    def emit(self, op, args=None, jump_to=None):
        i = {"op":op, "args":args or []}
        if jump_to is not None: i["jump_to"] = jump_to
        self.instrs.append(i); return len(self.instrs) - 1

    def patch(self, idx, tgt): self.instrs[idx]["jump_to"] = tgt
    def here(self): return len(self.instrs)

    # ── compile entry ─────────────────────────────────────────
    def compile(self, source):
        lines = [l.rstrip() for l in source.splitlines()]
        # pass 1: register + compile all functions
        i = 0
        while i < len(lines):
            if lines[i].strip().startswith("def "): i = self._func(lines,i,len(lines))
            else: i += 1
        # pass 2: top-level code
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            if not s or s.startswith("#"): i+=1; continue
            if s.startswith("def "):
                _, i = self._find_block(lines,i,len(lines)); continue
            i = self._stmt(lines, i, len(lines), self.globals)
        return encode_bytecode(self.instrs, self.data)

    # ── function compilation ──────────────────────────────────
    def _func(self, lines, i, end):
        s      = lines[i].strip()[4:].rstrip(":")
        fname  = s[:s.index("(")].strip()
        params = [p.strip() for p in s[s.index("(")+1:s.index(")")].split(",") if p.strip()]
        fid    = self._fid; self._fid += 1
        pslots = [self.alloc(0) for _ in params]
        local  = {params[j]: pslots[j] for j in range(len(params))}

        jmp_over = self.emit("JMP", jump_to=0)
        entry    = self.here()
        self.emit("FUNC", [fid, len(params)] + pslots)
        self.funcs[fname] = {"id":fid, "params":params, "param_slots":pslots,
                              "entry":entry, "local_slots":set(pslots)}

        body, ni = self._find_block(lines, i, end)
        db = len(self.data)
        self._block(body, 0, len(body), local)
        self.funcs[fname]["local_slots"] = set(pslots) | set(range(db, len(self.data)))

        if not self.instrs or self.instrs[-1]["op"] != "RET":
            self.emit("RET", [self.alloc(0)])
        self.patch(jmp_over, self.here())
        return ni

    # ── block / statement ─────────────────────────────────────
    def _block(self, lines, start, end, scope):
        i = start
        while i < end:
            s = lines[i].strip()
            if not s or s.startswith("#"): i+=1; continue
            i = self._stmt(lines, i, end, scope)

    def _stmt(self, lines, i, end, scope):
        s = lines[i].strip()
        if   s.startswith("if "):    return self._if(lines, i, end, scope)
        elif s.startswith("while "): return self._while(lines, i, end, scope)
        elif s.startswith("return "):
            rv = self._expr(s[7:].strip(), scope)
            self.emit("RET", [rv]); return i+1
        elif s.startswith("print "):
            idx = self._expr(s[6:].strip(), scope)
            self.emit("PRINT", [idx]); return i+1

        # assignment (array set OR regular)
        elif "=" in s and not any(op in s for op in ["==","!=","<=",">="]):
            eq = s.index("=")
            lhs = s[:eq].strip()
            rhs = s[eq+1:].strip()
            # array element assignment: arr[idx] = val
            if "[" in lhs and lhs.rstrip().endswith("]"):
                bracket  = lhs.index("[")
                arr_name = lhs[:bracket].strip()
                idx_expr = lhs[bracket+1:lhs.rindex("]")].strip()
                arr_slot = self._resolve(arr_name, scope)
                idx_slot = self._expr(idx_expr, scope)
                val_slot = self._expr(rhs, scope)
                self.emit("ARR_SET", [arr_slot, idx_slot, val_slot])
            else:
                res = self._expr(rhs, scope)
                if lhs not in scope: scope[lhs] = self.alloc(0)
                self.emit("LOAD", [res, scope[lhs]])
            return i+1

        return i+1

    def _find_block(self, lines, hi, end):
        if hi+1 >= end: return [], hi+1
        base = len(lines[hi]) - len(lines[hi].lstrip())
        blk = []; i = hi+1
        while i < end:
            raw = lines[i]; s = raw.strip()
            if not s: i+=1; continue
            ci = len(raw) - len(raw.lstrip())
            if ci <= base: break
            blk.append(raw); i+=1
        return blk, i

    def _if(self, lines, i, end, scope):
        s        = lines[i].strip()
        cond_idx = self._cond(s[3:].rstrip(":").strip(), scope)
        jif      = self.emit("JIF", [cond_idx], jump_to=0)
        th, ni   = self._find_block(lines, i, end)
        self._block(th, 0, len(th), scope)
        if ni < end and lines[ni].strip() == "else:":
            jmp = self.emit("JMP", jump_to=0)
            self.patch(jif, self.here())
            el, ni = self._find_block(lines, ni, end)
            self._block(el, 0, len(el), scope)
            self.patch(jmp, self.here())
        else:
            self.patch(jif, self.here())
        return ni

    def _while(self, lines, i, end, scope):
        s        = lines[i].strip()
        top      = self.here()
        cond_idx = self._cond(s[6:].rstrip(":").strip(), scope)
        jif      = self.emit("JIF", [cond_idx], jump_to=0)
        bd, ni   = self._find_block(lines, i, end)
        self._block(bd, 0, len(bd), scope)
        self.emit("JMP", jump_to=top)
        self.patch(jif, self.here())
        return ni

    def _cond(self, s, scope):
        for os, on in [("<=","CMP_LTE"),(">=","CMP_GTE"),("==","CMP_EQ"),
                       ("!=","CMP_NEQ"),("<","CMP_LT"),(">","CMP_GT")]:
            if os in s:
                l, r = s.split(os, 1)
                li = self._expr(l.strip(), scope)
                ri = self._expr(r.strip(), scope)
                res = self.alloc(0); self.emit(on, [li,ri,res]); return res
        return self._resolve(s.strip(), scope)

    def _expr(self, expr, scope):
        expr = expr.strip()

        # array indexing: arr[idx]
        if "[" in expr and expr.rstrip().endswith("]"):
            bracket = expr.index("[")
            arr_name = expr[:bracket].strip()
            idx_expr = expr[bracket+1:expr.rindex("]")].strip()
            if arr_name and not any(op in arr_name for op in ["+","-","*","/"]):
                arr_slot = self._resolve(arr_name, scope)
                idx_slot = self._expr(idx_expr, scope)
                res      = self.alloc(0)
                self.emit("ARR_GET", [arr_slot, idx_slot, res])
                return res

        # array(n) constructor
        if expr.startswith("array(") and expr.endswith(")"):
            size_expr = expr[6:-1].strip()
            size_slot = self._expr(size_expr, scope)
            res       = self.alloc(0)
            self.emit("ARR_NEW", [size_slot, res])
            return res

        # len(arr)
        if expr.startswith("len(") and expr.endswith(")"):
            arr_expr = expr[4:-1].strip()
            arr_slot = self._resolve(arr_expr, scope)
            res      = self.alloc(0)
            self.emit("ARR_LEN", [arr_slot, res])
            return res

        # binary operators (paren-aware, left to right within precedence)
        for os, on in [("+","ADD"),("-","SUB"),("*","MUL"),("/","DIV"),("%","MOD")]:
            depth = 0; idx = -1
            for k, ch in enumerate(expr):
                if ch == "(": depth += 1
                elif ch == ")": depth -= 1
                elif ch == os and depth == 0: idx = k; break
            if idx > 0:
                l = expr[:idx].strip(); r = expr[idx+len(os):].strip()
                if l and r:
                    li = self._expr(l, scope); ri = self._expr(r, scope)
                    res = self.alloc(0); self.emit(on, [li,ri,res]); return res

        # function call: name(...) ending with )
        if "(" in expr and expr.rstrip().endswith(")"):
            fname = expr[:expr.index("(")].strip()
            if fname.isidentifier() and fname in self.funcs:
                astr  = expr[expr.index("(")+1:expr.rindex(")")].strip()
                atoks = [a.strip() for a in astr.split(",") if a.strip()] if astr else []
                fi    = self.funcs[fname]
                aidxs = [self._expr(a, scope) for a in atoks]
                res   = self.alloc(0)
                self.emit("CALL", [fi["id"], len(aidxs), res] + aidxs)
                return res

        return self._resolve(expr, scope)

    def _resolve(self, token, scope):
        token = token.strip()
        if token.lstrip("-").isdigit(): return self.alloc(int(token) & 0xFFFF)
        if token in scope: return scope[token]
        return self.gvar(token)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VIRTUAL MACHINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class VM:
    def __init__(self, max_steps=10_000_000):
        self.output    = []
        self.max_steps = max_steps
        self.func_local_slots = {}

    def run(self, bitstream):
        ops, data = decode_bytecode(bitstream)
        data   = list(data)
        arrays = []  # runtime array table: list of lists

        # build function table
        ftable = {}
        for idx, op in enumerate(ops):
            if op["op"] == "FUNC":
                fid    = op["args"][0]; npr = op["args"][1]
                pslots = op["args"][2:2+npr]
                lslots = self.func_local_slots.get(fid, set(pslots))
                ftable[fid] = {"entry":idx, "nparams":npr,
                               "param_slots":pslots, "local_slots":lslots}

        pc = 0; steps = 0; cstack = []

        while pc < len(ops):
            if steps > self.max_steps: print("  [VM: step limit]"); break
            steps += 1
            op = ops[pc]; name = op["op"]; args = op["args"]

            if   name=="ADD"     and len(args)>=3: data[args[2]]=(data[args[0]]+data[args[1]])&0xFFFF
            elif name=="SUB"     and len(args)>=3: data[args[2]]=(data[args[0]]-data[args[1]])&0xFFFF
            elif name=="MUL"     and len(args)>=3: data[args[2]]=(data[args[0]]*data[args[1]])&0xFFFF
            elif name=="DIV"     and len(args)>=3: data[args[2]]=(data[args[0]]//data[args[1]]) if data[args[1]] else 0
            elif name=="MOD"     and len(args)>=3: data[args[2]]=(data[args[0]]%data[args[1]])  if data[args[1]] else 0
            elif name=="LOAD"    and len(args)>=2: data[args[1]]=data[args[0]]
            elif name=="PRINT"   and len(args)>=1:
                v = data[args[0]]; self.output.append(v); print(f"  → {v}")
            elif name=="CMP_LT"  and len(args)>=3: data[args[2]]=1 if data[args[0]]< data[args[1]] else 0
            elif name=="CMP_GT"  and len(args)>=3: data[args[2]]=1 if data[args[0]]> data[args[1]] else 0
            elif name=="CMP_EQ"  and len(args)>=3: data[args[2]]=1 if data[args[0]]==data[args[1]] else 0
            elif name=="CMP_NEQ" and len(args)>=3: data[args[2]]=1 if data[args[0]]!=data[args[1]] else 0
            elif name=="CMP_LTE" and len(args)>=3: data[args[2]]=1 if data[args[0]]<=data[args[1]] else 0
            elif name=="CMP_GTE" and len(args)>=3: data[args[2]]=1 if data[args[0]]>=data[args[1]] else 0
            elif name=="JMP": pc=op["jump_to"]; continue
            elif name=="JIF":
                if data[args[0]]==0: pc=op["jump_to"]; continue
            elif name=="FUNC": pass  # skipped by JMP emitted before FUNC

            # ── array operations ──────────────────────────────
            elif name=="ARR_NEW" and len(args)>=2:
                size   = data[args[0]]
                arr_id = len(arrays)
                arrays.append([0]*size)
                # extend data if needed
                while args[1] >= len(data): data.append(0)
                data[args[1]] = arr_id
            elif name=="ARR_GET" and len(args)>=3:
                arr_id = data[args[0]]
                idx    = data[args[1]]
                arr    = arrays[arr_id] if arr_id < len(arrays) else []
                data[args[2]] = arr[idx] if 0 <= idx < len(arr) else 0
            elif name=="ARR_SET" and len(args)>=3:
                arr_id = data[args[0]]
                idx    = data[args[1]]
                val    = data[args[2]]
                if arr_id < len(arrays) and 0 <= idx < len(arrays[arr_id]):
                    arrays[arr_id][idx] = val
            elif name=="ARR_LEN" and len(args)>=2:
                arr_id = data[args[0]]
                data[args[1]] = len(arrays[arr_id]) if arr_id < len(arrays) else 0

            # ── call / return ─────────────────────────────────
            elif name=="CALL":
                fid=args[0]; na=args[1]; res_slot=args[2]; cargs=args[3:3+na]
                if fid in ftable:
                    fi      = ftable[fid]
                    pslots  = fi["param_slots"]
                    all_s   = set(pslots) | fi.get("local_slots", set())
                    saved   = {s: data[s] for s in all_s if s < len(data)}
                    arg_vals= [data[cargs[j]] for j in range(min(na, len(pslots)))]
                    for j, ps in enumerate(pslots):
                        while ps >= len(data): data.append(0)
                        data[ps] = arg_vals[j] if j < len(arg_vals) else 0
                    cstack.append({"ret_pc":pc+1,"res_slot":res_slot,"saved":saved})
                    pc = fi["entry"]+1; continue
            elif name=="RET":
                if cstack:
                    frame = cstack.pop()
                    rv    = data[args[0]] if args else 0
                    for slot, val in frame["saved"].items():
                        while slot >= len(data): data.append(0)
                        data[slot] = val
                    while frame["res_slot"] >= len(data): data.append(0)
                    data[frame["res_slot"]] = rv
                    pc = frame["ret_pc"]; continue
                else: break
            pc += 1
        return data

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST RUNNER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _run(source, expected=None):
    c  = Compiler()
    bc = c.compile(source)
    vm = VM()
    vm.func_local_slots = {fi["id"]: fi.get("local_slots",set()) for fi in c.funcs.values()}
    vm.run(bc)
    ok = (expected is None) or (vm.output == expected)
    return ok, len(bc), len(c.instrs), len(c.data), vm.output

def run_test(title, source, expected=None):
    print(f"\n{'═'*62}")
    print(f"  {title}")
    print(f"{'═'*62}")
    for line in source.strip().splitlines():
        if line.strip(): print(f"    {line.rstrip()}")
    print()
    try:
        ok, bits, ni, nd, got = _run(source, expected)
        print(f"  ZCE: {bits} bits ({bits//8} bytes) | {ni} instrs | {nd} data slots")
        print(f"  Output:")
        for v in got: print(f"  → {v}")
        print(f"\n  Expected : {expected}")
        print(f"  Got      : {got}")
        print(f"  {'✓ PASS' if ok else '✗ FAIL'}")
        return ok, bits
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ✗ ERROR: {e}")
        return False, 0

def py_bytecode_bits(py_source):
    """Pure bytecode bytes from Python co_code, recursively."""
    code = compile(py_source, "<string>", "exec")
    def total(c):
        t = len(c.co_code)
        for k in c.co_consts:
            if hasattr(k, "co_code"): t += total(k)
        return t
    return total(code) * 8

def run_benchmark(title, zce_src, py_src, expected=None):
    print(f"\n{'═'*62}")
    print(f"  BENCH: {title}")
    print(f"{'═'*62}")
    try:
        ok, zb, ni, nd, got = _run(zce_src, expected)
        pb  = py_bytecode_bits(py_src)
        pm  = len(marshal.dumps(compile(py_src,"<string>","exec"))) * 8
        print(f"  ZCE bytecode   : {zb:6} bits  ({zb//8:4} bytes)")
        print(f"  Python co_code : {pb:6} bits  ({pb//8:4} bytes)  [pure bytecode only]")
        print(f"  Python marshal : {pm:6} bits  ({pm//8:4} bytes)  [full .pyc equivalent]")
        print(f"  ZCE/co_code    : {zb/pb:.2f}x   ZCE/marshal: {zb/pm:.2f}x")
        print(f"  Output:")
        for v in got: print(f"  → {v}")
        print(f"\n  {'✓ PASS' if ok else '✗ FAIL'}")
        return ok, zb, pb, pm
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ✗ ERROR: {e}")
        return False, 0, 0, 0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST SUITE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── Part 1: Elias coding verification ───────────────────────
ELIAS_TESTS = [
("Elias: basic arithmetic (small numbers)","""
x = 3 + 5
print x
""",[8]),

("Elias: large number 1000+2000","""
x = 1000 + 2000
print x
""",[3000]),

("Elias: factorial 7 (=5040 fits in 16-bit)","""
def fact(n):
    if n <= 1:
        return 1
    r = fact(n - 1)
    return n * r
print fact(7)
""",[5040]),
]

# ── Part 2: Array tests ──────────────────────────────────────
ARRAY_TESTS = [
("Array: create and read","""
arr = array(5)
arr[0] = 10
arr[1] = 20
arr[2] = 30
print arr[0]
print arr[1]
print arr[2]
""",[10,20,30]),

("Array: len","""
arr = array(8)
n = len(arr)
print n
""",[8]),

("Array: fill with loop","""
arr = array(5)
i = 0
while i < 5:
    arr[i] = i * 2
    i = i + 1
i = 0
while i < 5:
    print arr[i]
    i = i + 1
""",[0,2,4,6,8]),

("Array: sum elements","""
arr = array(5)
arr[0] = 10
arr[1] = 20
arr[2] = 30
arr[3] = 40
arr[4] = 50
s = 0
i = 0
n = len(arr)
while i < n:
    s = s + arr[i]
    i = i + 1
print s
""",[150]),

("Array: find maximum","""
arr = array(6)
arr[0] = 3
arr[1] = 1
arr[2] = 4
arr[3] = 1
arr[4] = 5
arr[5] = 9
mx = arr[0]
i = 1
n = len(arr)
while i < n:
    if arr[i] > mx:
        mx = arr[i]
    i = i + 1
print mx
""",[9]),

("Array: reverse","""
arr = array(5)
arr[0] = 1
arr[1] = 2
arr[2] = 3
arr[3] = 4
arr[4] = 5
i = 0
j = 4
while i < j:
    tmp = arr[i]
    arr[i] = arr[j]
    arr[j] = tmp
    i = i + 1
    j = j - 1
i = 0
while i < 5:
    print arr[i]
    i = i + 1
""",[5,4,3,2,1]),

("Array: bubble sort (proper)","""
arr = array(8)
arr[0] = 64
arr[1] = 34
arr[2] = 25
arr[3] = 12
arr[4] = 22
arr[5] = 11
arr[6] = 90
arr[7] = 1
n = len(arr)
i = 0
while i < n:
    j = 0
    while j < n - 1:
        if arr[j] > arr[j+1]:
            tmp = arr[j]
            arr[j] = arr[j+1]
            arr[j+1] = tmp
        j = j + 1
    i = i + 1
i = 0
while i < n:
    print arr[i]
    i = i + 1
""",[1,11,12,22,25,34,64,90]),

("Array: function taking array","""
def arr_sum(arr, n):
    s = 0
    i = 0
    while i < n:
        s = s + arr[i]
        i = i + 1
    return s

data = array(4)
data[0] = 10
data[1] = 20
data[2] = 30
data[3] = 40
result = arr_sum(data, 4)
print result
""",[100]),
]

# ── Part 3: Real program benchmarks ─────────────────────────
BENCHMARKS = [
("Bubble sort (array)",
"""
arr = array(8)
arr[0] = 64
arr[1] = 34
arr[2] = 25
arr[3] = 12
arr[4] = 22
arr[5] = 11
arr[6] = 90
arr[7] = 1
n = len(arr)
i = 0
while i < n:
    j = 0
    while j < n - 1:
        if arr[j] > arr[j+1]:
            tmp = arr[j]
            arr[j] = arr[j+1]
            arr[j+1] = tmp
        j = j + 1
    i = i + 1
i = 0
while i < n:
    print arr[i]
    i = i + 1
""",
"""
arr=[64,34,25,12,22,11,90,1]
n=len(arr)
for i in range(n):
    for j in range(n-1):
        if arr[j]>arr[j+1]:
            arr[j],arr[j+1]=arr[j+1],arr[j]
for x in arr:
    print(x)
""",
[1,11,12,22,25,34,64,90]),

("Primes sieve to 50",
"""
sieve = array(51)
i = 2
while i <= 50:
    sieve[i] = 1
    i = i + 1
i = 2
while i <= 50:
    if sieve[i] == 1:
        j = i * 2
        while j <= 50:
            sieve[j] = 0
            j = j + i
    i = i + 1
i = 2
while i <= 50:
    if sieve[i] == 1:
        print i
    i = i + 1
""",
"""
sieve=[0]*51
for i in range(2,51): sieve[i]=1
for i in range(2,51):
    if sieve[i]:
        j=i*2
        while j<=50:
            sieve[j]=0
            j+=i
for i in range(2,51):
    if sieve[i]: print(i)
""",
[2,3,5,7,11,13,17,19,23,29,31,37,41,43,47]),

("Fibonacci (recursive)",
"""
def fib(n):
    if n <= 1:
        return n
    a = fib(n - 1)
    b = fib(n - 2)
    return a + b
i = 0
while i <= 10:
    print fib(i)
    i = i + 1
""",
"""
def fib(n):
    if n<=1: return n
    return fib(n-1)+fib(n-2)
for i in range(11): print(fib(i))
""",
[0,1,1,2,3,5,8,13,21,34,55]),

("GCD Euclidean",
"""
def gcd(a, b):
    while b != 0:
        tmp = b
        b = a - a / b * b
        a = tmp
    return a
print gcd(48, 18)
print gcd(100, 75)
print gcd(252, 105)
""",
"""
def gcd(a,b):
    while b:
        a,b=b,a%b
    return a
print(gcd(48,18))
print(gcd(100,75))
print(gcd(252,105))
""",
[6,25,21]),
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ELIAS SAVINGS REPORT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def elias_savings_report():
    print(f"\n{'▓'*62}")
    print("  ELIAS GAMMA vs FIXED-16 DATA SECTION SAVINGS")
    print(f"{'▓'*62}")
    print(f"  {'Value':>8}  {'Fixed-16':>10}  {'Elias':>8}  {'Savings':>10}")
    print(f"  {'-'*8}  {'-'*10}  {'-'*8}  {'-'*10}")
    for v in [0,1,2,3,5,7,10,15,16,31,63,100,255,1000,65535]:
        e = elias_bits(v)
        print(f"  {v:>8}  {16:>10}  {e:>8}  {16-e:>+10} bits")
    print()
    # show for a typical program's data
    typical = [0]*10 + [1,2,3,5,10,0,0,0,1,0]
    f16 = len(typical)*16
    eli = sum(elias_bits(v) for v in typical)
    print(f"  Typical program data ({len(typical)} slots):")
    print(f"  Fixed-16 total : {f16} bits")
    print(f"  Elias total    : {eli} bits")
    print(f"  Savings        : {f16-eli} bits ({100*(f16-eli)/f16:.0f}%)")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    print("\n" + "█"*62)
    print("  ZC-LANG v4 — ELIAS CODING + ARRAYS")
    print("  Zero-Count Encoding System")
    print("█"*62)

    elias_savings_report()

    print(f"\n\n{'▓'*62}")
    print("  PART 1: ELIAS CODING CORRECTNESS")
    print(f"{'▓'*62}")
    r1 = [run_test(t,s,e) for t,s,e in ELIAS_TESTS]

    print(f"\n\n{'▓'*62}")
    print("  PART 2: ARRAY OPERATIONS")
    print(f"{'▓'*62}")
    r2 = [run_test(t,s,e) for t,s,e in ARRAY_TESTS]

    print(f"\n\n{'▓'*62}")
    print("  PART 3: BENCHMARKS vs PYTHON")
    print(f"{'▓'*62}")
    bm_data = []
    r3 = []
    for t,zs,ps,e in BENCHMARKS:
        ok, zb, pb, pm = run_benchmark(t, zs, ps, e)
        r3.append(ok); bm_data.append((t,zb,pb,pm))

    # ── final summary ────────────────────────────────────────
    print(f"\n\n{'█'*62}")
    print("  FINAL SUMMARY")
    print(f"{'█'*62}")
    p1=sum(x[0] for x in r1); t1=len(r1)
    p2=sum(x[0] for x in r2); t2=len(r2)
    p3=sum(r3);                t3=len(r3)
    print(f"\n  Elias correctness : {p1}/{t1} {'✓' if p1==t1 else '✗'}")
    print(f"  Array operations  : {p2}/{t2} {'✓' if p2==t2 else '✗'}")
    print(f"  Benchmarks        : {p3}/{t3} {'✓' if p3==t3 else '✗'}")
    print(f"\n  Benchmark size comparison:")
    print(f"  {'Program':<32} {'ZCE':>8} {'Py code':>8} {'Py .pyc':>8} {'vs code':>8} {'vs .pyc':>8}")
    print(f"  {'-'*32} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for t,zb,pb,pm in bm_data:
        print(f"  {t:<32} {zb//8:>7}B {pb//8:>7}B {pm//8:>7}B {zb/pb:>8.2f}x {zb/pm:>8.2f}x")
    total = p1+p2+p3; total_t = t1+t2+t3
    print(f"\n  Total: {total}/{total_t} tests passed {'✓ ALL PASS' if total==total_t else ''}")
    print("█"*62+"\n")
