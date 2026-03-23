## SAT-ATPG Project README (Updated)

### 1) Project Overview

This project implements a **SAT-based Automatic Test Pattern Generation (ATPG)** flow for combinational circuits, with:

- A robust **baseline SAT-ATPG engine** (PySAT + MiniSat22),
- Support for **two netlist formats**:
  - ISCAS-style gate-level Verilog (`.v`)
  - Yosys JSON netlists (`.json`)
- Benchmark evaluation scripts for coverage/time/conflicts/decisions,
- A separate **LLM-guided ATPG comparison module** to evaluate guided assumptions vs baseline.

The code is designed to support assignment goals:
- Fault modeling with good/faulty copies + miter,
- Fault-level SAT metrics,
- Baseline vs guided comparison using measurable solver statistics.

---

### 2) What Is Implemented So Far

#### Baseline SAT-ATPG
- Gate-level CNF encoder for:
  - `AND`, `OR`, `NOT`, `BUF`, `NAND`, `NOR`, `XOR`
- Correct stuck-at fault injection (`sa0`, `sa1`)
- Correct fault-site handling in faulty copy:
  - Fault site is disconnected from original driver in faulty copy
- Multi-output miter:
  - Detects if **any output differs** between good and faulty copies
- Per-fault SAT solve and aggregation metrics:
  - detected/total faults
  - SAT time / wall time
  - average decisions / average conflicts

#### Netlist Parsing
- `parse_iscas_verilog(...)`:
  - Supports multiline `input`/`output` declarations
  - Parses ISCAS primitive gate instances
- `parse_yosys_json(...)`:
  - Parses Yosys JSON modules/ports/cells
  - Normalizes common internal + tech-mapped gate names
- `parse_netlist(...)` auto-selects parser from extension

#### Benchmark Runner
- Full benchmark suite execution
- Quick mode and custom benchmark selection
- Progress display + CSV export

#### LLM-Guided Comparison
- Separate module (`sat_atpg_llm_demo.py`) for clean A/B evaluation
- Guidance via assumptions + fallback
- Selective guidance on hard faults only
- Threshold and assumption budget tuning
- Baseline vs guided metrics + CSV export

---

### 3) Repository Structure

```text
sat_atpg_project/
├── Benchmarks/                    # ISCAS and other benchmark netlists
├── src/
│   ├── cnf_encoder.py             # CNF encodings + miter
│   ├── sat_atpg_demo.py           # Baseline ATPG engine + parsers
│   ├── benchmark_runner.py        # Baseline benchmark suite runner
│   ├── sat_atpg_llm_demo.py       # Baseline vs LLM-guided comparison
│   └── toy_demo.py                # Tiny sanity demo
├── benchmark_metrics.csv          # Baseline benchmark output CSV
└── llm_compare_metrics*.csv       # Guided comparison CSVs
```

---

### 4) Requirements

- Python 3.10+
- `python-sat` (PySAT)

Install dependency:
```bash
pip install python-sat
```

Optional (for OpenAI-guided mode):
- `OPENAI_API_KEY` environment variable
- (optional) `OPENAI_MODEL` (default used in code if unset)

---

### 5) How to Run (Do Not Run All at Once)

> Run commands from **project root** unless specified.

---

## A) Baseline ATPG Commands

### A1. Single-fault debug run
Runs one chosen fault with printed PI assignment and output diff:

```bash
python src/sat_atpg_demo.py
```

Use this when you want detailed debugging for one fault instance.

---

### A2. Baseline benchmark quick run (recommended while developing)
Skips heavy circuits (`c3540`, `c6288`):

```bash
python -u src/benchmark_runner.py --quick
```

Good for fast validation of correctness/performance changes.

---

### A3. Baseline full benchmark run
Runs full configured suite (can take long due to large circuits):

```bash
python -u src/benchmark_runner.py
```

Use for final baseline metrics.

---

### A4. Run selected baseline benchmarks only
Example:

```bash
python -u src/benchmark_runner.py --bench c432.v --bench c880.v
```

Useful for targeted debugging.

---

### A5. Write baseline CSV to custom path
```bash
python -u src/benchmark_runner.py --quick --csv benchmark_metrics.csv
```

---

## B) LLM-Guided Comparison Commands

### B1. Compare baseline vs guided on one benchmark (heuristic guidance)
```bash
python src/sat_atpg_llm_demo.py --bench c432.v --llm-mode heuristic --fault-limit 200
```

Use for quick A/B sanity checks.

---

### B2. Compare on full fault set for one benchmark
```bash
python src/sat_atpg_llm_demo.py --bench c499.v --llm-mode heuristic --fault-limit 486
```

(or omit `--fault-limit` to use full for single benchmark)

---

### B3. Suite comparison with automatic heavy-benchmark cap
This runs all suite benchmarks with:
- full faults for normal circuits
- `--big-limit` for `c3540` and `c6288`

```bash
python src/sat_atpg_llm_demo.py \
  --suite \
  --llm-mode heuristic \
  --big-limit 1200 \
  --hard-conflicts 100 \
  --hard-decisions 800 \
  --max-assumptions 4 \
  --csv llm_compare_metrics_final.csv
```

This is the recommended “final report” command.

---

### B4. Force one global fault limit for all suite benchmarks
```bash
python src/sat_atpg_llm_demo.py \
  --suite \
  --fault-limit 500 \
  --llm-mode heuristic \
  --csv llm_compare_metrics.csv
```

Useful for quick comparative sweeps.

---

### B5. OpenAI-guided mode (optional)
```bash
export OPENAI_API_KEY="YOUR_KEY"
export OPENAI_MODEL="gpt-4o-mini"

python src/sat_atpg_llm_demo.py \
  --bench c432.v \
  --llm-mode openai \
  --fault-limit 300
```

If API fails, code safely falls back to heuristic guidance.

---

## C) Toy Sanity Demo

```bash
python src/toy_demo.py
```

Tiny circuit sanity check to validate SAT setup quickly.

---

### 6) Output Files

- **Baseline metrics CSV**:
  - `benchmark_metrics.csv` (or custom via `--csv` in benchmark runner)
- **Guided comparison CSV**:
  - `llm_compare_metrics.csv` / `llm_compare_metrics_final.csv` (as specified)

---

### 7) Interpreting Metrics

For both baseline and guided runs, key metrics are:

- `detected / total faults` → fault coverage
- `SAT time` → solver effort time
- `avg decisions` → branching complexity
- `avg conflicts` → search difficulty

For guided runs also monitor:
- `guided_fallback_count` (high means hints over-constrain)
- `guided_invocations/skipped` (selective gating behavior)
- `avg_unsat_core_size` (assumption conflict signal)

---

### 8) Known Runtime Behavior

- `c3540` and `c6288` are heavy and can dominate runtime.
- If development loop is slow, use:
  - `--quick` in baseline runner
  - suite with `--big-limit 1200` in guided runner

---

### 9) Current Assignment Alignment Status

✅ SAT ATPG baseline implemented  
✅ Metrics collection implemented  
✅ ISCAS Verilog netlists supported  
✅ Yosys JSON netlists supported  
✅ Guided-vs-baseline comparison framework implemented  
✅ Selective guidance to reduce regressions on easy faults  

🔜 Next improvement opportunities:
- richer LLM prompt context (local cones/path constraints),
- smarter assumption generation (not fixed zeros),
- per-fault CSV logs for deeper statistical tests.
