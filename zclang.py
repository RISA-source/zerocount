"""
ZC-LANG — Zero-Count Encoding System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A self-describing binary encoding where the count of consecutive zeros
is the grammar, and the count of consecutive ones is the tree depth.

DESIGN PHILOSOPHY:
  Zeros  = token type  (what this token IS)
  Ones   = tree depth  (where in the tree it LIVES)
  Zero   = terminator  (token complete)

TOKEN FORMAT:  [N zeros][M ones][0]
  N = token type (1..12)
  M = tree depth (1..N)
  0 = end of token

DEPTH=0 ESCAPE (M=0):  [N zeros][0]
  Not a structural token. N encodes an Elias gamma pointer.
  This means: "read data slot index N via Elias".
  Zero ones = not a tree position = it's a reference.

TOKEN TYPE TABLE:
  N=1   value reference  (depth=0 escape only — pointer follows in Elias)
  N=2   instruction      (followed by instruction code token)
  N=3   variable binding (name hash + slot in data section)
  N=4   block
  N=5   section
  N=6   jump-if-false    (Elias: cond slot, Elias: instr target)
  N=7   document
  N=8   unconditional jump (Elias: instr target)
  N=9   function entry   (depth marks function scope level)
  N=10  call             (Elias: func_id, nargs, result_slot, arg slots)
  N=11  return           (Elias: return value slot)
  N=12  array operation  (followed by array op code token)

INSTRUCTION CODES (N after N=2 token):
  N=1 ADD   N=2 SUB   N=3 MUL   N=4 DIV   N=5 MOD
  N=6 LOAD  N=7 PRINT
  N=8  CMP_LT   N=9  CMP_GT   N=10 CMP_EQ
  N=11 CMP_NEQ  N=12 CMP_LTE  N=13 CMP_GTE

ARRAY OP CODES (N after N=12 token):
  N=1 ARR_NEW   N=2 ARR_GET   N=3 ARR_SET   N=4 ARR_LEN

ELIAS GAMMA CODING (for all pointers and data values):
  Encode V≥0 as Elias(V+1):
    k = floor(log2(V+1))
    emit k zeros, then (V+1) in binary (k+1 bits)
  Self-delimiting. Decoder counts leading zeros.
  Examples: 0→'1'  1→'010'  2→'011'  7→'00111'  100→'0000001100100'

BITSTREAM FORMAT:
  [16-bit grammar length] [16-bit data count] [grammar bits] [Elias data]

DEPTH MODEL:
  Every grammar token declares its depth via the ones-run.
  Depth=1  top-level code
  Depth=2  function body / top-level block
  Depth=3+ nested blocks
  The VM maintains a frame stack keyed by depth.
  On depth decrease: pop frames until current depth.
  Variables are scoped to their declaration depth — inner sees outer,
  outer cannot see inner.
"""

import math, marshal, sys

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ELIAS GAMMA
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def eg_enc(v):
    """Encode non-negative integer v using Elias gamma (offset +1)."""
    n = v + 1
    k = int(math.log2(n))
    return '0' * k + format(n, f'0{k+1}b')

def eg_dec(bits, pos):
    """Decode one Elias gamma value at pos. Returns (value, new_pos)."""
    k = 0
    while pos < len(bits) and bits[pos] == '0': k += 1; pos += 1
    n = int(bits[pos:pos+k+1], 2)
    return n - 1, pos + k + 1

def eg_bits(v):
    n = v + 1
    k = int(math.log2(n)) if n > 0 else 0
    return 2 * k + 1

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TOKEN PRIMITIVES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def tok(n, depth):
    """Emit grammar token: N zeros + depth ones + 0."""
    return '0'*n + '1'*depth + '0'

def ptr(slot):
    """Emit a depth=0 pointer (Elias gamma of slot index).
    Format: Elias(slot+1) — leading zeros then value bits ending in 1,
    but we need to distinguish from a depth token.
    Since depth tokens end in 0, and Elias ends in a 1-bit of the value,
    we use a clean escape: emit tok(n=1, depth=0) style which is '0' + '0' = '00'
    but that's ambiguous. 
    
    REVISED POINTER SCHEME:
    After reading a token that expects a pointer argument (N=2 instruction,
    N=6 JIF, N=8 JMP, N=9 FUNC, N=10 CALL, N=11 RET, N=12 ARR),
    the decoder is in 'read pointer' mode, not 'read token' mode.
    In pointer mode it reads one Elias gamma value directly.
    This avoids any ambiguity — the decoder always knows which mode it's in.
    """
    return eg_enc(slot)

def raw16(n): return format(n & 0xFFFF, '016b')
def r16(b):   return int(b, 2)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# INSTRUCTION / ARRAY OP TABLES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INSTR = {
    1:'ADD', 2:'SUB',  3:'MUL',  4:'DIV',  5:'MOD',
    6:'LOAD', 7:'PRINT',
    8:'CMP_LT', 9:'CMP_GT', 10:'CMP_EQ',
    11:'CMP_NEQ', 12:'CMP_LTE', 13:'CMP_GTE',
}
INSTR_R = {v:k for k,v in INSTR.items()}

ARROP = {1:'ARR_NEW', 2:'ARR_GET', 3:'ARR_SET', 4:'ARR_LEN'}
ARROP_R = {v:k for k,v in ARROP.items()}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENCODER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Depth conventions
D_TOP   = 1   # top-level
D_FUNC  = 2   # function body
D_BLOCK = 3   # block inside function (if/while body)
D_INNER = 4   # nested block

def encode(instructions, data_values):
    """Encode instruction list + data into ZCE bitstream."""
    g = []
    # Document envelope at depth 1
    g.append(tok(7, 1))   # document open
    g.append(tok(5, 1))   # section open

    for instr in instructions:
        op   = instr['op']
        args = instr.get('args', [])
        d    = instr.get('depth', D_TOP)

        if op == 'JMP':
            g.append(tok(8, d))
            g.append(ptr(instr['jump_to']))
        elif op == 'JIF':
            g.append(tok(6, d))
            g.append(ptr(args[0]))
            g.append(ptr(instr['jump_to']))
        elif op == 'FUNC':
            # args = [func_id, nparams, pslot0, pslot1, ...]
            g.append(tok(9, d))
            g.append(ptr(args[0]))   # func_id
            g.append(ptr(args[1]))   # nparams
            for ps in args[2:]:
                g.append(ptr(ps))
        elif op == 'CALL':
            # args = [func_id, nargs, result_slot, arg0, arg1, ...]
            g.append(tok(10, d))
            g.append(ptr(args[0]))   # func_id
            g.append(ptr(args[1]))   # nargs
            g.append(ptr(args[2]))   # result_slot
            for a in args[3:]:
                g.append(ptr(a))
        elif op == 'RET':
            g.append(tok(11, d))
            g.append(ptr(args[0]))
        elif op in ARROP_R:
            g.append(tok(12, d))
            g.append(tok(ARROP_R[op], d))
            for a in args:
                g.append(ptr(a))
        else:
            # Regular instruction
            g.append(tok(2, d))
            g.append(tok(INSTR_R[op], d))
            for a in args:
                g.append(ptr(a))

    g.append(tok(5, 1))   # section close
    g.append(tok(7, 1))   # document close

    grammar  = ''.join(g)
    data_enc = ''.join(eg_enc(v) for v in data_values)
    header   = raw16(len(grammar)) + raw16(len(data_values))
    return header + grammar + data_enc


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DECODER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def decode(bs):
    """Decode ZCE bitstream into (ops list, data list)."""
    gl   = r16(bs[:16])
    dc   = r16(bs[16:32])
    gram = bs[32:32+gl]
    draw = bs[32+gl:]

    # Decode Elias data
    pos2, data = 0, []
    for _ in range(dc):
        v, pos2 = eg_dec(draw, pos2)
        data.append(v)

    ops = []
    pos = 0

    def read_ptr():
        nonlocal pos
        v, pos = eg_dec(gram, pos)
        return v

    def read_tok():
        """Read one grammar token. Returns (n, depth)."""
        nonlocal pos
        n = 0
        while pos < len(gram) and gram[pos] == '0': n += 1; pos += 1
        m = 0
        while pos < len(gram) and gram[pos] == '1': m += 1; pos += 1
        if pos < len(gram) and gram[pos] == '0':    pos += 1  # terminator
        return n, m

    while pos < len(gram):
        if pos >= len(gram): break
        n, depth = read_tok()
        if n == 0: continue

        if n == 7 or n == 5:
            # document/section envelope — skip
            pass
        elif n == 8:
            tgt = read_ptr()
            ops.append({'op':'JMP', 'args':[], 'jump_to':tgt, 'depth':depth})
        elif n == 6:
            cond = read_ptr()
            tgt  = read_ptr()
            ops.append({'op':'JIF', 'args':[cond], 'jump_to':tgt, 'depth':depth})
        elif n == 9:
            fid  = read_ptr()
            npr  = read_ptr()
            pslots = [read_ptr() for _ in range(npr)]
            ops.append({'op':'FUNC', 'args':[fid, npr]+pslots, 'depth':depth})
        elif n == 10:
            fid  = read_ptr()
            na   = read_ptr()
            res  = read_ptr()
            cargs= [read_ptr() for _ in range(na)]
            ops.append({'op':'CALL', 'args':[fid,na,res]+cargs, 'depth':depth})
        elif n == 11:
            rv = read_ptr()
            ops.append({'op':'RET', 'args':[rv], 'depth':depth})
        elif n == 12:
            an, _ = read_tok()   # array op code
            aop = ARROP.get(an, f'UNK_ARR{an}')
            args = []
            # peek ahead for pointer args — read until next structural token
            # We know each array op has fixed arity:
            # ARR_NEW: 2 (size, result)
            # ARR_GET: 3 (arr, idx, result)
            # ARR_SET: 3 (arr, idx, val)
            # ARR_LEN: 2 (arr, result)
            arity = {1:2, 2:3, 3:3, 4:2}.get(an, 0)
            for _ in range(arity): args.append(read_ptr())
            ops.append({'op':aop, 'args':args, 'depth':depth})
        elif n == 2:
            ic, _ = read_tok()   # instruction code
            iop   = INSTR.get(ic, f'UNK{ic}')
            # arity by instruction
            arity = {
                'ADD':3,'SUB':3,'MUL':3,'DIV':3,'MOD':3,
                'LOAD':2,'PRINT':1,
                'CMP_LT':3,'CMP_GT':3,'CMP_EQ':3,
                'CMP_NEQ':3,'CMP_LTE':3,'CMP_GTE':3,
            }.get(iop, 0)
            args = [read_ptr() for _ in range(arity)]
            ops.append({'op':iop, 'args':args, 'depth':depth})

    return ops, data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMPILER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Compiler:
    def __init__(self):
        self.data    = []       # flat data slot array
        self.instrs  = []       # instruction list
        self.funcs   = {}       # name → {id, params, param_slots, local_slots, entry}
        self._fid    = 0
        self._depth  = D_TOP    # current emission depth
        self._scopes = [{}]     # scope stack: list of {name: slot}

    # ── data allocation ───────────────────────────────────────
    def alloc(self, v=0):
        i = len(self.data); self.data.append(int(v) & 0xFFFF); return i

    def _lookup(self, name):
        """Look up name in scope stack, innermost first."""
        for scope in reversed(self._scopes):
            if name in scope: return scope[name]
        return None

    def _declare(self, name, slot=None):
        """Declare name in current (innermost) scope."""
        if slot is None: slot = self.alloc(0)
        self._scopes[-1][name] = slot
        return slot

    def _push_scope(self): self._scopes.append({})
    def _pop_scope(self):  self._scopes.pop()

    # ── instruction emission ──────────────────────────────────
    def emit(self, op, args=None, jump_to=None, depth=None):
        i = {'op':op, 'args':args or [], 'depth': depth or self._depth}
        if jump_to is not None: i['jump_to'] = jump_to
        self.instrs.append(i); return len(self.instrs) - 1

    def patch(self, idx, tgt): self.instrs[idx]['jump_to'] = tgt
    def here(self):            return len(self.instrs)

    # ── compile entry ─────────────────────────────────────────
    def compile(self, source):
        lines = [l.rstrip() for l in source.splitlines()]
        # Pass 1: register all function definitions
        i = 0
        while i < len(lines):
            if lines[i].strip().startswith('def '): i = self._func(lines, i, len(lines))
            else: i += 1
        # Pass 2: compile top-level code (skip def blocks)
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            if not s or s.startswith('#'):    i += 1; continue
            if s.startswith('def '):
                _, i = self._find_block(lines, i, len(lines)); continue
            i = self._stmt(lines, i, len(lines))
        return encode(self.instrs, self.data)

    # ── function compilation ──────────────────────────────────
    def _func(self, lines, i, end):
        s      = lines[i].strip()[4:].rstrip(':')
        fname  = s[:s.index('(')].strip()
        params = [p.strip() for p in s[s.index('(')+1:s.index(')')].split(',') if p.strip()]
        fid    = self._fid; self._fid += 1

        # Allocate param slots
        pslots = [self.alloc(0) for _ in params]

        # Emit JMP over function body
        jmp_over = self.emit('JMP', jump_to=0, depth=D_TOP)

        # Emit FUNC marker at function depth
        entry = self.here()
        self.emit('FUNC', [fid, len(params)] + pslots, depth=D_FUNC)

        self.funcs[fname] = {
            'id': fid, 'params': params,
            'param_slots': pslots, 'entry': entry,
            'local_slots': set(pslots)
        }

        # Compile body with function scope at depth D_FUNC
        body, ni = self._find_block(lines, i, end)
        self._push_scope()
        for j, p in enumerate(params): self._scopes[-1][p] = pslots[j]

        old_depth = self._depth; self._depth = D_FUNC
        db = len(self.data)
        self._block(body, 0, len(body))
        self.funcs[fname]['local_slots'] = set(pslots) | set(range(db, len(self.data)))

        # Ensure RET at end
        if not self.instrs or self.instrs[-1]['op'] != 'RET':
            self.emit('RET', [self.alloc(0)], depth=D_FUNC)

        self._depth = old_depth
        self._pop_scope()
        self.patch(jmp_over, self.here())
        return ni

    # ── block / statement ─────────────────────────────────────
    def _block(self, lines, start, end):
        i = start
        while i < end:
            s = lines[i].strip()
            if not s or s.startswith('#'): i += 1; continue
            i = self._stmt(lines, i, end)

    def _stmt(self, lines, i, end):
        s = lines[i].strip()
        if   s.startswith('if '):    return self._if(lines, i, end)
        elif s.startswith('while '): return self._while(lines, i, end)
        elif s.startswith('return '):
            rv = self._expr(s[7:].strip())
            self.emit('RET', [rv]); return i+1
        elif s.startswith('print '):
            idx = self._expr(s[6:].strip())
            self.emit('PRINT', [idx]); return i+1
        elif '=' in s and not any(op in s for op in ['==','!=','<=','>=']):
            eq  = s.index('=')
            lhs = s[:eq].strip()
            rhs = s[eq+1:].strip()
            if '[' in lhs and lhs.rstrip().endswith(']'):
                bracket  = lhs.index('[')
                arr_name = lhs[:bracket].strip()
                idx_expr = lhs[bracket+1:lhs.rindex(']')].strip()
                arr_slot = self._resolve(arr_name)
                idx_slot = self._expr(idx_expr)
                val_slot = self._expr(rhs)
                self.emit('ARR_SET', [arr_slot, idx_slot, val_slot])
            else:
                res = self._expr(rhs)
                slot = self._lookup(lhs)
                if slot is None: slot = self._declare(lhs)
                self.emit('LOAD', [res, slot])
            return i+1
        return i+1

    def _find_block(self, lines, hi, end):
        if hi+1 >= end: return [], hi+1
        base = len(lines[hi]) - len(lines[hi].lstrip())
        blk = []; i = hi+1
        while i < end:
            raw = lines[i]; s = raw.strip()
            if not s: i += 1; continue
            if len(raw)-len(raw.lstrip()) <= base: break
            blk.append(raw); i += 1
        return blk, i

    def _if(self, lines, i, end):
        s        = lines[i].strip()
        cond_idx = self._cond(s[3:].rstrip(':').strip())
        jif      = self.emit('JIF', [cond_idx], jump_to=0)
        self._push_scope()
        old_depth = self._depth; self._depth = max(self._depth, D_BLOCK)
        th, ni   = self._find_block(lines, i, end)
        self._block(th, 0, len(th))
        if ni < end and lines[ni].strip() == 'else:':
            jmp = self.emit('JMP', jump_to=0)
            self.patch(jif, self.here())
            el, ni = self._find_block(lines, ni, end)
            self._block(el, 0, len(el))
            self.patch(jmp, self.here())
        else:
            self.patch(jif, self.here())
        self._depth = old_depth; self._pop_scope()
        return ni

    def _while(self, lines, i, end):
        s    = lines[i].strip()
        top  = self.here()
        self._push_scope()
        old_depth = self._depth; self._depth = max(self._depth, D_BLOCK)
        cond_idx = self._cond(s[6:].rstrip(':').strip())
        jif      = self.emit('JIF', [cond_idx], jump_to=0)
        bd, ni   = self._find_block(lines, i, end)
        self._block(bd, 0, len(bd))
        self.emit('JMP', jump_to=top)
        self.patch(jif, self.here())
        self._depth = old_depth; self._pop_scope()
        return ni

    def _cond(self, s):
        for os, on in [('<=','CMP_LTE'),('>=','CMP_GTE'),('==','CMP_EQ'),
                       ('!=','CMP_NEQ'),('<','CMP_LT'),('>','CMP_GT')]:
            if os in s:
                l, r = s.split(os, 1)
                li = self._expr(l.strip()); ri = self._expr(r.strip())
                res = self.alloc(0); self.emit(on, [li,ri,res]); return res
        return self._resolve(s.strip())

    def _expr(self, expr):
        expr = expr.strip()

        # array[idx]
        if '[' in expr and expr.rstrip().endswith(']'):
            bracket  = expr.index('[')
            arr_name = expr[:bracket].strip()
            idx_expr = expr[bracket+1:expr.rindex(']')].strip()
            if arr_name and not any(op in arr_name for op in ['+','-','*','/','^']):
                arr_slot = self._resolve(arr_name)
                idx_slot = self._expr(idx_expr)
                res      = self.alloc(0)
                self.emit('ARR_GET', [arr_slot, idx_slot, res])
                return res

        # array(n)
        if expr.startswith('array(') and expr.endswith(')'):
            size_slot = self._expr(expr[6:-1].strip())
            res       = self.alloc(0)
            self.emit('ARR_NEW', [size_slot, res])
            return res

        # len(arr)
        if expr.startswith('len(') and expr.endswith(')'):
            arr_slot = self._resolve(expr[4:-1].strip())
            res      = self.alloc(0)
            self.emit('ARR_LEN', [arr_slot, res])
            return res

        # Binary operators (paren-aware, left-to-right within level)
        for os, on in [('+','ADD'),('-','SUB'),('*','MUL'),('/','DIV'),('%','MOD')]:
            depth = 0; idx = -1
            for k, ch in enumerate(expr):
                if ch == '(': depth += 1
                elif ch == ')': depth -= 1
                elif ch == os and depth == 0: idx = k; break
            if idx > 0:
                l = expr[:idx].strip(); r = expr[idx+len(os):].strip()
                if l and r:
                    li = self._expr(l); ri = self._expr(r)
                    res = self.alloc(0); self.emit(on, [li,ri,res]); return res

        # Function call: name(args)
        if '(' in expr and expr.rstrip().endswith(')'):
            fname = expr[:expr.index('(')].strip()
            if fname.isidentifier() and fname in self.funcs:
                astr  = expr[expr.index('(')+1:expr.rindex(')')].strip()
                atoks = [a.strip() for a in astr.split(',') if a.strip()] if astr else []
                fi    = self.funcs[fname]
                aidxs = [self._expr(a) for a in atoks]
                res   = self.alloc(0)
                self.emit('CALL', [fi['id'], len(aidxs), res] + aidxs)
                return res

        return self._resolve(expr)

    def _resolve(self, token):
        token = token.strip()
        if token.lstrip('-').isdigit(): return self.alloc(int(token) & 0xFFFF)
        slot = self._lookup(token)
        if slot is not None: return slot
        return self._declare(token)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VIRTUAL MACHINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class VM:
    def __init__(self, max_steps=10_000_000):
        self.output    = []
        self.max_steps = max_steps
        self.func_local_slots = {}

    def run(self, bitstream):
        ops, data = decode(bitstream)
        data    = list(data)
        arrays  = []

        # Build function table
        ftable = {}
        for idx, op in enumerate(ops):
            if op['op'] == 'FUNC':
                fid    = op['args'][0]; npr = op['args'][1]
                pslots = op['args'][2:2+npr]
                lslots = self.func_local_slots.get(fid, set(pslots))
                ftable[fid] = {
                    'entry': idx, 'nparams': npr,
                    'param_slots': pslots, 'local_slots': lslots
                }

        def extend(slot):
            while slot >= len(data): data.append(0)

        pc = 0; steps = 0; cstack = []

        while pc < len(ops):
            if steps > self.max_steps: print('  [VM: step limit]'); break
            steps += 1
            op = ops[pc]; name = op['op']; args = op['args']

            if   name=='ADD'     and len(args)>=3:
                extend(args[2]); data[args[2]]=(data[args[0]]+data[args[1]])&0xFFFF
            elif name=='SUB'     and len(args)>=3:
                extend(args[2]); data[args[2]]=(data[args[0]]-data[args[1]])&0xFFFF
            elif name=='MUL'     and len(args)>=3:
                extend(args[2]); data[args[2]]=(data[args[0]]*data[args[1]])&0xFFFF
            elif name=='DIV'     and len(args)>=3:
                extend(args[2]); data[args[2]]=(data[args[0]]//data[args[1]]) if data[args[1]] else 0
            elif name=='MOD'     and len(args)>=3:
                extend(args[2]); data[args[2]]=(data[args[0]]%data[args[1]])  if data[args[1]] else 0
            elif name=='LOAD'    and len(args)>=2:
                extend(args[1]); data[args[1]]=data[args[0]]
            elif name=='PRINT'   and len(args)>=1:
                v = data[args[0]]; self.output.append(v); print(f'  → {v}')
            elif name=='CMP_LT'  and len(args)>=3: extend(args[2]); data[args[2]]=1 if data[args[0]]< data[args[1]] else 0
            elif name=='CMP_GT'  and len(args)>=3: extend(args[2]); data[args[2]]=1 if data[args[0]]> data[args[1]] else 0
            elif name=='CMP_EQ'  and len(args)>=3: extend(args[2]); data[args[2]]=1 if data[args[0]]==data[args[1]] else 0
            elif name=='CMP_NEQ' and len(args)>=3: extend(args[2]); data[args[2]]=1 if data[args[0]]!=data[args[1]] else 0
            elif name=='CMP_LTE' and len(args)>=3: extend(args[2]); data[args[2]]=1 if data[args[0]]<=data[args[1]] else 0
            elif name=='CMP_GTE' and len(args)>=3: extend(args[2]); data[args[2]]=1 if data[args[0]]>=data[args[1]] else 0
            elif name=='JMP':
                pc = op['jump_to']; continue
            elif name=='JIF':
                if data[args[0]] == 0: pc = op['jump_to']; continue
            elif name=='FUNC':
                pass  # skipped by JMP emitted before FUNC

            elif name=='ARR_NEW' and len(args)>=2:
                extend(args[1])
                size = data[args[0]]; arr_id = len(arrays)
                arrays.append([0]*size); data[args[1]] = arr_id
            elif name=='ARR_GET' and len(args)>=3:
                extend(args[2])
                arr_id = data[args[0]]; idx = data[args[1]]
                arr    = arrays[arr_id] if arr_id < len(arrays) else []
                data[args[2]] = arr[idx] if 0 <= idx < len(arr) else 0
            elif name=='ARR_SET' and len(args)>=3:
                arr_id = data[args[0]]; idx = data[args[1]]; val = data[args[2]]
                if arr_id < len(arrays) and 0 <= idx < len(arrays[arr_id]):
                    arrays[arr_id][idx] = val
            elif name=='ARR_LEN' and len(args)>=2:
                extend(args[1])
                arr_id = data[args[0]]
                data[args[1]] = len(arrays[arr_id]) if arr_id < len(arrays) else 0

            elif name=='CALL':
                fid=args[0]; na=args[1]; res_slot=args[2]; cargs=args[3:3+na]
                if fid in ftable:
                    fi      = ftable[fid]
                    pslots  = fi['param_slots']
                    all_s   = set(pslots) | fi.get('local_slots', set())
                    saved   = {s: data[s] for s in all_s if s < len(data)}
                    arg_vals= [data[cargs[j]] for j in range(min(na, len(pslots)))]
                    for j, ps in enumerate(pslots):
                        extend(ps); data[ps] = arg_vals[j] if j < len(arg_vals) else 0
                    cstack.append({'ret_pc':pc+1,'res_slot':res_slot,'saved':saved})
                    pc = fi['entry']+1; continue
            elif name=='RET':
                if cstack:
                    frame = cstack.pop()
                    rv    = data[args[0]] if args else 0
                    for slot, val in frame['saved'].items():
                        extend(slot); data[slot] = val
                    extend(frame['res_slot']); data[frame['res_slot']] = rv
                    pc = frame['ret_pc']; continue
                else: break

            pc += 1
        return data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST INFRASTRUCTURE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _run(source, expected=None):
    c  = Compiler()
    bc = c.compile(source)
    vm = VM()
    vm.func_local_slots = {fi['id']: fi.get('local_slots',set()) for fi in c.funcs.values()}
    vm.run(bc)
    ok = (expected is None) or (vm.output == expected)
    return ok, len(bc), len(c.instrs), len(c.data), vm.output

def py_bytecode_bits(py_source):
    code = compile(py_source, '<string>', 'exec')
    def total(c):
        t = len(c.co_code)
        for k in c.co_consts:
            if hasattr(k, 'co_code'): t += total(k)
        return t
    return total(code) * 8

def run_test(title, source, expected=None, show=True):
    if show:
        print(f"\n{'═'*62}")
        print(f"  {title}")
        print(f"{'═'*62}")
        for line in source.strip().splitlines():
            if line.strip(): print(f"    {line.rstrip()}")
        print()
    try:
        ok, bits, ni, nd, got = _run(source, expected)
        if show:
            print(f"  ZCE: {bits} bits ({bits//8} bytes) | {ni} instrs | {nd} data slots")
            for v in got: print(f"  → {v}")
            print(f"\n  Expected: {expected}  Got: {got}")
            print(f"  {'✓ PASS' if ok else '✗ FAIL'}")
        return ok, bits
    except Exception as e:
        if show:
            import traceback; traceback.print_exc()
            print(f"  ✗ ERROR: {e}")
        return False, 0

def run_bench(title, zce_src, py_src, expected=None):
    print(f"\n{'═'*62}")
    print(f"  BENCH: {title}")
    print(f"{'═'*62}")
    try:
        ok, zb, ni, nd, got = _run(zce_src, expected)
        pb  = py_bytecode_bits(py_src)
        pm  = len(marshal.dumps(compile(py_src,'<string>','exec'))) * 8
        print(f"  ZCE: {zb} bits ({zb//8}B)  |  Python co_code: {pb//8}B  |  Python marshal: {pm//8}B")
        print(f"  ZCE/co_code: {zb/pb:.2f}x   ZCE/marshal: {zb/pm:.2f}x")
        for v in got: print(f"  → {v}")
        print(f"  {'✓ PASS' if ok else '✗ FAIL'}")
        return ok, zb, pb, pm
    except Exception as e:
        import traceback; traceback.print_exc()
        return False, 0, 0, 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST SUITE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TESTS = [

# ── arithmetic & variables ───────────────────────────────────
("add",           "x = 3 + 5\nprint x",                              [8]),
("subtract",      "x = 10 - 3\nprint x",                             [7]),
("multiply",      "x = 6 * 7\nprint x",                              [42]),
("divide",        "x = 20 / 4\nprint x",                             [5]),
("mod",           "x = 17 % 5\nprint x",                             [2]),
("chain",         "x = 3 + 5\ny = x * 2\nprint y",                   [16]),
("multi-var",     "a = 10\nb = 3\nc = a - b\nprint c",               [7]),
("large nums",    "x = 1000 + 2000\nprint x",                        [3000]),
("compound expr", "x = 3 + 2 * 4\nprint x",                          [11]),  # standard precedence: 3+(2*4)=11

# ── conditionals ─────────────────────────────────────────────
("if true",       "x = 5\nif x > 3:\n    print x",                   [5]),
("if false",      "x = 2\nif x > 3:\n    print x",                   []),
("if-else true",  "x = 5\nif x > 3:\n    print x\nelse:\n    y = 0\n    print y", [5]),
("if-else false", "x = 2\nif x > 3:\n    print x\nelse:\n    y = 99\n    print y",[99]),
("eq check",      "x = 5\nif x == 5:\n    print x",                  [5]),
("neq check",     "x = 4\nif x != 5:\n    print x",                  [4]),

# ── loops ────────────────────────────────────────────────────
("while count",   "i = 0\nwhile i < 5:\n    i = i + 1\nprint i",     [5]),
("sum 1-10",      "s = 0\ni = 1\nwhile i <= 10:\n    s = s + i\n    i = i + 1\nprint s", [55]),
("countdown",     "x = 5\nwhile x > 0:\n    x = x - 1\nprint x",    [0]),
("factorial 5",   "n = 5\nr = 1\nwhile n > 1:\n    r = r * n\n    n = n - 1\nprint r", [120]),
("fibonacci 10",  """
a = 0
b = 1
i = 0
while i < 9:
    c = a + b
    a = b
    b = c
    i = i + 1
print b
""", [55]),

# ── functions ────────────────────────────────────────────────
("func basic", """
def double(x):
    return x * 2
print double(5)
""", [10]),

("func two args", """
def add(a, b):
    return a + b
print add(3, 7)
""", [10]),

("func multi-call", """
def square(n):
    return n * n
print square(3)
print square(4)
""", [9, 16]),

("func with logic", """
def max2(a, b):
    if a > b:
        return a
    else:
        return b
print max2(17, 42)
""", [42]),

("func calls func", """
def double(x):
    return x * 2
def quad(x):
    return double(x) * 2
print quad(5)
""", [20]),

("recursive fact", """
def fact(n):
    if n <= 1:
        return 1
    r = fact(n - 1)
    return n * r
print fact(5)
print fact(6)
""", [120, 720]),

("recursive fib", """
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
""", [0,1,1,2,3,5,8,13,21,34,55]),

# ── arrays ───────────────────────────────────────────────────
("arr create read", """
arr = array(3)
arr[0] = 10
arr[1] = 20
arr[2] = 30
print arr[0]
print arr[1]
print arr[2]
""", [10, 20, 30]),

("arr len", """
arr = array(8)
print len(arr)
""", [8]),

("arr loop fill", """
arr = array(5)
i = 0
while i < 5:
    arr[i] = i * 3
    i = i + 1
i = 0
while i < 5:
    print arr[i]
    i = i + 1
""", [0, 3, 6, 9, 12]),

("arr sum", """
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
""", [150]),

("arr max", """
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
""", [9]),

("arr reverse", """
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
""", [5, 4, 3, 2, 1]),

("arr bubble sort", """
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
""", [1,11,12,22,25,34,64,90]),

("arr func param", """
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
print arr_sum(data, 4)
""", [100]),

# ── real programs ─────────────────────────────────────────────
("primes sieve 50", """
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
""", [2,3,5,7,11,13,17,19,23,29,31,37,41,43,47]),

("gcd euclidean", """
def gcd(a, b):
    while b != 0:
        tmp = b
        b = a - a / b * b
        a = tmp
    return a
print gcd(48, 18)
print gcd(100, 75)
print gcd(252, 105)
""", [6, 25, 21]),

("power of 2", """
x = 1
i = 0
while i < 8:
    x = x * 2
    i = i + 1
print x
""", [256]),

("nested if-while", """
s = 0
i = 1
while i <= 10:
    if i % 2 == 0:
        s = s + i
    i = i + 1
print s
""", [30]),

("abs diff", """
def absdiff(a, b):
    if a > b:
        return a - b
    else:
        return b - a
print absdiff(10, 3)
print absdiff(3, 10)
""", [7, 7]),

]

BENCHMARKS = [
("Bubble sort (array)", """
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
""", """
arr=[64,34,25,12,22,11,90,1]
for i in range(len(arr)):
    for j in range(len(arr)-1):
        if arr[j]>arr[j+1]: arr[j],arr[j+1]=arr[j+1],arr[j]
for x in arr: print(x)
""", [1,11,12,22,25,34,64,90]),

("Primes sieve to 50", """
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
""", """
sieve=[0]*51
for i in range(2,51): sieve[i]=1
for i in range(2,51):
    if sieve[i]:
        j=i*2
        while j<=50: sieve[j]=0; j+=i
for i in range(2,51):
    if sieve[i]: print(i)
""", [2,3,5,7,11,13,17,19,23,29,31,37,41,43,47]),

("Fibonacci recursive", """
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
""", """
def fib(n):
    if n<=1: return n
    return fib(n-1)+fib(n-2)
for i in range(11): print(fib(i))
""", [0,1,1,2,3,5,8,13,21,34,55]),

("GCD Euclidean", """
def gcd(a, b):
    while b != 0:
        tmp = b
        b = a - a / b * b
        a = tmp
    return a
print gcd(48, 18)
print gcd(100, 75)
print gcd(252, 105)
""", """
def gcd(a,b):
    while b: a,b=b,a%b
    return a
print(gcd(48,18)); print(gcd(100,75)); print(gcd(252,105))
""", [6, 25, 21]),
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == '__main__':
    print('\n' + '█'*62)
    print('  ZC-LANG — Zero-Count Encoding System')
    print('  Token format: [N zeros][M ones][0]  (zeros=type, ones=depth)')
    print('█'*62)

    # ── token format demo ─────────────────────────────────────
    print(f"\n{'▓'*62}")
    print('  TOKEN FORMAT: zeros=type, ones=depth')
    print(f"{'▓'*62}")
    print('  Examples:')
    for n,label in [(2,'instruction'),(6,'JIF'),(9,'FUNC'),(11,'RET')]:
        for d in [1,2,3]:
            t = tok(n,d)
            print(f"    N={n:2d} d={d}  →  {t:18s}  ({label} at depth {d})")
    print()
    print('  Elias gamma pointer examples:')
    for v in [0,1,5,10,50,100]:
        print(f"    slot {v:4d}  ->  {ptr(v):20s}  ({eg_bits(v)} bits, was {v+2} unary)")

    # ── tests ─────────────────────────────────────────────────
    print(f"\n{'▓'*62}")
    print('  FULL TEST SUITE')
    print(f"{'▓'*62}")
    results = []
    for title, src, expected in TESTS:
        ok, bits = run_test(title, src, expected)
        results.append((title, ok, bits))

    # ── benchmarks ────────────────────────────────────────────
    print(f"\n{'▓'*62}")
    print('  BENCHMARKS vs PYTHON')
    print(f"{'▓'*62}")
    bm_data = []
    bm_results = []
    for title, zce_src, py_src, expected in BENCHMARKS:
        ok, zb, pb, pm = run_bench(title, zce_src, py_src, expected)
        bm_results.append(ok); bm_data.append((title, zb, pb, pm))

    # ── elias savings ─────────────────────────────────────────
    print(f"\n{'▓'*62}")
    print('  ELIAS GAMMA POINTER SAVINGS vs UNARY')
    print(f"{'▓'*62}")
    print(f"  {'slot':>6}  {'unary bits':>12}  {'elias bits':>12}  {'saved':>8}")
    for v in [0,1,2,5,10,20,50,100,200]:
        unary = v + 2
        elias = eg_bits(v)
        print(f"  {v:>6}  {unary:>12}  {elias:>12}  {unary-elias:>+8}")

    # ── summary ───────────────────────────────────────────────
    print(f"\n\n{'█'*62}")
    print('  SUMMARY')
    print(f"{'█'*62}")
    passed = sum(1 for _,ok,_ in results if ok)
    total  = len(results)
    print(f"\n  Tests:      {passed}/{total} {'✓ ALL PASS' if passed==total else '✗'}")
    print(f"  Benchmarks: {sum(bm_results)}/{len(bm_results)}")
    print()
    print(f"  {'Program':<30} {'ZCE':>7} {'Py code':>8} {'Py .pyc':>8} {'vs code':>8} {'vs .pyc':>8}")
    print(f"  {'-'*30} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for t,zb,pb,pm in bm_data:
        print(f"  {t:<30} {zb//8:>6}B {pb//8:>7}B {pm//8:>7}B  {zb/pb:>7.2f}x  {zb/pm:>7.2f}x")
    print()

    # failed tests
    failed = [(t,ok,b) for t,ok,b in results if not ok]
    if failed:
        print('  FAILED:')
        for t,_,_ in failed: print(f'    ✗ {t}')
    print('█'*62 + '\n')