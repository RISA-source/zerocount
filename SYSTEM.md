# Zero-Count Encoding System (ZCE)
### Version 2.0 — Full System Blueprint

---

## Origin

This system came from a single intuition: **what if zeros in a bitstream weren't data, but grammar?** Instead of treating every bit as carrying equal weight, what if the *count* of consecutive zeros told you how to read everything that follows — no lookup tables, no external schema, no format specification needed?

The idea had been circling for months without a clear form. Then in one session it crystallized: zeros as structural markers, ones as terminators, data completely separated. That intuition, developed from scratch, produced a complete self-describing binary encoding system, a bytecode format, a Turing-complete compiler, and a virtual machine. This document describes every part of it.

---

## Core Philosophy

In conventional binary, every bit position is equivalent — just a power of 2. In ZCE, bits have two completely different roles:

- **Zeros** are grammar. They are structural markers. They cost nothing computationally — the decoder just counts them.
- **Ones** terminate zero-sequences. A `1` always ends a grammar token.
- **Data** lives separately, completely isolated from grammar. It is raw and untouched.

The bitstream has three regions:

```
[ 16-bit header ] [ GRAMMAR SECTION ] [ DATA SECTION ]
```

The 16-bit header stores the exact length of the grammar section in bits. The grammar section describes structure, instructions, and references. The data section holds raw values. The two main sections never mix.

---

## The Grammar Token

The single primitive of the entire system is the **grammar token**:

```
N zeros followed by a terminating 1
```

The count N is the entire meaning. No table. No context needed. Just count the zeros, hit the 1, and you know what's coming next.

### Token Table

| Zero Count (N) | Meaning               | Paired? |
|----------------|-----------------------|---------|
| 1              | Value reference       | No      |
| 2              | Instruction           | No      |
| 3              | Unit open/close       | Yes     |
| 4              | Block open/close      | Yes     |
| 5              | Section open/close    | Yes     |
| 6              | Jump-if-false (JIF)   | No      |
| 7              | Document open/close   | Yes     |
| 8              | Unconditional jump    | No      |

**Paired tokens** work like HTML tags — open and close by context. First N=3 opens a unit, second N=3 closes it. Tracked by a simple stack.

---

## The Header

The bitstream starts with a **16-bit unsigned integer** representing the exact length of the grammar section in bits:

```
[ 16 bits: grammar length ] [ grammar bits ] [ data bits ]
```

The decoder reads the first 16 bits, knows exactly where the grammar ends and data begins, and never needs to scan for a separator pattern. This approach was chosen after an earlier pattern-based separator caused collisions with jump tokens. See BUILDLOG.md for details.

---

## Unary Pointers

When the decoder encounters N=1 (value reference) or a jump token, it reads a **unary pointer**. A unary pointer encodes a data or instruction index as:

```
(index + 1) ones, followed by a zero
```

Examples: `data[0]` → `10`, `data[1]` → `110`, `data[4]` → `111110`

Decoding: count ones until zero. Count minus 1 = index.

Unary pointers contain **no zeros except the final terminator**, so they can never be misread as grammar tokens.

---

## Instructions

When the decoder sees N=2, it reads the next token's zero-count as the instruction code:

| N  | Operation | What it does                         |
|----|-----------|--------------------------------------|
| 1  | ADD       | data[a] + data[b] → data[c]         |
| 2  | SUB       | data[a] - data[b] → data[c]         |
| 3  | MUL       | data[a] * data[b] → data[c]         |
| 4  | DIV       | data[a] / data[b] → data[c]         |
| 5  | LOAD      | data[a] → data[b]                   |
| 6  | PRINT     | output data[a]                       |
| 7  | CMP_LT    | (data[a] < data[b]) → data[c]      |
| 8  | CMP_GT    | (data[a] > data[b]) → data[c]      |
| 9  | CMP_EQ    | (data[a] == data[b]) → data[c]     |
| 10 | CMP_NEQ   | (data[a] != data[b]) → data[c]     |
| 11 | CMP_LTE   | (data[a] <= data[b]) → data[c]     |
| 12 | CMP_GTE   | (data[a] >= data[b]) → data[c]     |

No opcode table anywhere. The count of zeros IS the operation.

---

## Jump Instructions

Jumps have their own grammar token IDs and do not use the N=2 wrapper.

**Jump-if-false (N=6):** `[N=6] [uptr: cond data idx] [uptr: instr target]`
If `data[cond] == 0`, jump to instruction at target. Used for if/else and while.

**Unconditional jump (N=8):** `[N=8] [uptr: instr target]`
Always jump. Used to skip else branches and loop back to while condition.

Targets are **instruction indices**, not bit offsets. The compiler emits JIF/JMP with placeholder targets (0) and **patches** them once the body is fully compiled and the real target is known.

---

## The Data Section

A flat array of **16-bit unsigned integers** (0–65535). Each stored as 16 raw bits. No encoding, no escaping.

The data array is the program's entire memory — no registers, no stack. A snapshot of the data array at any point is a complete picture of program state.

---

## Worked Example — `x = 3 + 5; print x`

**Compiled instruction list:**
```
0: ADD   data[0], data[1] → data[2]   (3+5 → result)
1: LOAD  data[2] → data[3]            (result → x)
2: PRINT data[3]                       (print x)
```

**Data section:**
```
data[0]=3  data[1]=5  data[2]=0(result)  data[3]=0(x)
```

**Execution:** ADD produces 8 in data[2], LOAD copies to data[3], PRINT outputs 8.

---

## Worked Example — while loop

```
i = 1
s = 0
while i <= 10:
    s = s + i
    i = i + 1
print s
```

**Instruction list with jumps:**
```
0:  LOAD lit(1) → i
1:  LOAD lit(0) → s
2:  CMP_LTE i, lit(10) → cond     ← loop top
3:  JIF cond → 10                  ← exit if false
4:  ADD s, i → tmp
5:  LOAD tmp → s
6:  ADD i, lit(1) → tmp
7:  LOAD tmp → i
8:  JMP → 2                        ← back to loop top
9:  PRINT s                        ← after loop
```

The compiler emits JIF at step 3 with target=0, compiles the body (steps 4-8), emits JMP back to 2, then patches JIF's target to 9 (after loop).

---

## Decoder Algorithm

```
read 16 bits → grammar_length
grammar = bitstream[16 : 16+grammar_length]
data    = bitstream[16+grammar_length:]  (16-bit chunks)

pos = 0
while pos < len(grammar):
    count zeros → N
    consume terminating 1

    N=7 → toggle document context
    N=5 → toggle section context
    N=4 → toggle block context; on close: commit instruction
    N=3 → toggle unit context
    N=8 → read uptr → emit JMP
    N=6 → read uptr(cond) + uptr(target) → emit JIF
    N=2 → read next N → instruction name
    N=1 → read uptr → append data index to current instruction
```

---

## Compiler Pipeline

```
Source (ZC-Lang)
  → Line scanner (strips comments/blanks)
  → Statement parser (assign / print / if / while)
  → Expression compiler (arithmetic → data slots + instructions)
  → Condition compiler (comparisons → CMP instruction + result slot)
  → Block finder (indentation-based sub-block extraction)
  → Jump emitter + forward patcher
  → Encoder (wrap in block/unit tokens, prepend header, append data)
ZCE Bitstream
  → Decoder (header → grammar → data)
  → VM (PC-based executor over instruction list)
Output
```

---

## VM

```
pc = 0
while pc < len(ops):
    execute ops[pc]
    JMP/JIF: set pc = target, continue
    else: pc += 1
```

Max step limit prevents infinite loops in runaway programs.

---

## ZC-Lang v2 Language

```python
# arithmetic
x = 3 + 5
y = x * 2
z = y / 4

# conditionals
if x > 5:
    result = 1
else:
    result = 0

# loops
while i < 10:
    i = i + 1

# output
print x
```

**Operators:** `+` `-` `*` `/` `==` `!=` `<` `>` `<=` `>=`
**Data:** 16-bit unsigned integers (0–65535)
**Limits:** one operator per expression, no functions, no strings

---

## Test Results

23/23 tests passing including: factorial(5)=120, fibonacci(10th)=55, sum 1-10=55, power of 2 loop (2^8=256), nested if-in-while, absolute difference, max of two numbers.

---

## What Makes This Different

| Property | ZCE v2 | JVM | x86 | Protobuf |
|----------|--------|-----|-----|----------|
| Self-describing | Yes | No | No | No |
| Needs external spec | No | Yes | Yes | Yes |
| Decoder size | ~80 lines | Thousands | Millions | Hundreds |
| Table-free grammar | Yes | No | No | No |
| Turing complete | Yes | Yes | Yes | No |

---

## Limitations (V2)

- One operator per expression
- No functions
- No strings or floats
- No negative literals
- Unary pointers grow linearly (data[100] = 101 bits)
- Overhead large at small scale

---

## Planned (V3)

- Functions (N=9 token)
- Elias-coded pointers (compact large indices)
- 32-bit data
- Negative numbers
- Compound expressions

---

## Files

| File | Description |
|------|-------------|
| `zerocount.py` | Core encoder/decoder v1 |
| `zclang_v2.py` | Full compiler + VM, 23 tests |
| `SYSTEM.md` | This document |
| `BUILDLOG.md` | Development log, bugs, decisions |

---

## Summary

Zero-Count Encoding is a binary format where consecutive zeros are the only grammar primitive, the count determines meaning without any table, data is separated from grammar via a length header, and any decoder knowing the token rules can read any ZCE bitstream without external specification.

Built in one day from a raw intuition about bits.

---

*ZCE v2 — conceived and developed March 1, 2026*