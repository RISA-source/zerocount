# Zero-Count Encoding System (ZCE)
### Current Version — Full System Blueprint

---

## Origin

This system came from a single intuition: what if zeros in a bitstream were not data, but grammar? Instead of treating every bit as carrying equal weight, what if the *count* of consecutive zeros told you how to read everything that follows — no lookup tables, no external schema, no format specification needed?

That intuition, developed from scratch, produced a complete self-describing binary encoding, a bytecode format, a Turing-complete compiler with functions and arrays, and a virtual machine. The decoder is around 60 lines. It has no dependencies. It needs no external specification.

---

## Core Design: Two Dimensions in Every Token

Every grammar token carries two independent pieces of information:

```
[ N zeros ] [ M ones ] [ 0 terminator ]
```

- **N zeros** — the token *type*. What this token is.
- **M ones** — the tree *depth*. Where in the tree it lives.
- **0** — the terminator. One bit. Always present.

The decoder loop is three steps:

```
count zeros  → token type
count ones   → tree depth
read 0       → token complete
```

Nothing else. No tables. No context tracking. No paired open/close tokens. Every token is self-contained.

---

## Why This Is Better Than Paired Tokens

Previous versions of ZCE used paired open/close tokens — like HTML tags. A block opened, content followed, the block closed. Tree structure had to be *reconstructed* from matched pairs.

The current design eliminates this entirely. Every token declares its own position in the tree directly. The tree structure is not reconstructed — it is read. A token at depth 3 inside a token at depth 2 inside a token at depth 1 tells you the entire nesting in three tokens with no bookkeeping.

Benefits:

- **Scoping** — variables are naturally scoped to their depth. Look up a name at current depth first, walk up toward root.
- **Functions as depth transitions** — calling enters a deeper depth. Returning steps up. The call frame shape is in the bitstream.
- **Random access** — jump to any bit offset, read one token, know your tree position immediately.
- **Error recovery** — resync by finding a valid depth transition, not a matched-pair boundary.
- **Expression trees natively** — operator at depth N, operands at depth N+1. Tree is in the bitstream directly.

---

## Token Type Table

| N (zero count) | Token type | Role |
|---|---|---|
| 2 | Instruction | Followed by instruction code token |
| 6 | JIF | Jump-if-false: Elias(cond slot) + Elias(target) |
| 7 | Document | Envelope token |
| 5 | Section | Envelope token |
| 8 | JMP | Unconditional jump: Elias(target) |
| 9 | FUNC | Function entry: Elias(fid) + Elias(nparams) + Elias(slots...) |
| 10 | CALL | Function call: Elias(fid, nargs, result, args...) |
| 11 | RET | Return: Elias(return slot) |
| 12 | Array op | Followed by array op code token |

---

## Instruction Codes

| N | Instruction | Operation |
|---|---|---|
| 1 | ADD | a + b → c |
| 2 | SUB | a - b → c |
| 3 | MUL | a * b → c |
| 4 | DIV | a / b → c (integer) |
| 5 | MOD | a % b → c |
| 6 | LOAD | a → b |
| 7 | PRINT | output a |
| 8–13 | CMP_LT/GT/EQ/NEQ/LTE/GTE | comparison → 0 or 1 |

## Array Operation Codes

| N | Operation |
|---|---|
| 1 | ARR_NEW size→result |
| 2 | ARR_GET arr,idx→result |
| 3 | ARR_SET arr,idx,val |
| 4 | ARR_LEN arr→result |

---

## Depth=0: The Pointer Escape

**M=0** (zero ones in a token) is the pointer escape. It does not represent a tree position. It signals: this token carries a data slot reference encoded in Elias gamma.

This elegantly resolves a collision: previously, value pointers used all-ones-then-zero sequences — which looked exactly like depth tokens. By making depth=0 mean "not a depth token, it's a pointer", the collision disappears and the pointer scaling problem is solved simultaneously.

---

## Elias Gamma Coding

All pointers and data values use Elias gamma coding. Encode V≥0 as Elias(V+1):

```
k = floor(log2(V+1))
emit k zeros, then (V+1) in binary (k+1 bits total after zeros)
```

Self-delimiting: the decoder counts leading zeros to know how many value bits to read.

| Value | Elias bits | Previous unary bits | Saved |
|---|---|---|---|
| 0 | 1 | 2 | +1 |
| 1 | 3 | 3 | 0 |
| 5 | 5 | 7 | +2 |
| 10 | 7 | 12 | +5 |
| 50 | 11 | 52 | +41 |
| 100 | 13 | 102 | +89 |
| 200 | 15 | 202 | +187 |

For programs with many data slots — like bubble sort with its array index arithmetic — this is a major win. Slot reference costs grow logarithmically instead of linearly.

---

## Bitstream Format

```
[ 16-bit grammar length ] [ 16-bit data count ] [ grammar bits ] [ Elias-coded data values ]
```

The decoder reads the grammar length, extracts exactly that many bits as grammar, then reads exactly data_count Elias gamma values as the data section. No pattern scanning. No separator search. The format is entirely self-specified from the first 32 bits.

---

## ZC-Lang Language Reference

### Syntax

```python
# Comments
x = 3 + 5          # assignment + arithmetic
y = x * 2          # variable reference
z = y % 3          # modulo

if x > 5:          # conditional (all 6 comparisons: < > == != <= >=)
    y = 1
else:
    y = 0

while i < 10:      # loop
    i = i + 1

print x            # output

arr = array(10)    # create array of 10 zeros
arr[i] = val       # set element
x = arr[i]         # get element
n = len(arr)       # length

def add(a, b):     # function definition
    return a + b

result = add(3, 5) # function call
```

### Operators

Arithmetic: `+` `-` `*` `/` `%`
Comparison: `==` `!=` `<` `>` `<=` `>=`
Precedence: `*` `/` `%` before `+` `-` (standard)

### Data types

16-bit unsigned integers (0–65535). Arrays of 16-bit integers.

### Limitations (current)

- No strings
- No floats
- No negative literals (subtraction works)
- One binary operator per sub-expression (no `a + b + c` — use temp vars)

---

## Compiler Architecture

```
Source text (ZC-Lang)
  → Line scanner (strip comments, blank lines)
  → Statement parser (assign / array-assign / print / if / while / return)
  → Expression compiler (operator-precedence, paren-aware, left-to-right)
  → Condition compiler (comparison → CMP instruction)
  → Block finder (indentation-based sub-block extraction)
  → Function compiler (two-pass: register all defs, then compile call sites)
  → Jump patcher (forward-patch JIF/JMP targets after body compilation)
  → Encoder (token format + Elias data)
ZCE Bitstream
  → Decoder (three-step loop: count zeros, count ones, read 0)
  → VM (PC-based executor with depth-aware call frames)
Output
```

---

## Virtual Machine

PC-based loop over decoded instruction list. Depth-aware call frames. Arrays in a separate runtime table referenced by integer ID.

```
for each instruction:
  arithmetic:   data[c] = data[a] OP data[b]
  LOAD:         data[b] = data[a]
  PRINT:        output data[a]
  comparisons:  data[c] = 1 if condition else 0
  JMP:          pc = target
  JIF:          if data[cond]==0: pc = target
  FUNC:         no-op (skipped by JMP emitted before)
  CALL:         save local slots, load args, push frame, jump to entry
  RET:          pop frame, restore locals, write return value, return to caller
  ARR_NEW:      arrays.append([0]*size); data[result] = arr_id
  ARR_GET:      data[result] = arrays[arr_id][idx]
  ARR_SET:      arrays[arr_id][idx] = data[val]
  ARR_LEN:      data[result] = len(arrays[arr_id])
```

---

## Benchmark Results

Comparing ZCE against Python's raw bytecode (`co_code`) and full `.pyc` marshal format.

| Program | ZCE | Python co_code | Python .pyc | vs co_code | vs .pyc |
|---|---|---|---|---|---|
| Bubble sort (array) | 233B | 200B | 490B | 1.17x | **0.48x** |
| Primes sieve to 50 | 161B | 204B | 476B | **0.79x** | **0.34x** |
| Fibonacci recursive | 80B | 138B | 405B | **0.58x** | **0.20x** |
| GCD Euclidean | 74B | 130B | 403B | **0.57x** | **0.18x** |

ZCE beats Python's pure bytecode (co_code) on three of four programs. GCD and Fibonacci are 40–43% smaller than Python's raw instruction bytes. Bubble sort is still larger because array indexing generates many intermediate slot references — slot indices in the 30–50 range cost more than Python's compact BINARY_SUBSCR. This gap closes as slot index density is reduced (e.g. loop unrolling, better register allocation in the compiler).

Against full `.pyc` (the fair comparison — what you'd actually deploy), ZCE is 2–5x smaller on every program.

---

## Test Results

40/40 tests passing across:

- Arithmetic and variables (add, sub, mul, div, mod, chain, compound)
- Conditionals (if/else, all 6 comparisons)
- Loops (while, for-style, nested)
- Functions (basic, multi-arg, multi-call, recursive factorial, recursive fibonacci)
- Arrays (create, read, write, len, fill loop, sum, max, reverse, bubble sort, array-param function)
- Real programs (primes sieve, GCD, power of 2, nested if-in-while, absolute difference)

---

## What Makes This Different

| Property | ZCE | JVM bytecode | x86 | Protobuf |
|---|---|---|---|---|
| Self-describing grammar | Yes — fully | No | No | No |
| Needs external spec | No | Yes | Yes | Yes |
| Token decodes in 3 steps | Yes | No | No | No |
| Tree structure intrinsic | Yes | No | No | No |
| Smaller than Python .pyc | Yes (2–5x) | Comparable | Much smaller | N/A |
| Decoder size | ~60 lines | Thousands | Millions | Hundreds |

---

## Current Limitations

- No strings or floats
- No negative literals
- 16-bit integer ceiling (65535)
- Single operator per sub-expression
- No closures or higher-order functions (planned)
- Self-modifying grammar not yet implemented

---

## Planned Extensions

1. **32-bit integers** — lift the 65535 ceiling
2. **Strings** — fixed-length and null-terminated in data section
3. **Floats** — IEEE 754 in data section, mode-shifted interpretation
4. **Negative literals** — sign bit in 16/32-bit representation
5. **Compound expressions** — expression tree natively via depth (operator at N, operands at N+1)
6. **Self-modifying grammar** — reflection token redefines subsequent token types inline
7. **C embedding API** — 100-line C decoder, no dependencies, embedded deployment

---

## Files

| File | Description |
|---|---|
| `zclang.py` | Compiler + VM + full test suite (single file) |
| `SYSTEM.md` | This document |
| `BUILDLOG.md` | Full development history, every decision and bug |

---

## Summary

Zero-Count Encoding is a binary format where:

1. The count of consecutive zeros is the token type — no tables
2. The count of consecutive ones is the tree depth — no paired tokens
3. Depth=0 is a pointer escape — Elias gamma references to data slots
4. All values use Elias gamma — logarithmic cost instead of linear
5. Data is separated from grammar — no ambiguity possible
6. Any decoder knowing the three-step rule can read any ZCE bitstream

Built from one intuition about zeros. Smaller than Python bytecode on real programs. Decoder fits in 60 lines.

---

*ZCE — conceived March 1, 2026. Current format established March 2, 2026.*