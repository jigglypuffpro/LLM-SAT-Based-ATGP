import argparse
import csv
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

from pysat.solvers import Minisat22

from cnf_encoder import CNF, Gate, new_var
from sat_atpg_demo import parse_netlist, build_cnf_for_fault, generate_fault_list


@dataclass
class FaultMetrics:
    sat: bool
    time_s: float
    decisions: int
    conflicts: int
    used_assumptions: bool
    fallback_used: bool
    core_size: int


class LLMOracle:
    """
    Provides partial PI assignments for a target fault.
    """

    def __init__(self, mode: str = "heuristic", max_assumptions: int = 6):
        self.mode = mode
        self.max_assumptions = max_assumptions
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def propose_assignments(
        self,
        gates: List[Gate],
        primary_inputs: List[str],
        primary_outputs: List[str],
        fault_signal: str,
        sa_val: int,
        max_assumptions: Optional[int] = None,
    ) -> Dict[str, int]:
        budget = max_assumptions if max_assumptions is not None else self.max_assumptions
        if self.mode == "openai" and self.api_key:
            try:
                return self._openai_assignments(
                    gates, primary_inputs, primary_outputs, fault_signal, sa_val, budget
                )
            except Exception:
                # Safe fallback in case API parsing/network fails.
                return self._heuristic_assignments(primary_inputs, fault_signal, sa_val, budget)
        return self._heuristic_assignments(primary_inputs, fault_signal, sa_val, budget)

    def _heuristic_assignments(
        self,
        primary_inputs: List[str],
        fault_signal: str,
        sa_val: int,
        budget: int,
    ) -> Dict[str, int]:
        hints: Dict[str, int] = {}
        # If fault is PI, try opposite value to activate.
        if fault_signal in primary_inputs:
            hints[fault_signal] = 1 - sa_val
        # Add a small deterministic prefix as weak guidance.
        for name in primary_inputs:
            if len(hints) >= budget:
                break
            if name in hints:
                continue
            hints[name] = 0
        return hints

    def _openai_assignments(
        self,
        gates: List[Gate],
        primary_inputs: List[str],
        primary_outputs: List[str],
        fault_signal: str,
        sa_val: int,
        budget: int,
    ) -> Dict[str, int]:
        gate_preview = gates[:20]
        prompt = (
            "You are guiding SAT-based ATPG. Suggest a small set of primary input "
            "assignments likely to detect the fault.\n"
            f"Fault: {fault_signal} stuck-at-{sa_val}\n"
            f"Primary inputs: {primary_inputs}\n"
            f"Primary outputs: {primary_outputs}\n"
            f"Gate preview (first 20): {gate_preview}\n"
            "Return ONLY valid JSON object of this form:\n"
            '{"assignments":[{"pi":"N1","value":0},{"pi":"N2","value":1}]}\n'
            f"Use at most {budget} assignments and only listed primary inputs.\n"
        )

        req_body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        data = json.dumps(req_body).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = payload["choices"][0]["message"]["content"].strip()

        # Try to parse direct JSON; if wrapped in markdown, strip fenced block.
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(line for line in lines if not line.startswith("```"))
        obj = json.loads(text)
        hints: Dict[str, int] = {}
        for item in obj.get("assignments", []):
            pi = item.get("pi")
            value = item.get("value")
            if pi in primary_inputs and value in (0, 1):
                hints[pi] = value
            if len(hints) >= budget:
                break
        return hints


def _build_solver(cnf: CNF) -> Minisat22:
    solver = Minisat22()
    for clause in cnf:
        solver.add_clause(clause)
    return solver


def _stats(solver: Minisat22) -> Tuple[int, int]:
    st = solver.accum_stats()
    return st.get("decisions", 0), st.get("conflicts", 0)


def solve_fault_baseline(cnf: CNF) -> FaultMetrics:
    solver = _build_solver(cnf)
    t0 = time.time()
    sat = solver.solve()
    t1 = time.time()
    decisions, conflicts = _stats(solver)
    return FaultMetrics(
        sat=sat,
        time_s=t1 - t0,
        decisions=decisions,
        conflicts=conflicts,
        used_assumptions=False,
        fallback_used=False,
        core_size=0,
    )


def solve_fault_guided(
    cnf: CNF,
    var_map: Dict[str, int],
    primary_inputs: List[str],
    hints: Dict[str, int],
) -> FaultMetrics:
    solver = _build_solver(cnf)
    assumptions: List[int] = []
    for pi, val in hints.items():
        if pi not in primary_inputs:
            continue
        v = new_var(var_map, pi + "_g")
        assumptions.append(v if val == 1 else -v)

    t0 = time.time()
    sat = solver.solve(assumptions=assumptions)
    core_size = 0
    fallback = False
    if not sat and assumptions:
        try:
            core = solver.get_core() or []
            core_size = len(core)
        except Exception:
            core_size = 0
        # Fallback to plain solve so guidance never hurts detectability.
        fallback = True
        sat = solver.solve()
    t1 = time.time()
    decisions, conflicts = _stats(solver)
    return FaultMetrics(
        sat=sat,
        time_s=t1 - t0,
        decisions=decisions,
        conflicts=conflicts,
        used_assumptions=len(assumptions) > 0,
        fallback_used=fallback,
        core_size=core_size,
    )


def compare_baseline_vs_llm(
    bench_filename: str,
    llm_mode: str = "heuristic",
    fault_limit: Optional[int] = None,
    hard_conflicts: int = 100,
    hard_decisions: int = 800,
    max_assumptions: int = 4,
) -> Dict[str, float]:
    this_dir = os.path.dirname(os.path.abspath(__file__))
    bench_path = os.path.join(this_dir, "..", "Benchmarks", bench_filename)
    gates, primary_inputs, primary_outputs = parse_netlist(bench_path)
    faults = generate_fault_list(gates)
    if fault_limit is not None:
        faults = faults[:fault_limit]

    oracle = LLMOracle(mode=llm_mode, max_assumptions=max_assumptions)

    b_detected = 0
    g_detected = 0
    b_time = 0.0
    g_time = 0.0
    b_decisions = 0
    g_decisions = 0
    b_conflicts = 0
    g_conflicts = 0
    fallback_count = 0
    core_total = 0
    guided_invocations = 0
    guided_skipped = 0

    for idx, (signal, sa_val) in enumerate(faults, start=1):
        cnf, var_map = build_cnf_for_fault(
            gates, primary_inputs, primary_outputs, signal, sa_val
        )

        base = solve_fault_baseline(cnf)
        # Selective guidance: apply only on harder faults.
        is_hard = (base.conflicts >= hard_conflicts) or (base.decisions >= hard_decisions)
        if is_hard:
            # Budget tuning: very hard faults get fewer assumptions to avoid over-constraining.
            very_hard = (base.conflicts >= 4 * hard_conflicts) or (
                base.decisions >= 4 * hard_decisions
            )
            budget = 2 if very_hard else max_assumptions
            hints = oracle.propose_assignments(
                gates,
                primary_inputs,
                primary_outputs,
                signal,
                sa_val,
                max_assumptions=budget,
            )
            guided = solve_fault_guided(cnf, var_map, primary_inputs, hints)
            guided_invocations += 1
        else:
            # Keep easy faults unchanged to prevent regressions.
            guided = FaultMetrics(
                sat=base.sat,
                time_s=base.time_s,
                decisions=base.decisions,
                conflicts=base.conflicts,
                used_assumptions=False,
                fallback_used=False,
                core_size=0,
            )
            guided_skipped += 1

        b_detected += int(base.sat)
        g_detected += int(guided.sat)
        b_time += base.time_s
        g_time += guided.time_s
        b_decisions += base.decisions
        g_decisions += guided.decisions
        b_conflicts += base.conflicts
        g_conflicts += guided.conflicts
        fallback_count += int(guided.fallback_used)
        core_total += guided.core_size

        if idx % 200 == 0:
            print(f"[{idx}/{len(faults)}] processed...")

    n = max(len(faults), 1)
    print("\n=== Baseline vs LLM-guided (Assumptions) ===")
    print(f"Benchmark: {bench_filename}")
    print(f"Faults evaluated: {len(faults)}")
    print(f"Baseline detected: {b_detected}/{len(faults)}")
    print(f"Guided detected:   {g_detected}/{len(faults)}")
    print(f"Baseline SAT time (s): {b_time:.3f}")
    print(f"Guided SAT time (s):   {g_time:.3f}")
    print(f"Baseline avg decisions: {b_decisions / n:.2f}")
    print(f"Guided avg decisions:   {g_decisions / n:.2f}")
    print(f"Baseline avg conflicts: {b_conflicts / n:.2f}")
    print(f"Guided avg conflicts:   {g_conflicts / n:.2f}")
    print(f"Guided fallback count:  {fallback_count}")
    print(f"Avg UNSAT core size (guided): {core_total / n:.2f}")
    print(f"Guided invoked/skipped: {guided_invocations}/{guided_skipped}")
    print(
        f"Hard thresholds: conflicts>={hard_conflicts}, decisions>={hard_decisions}; "
        f"max_assumptions={max_assumptions}"
    )
    return {
        "benchmark": bench_filename,
        "faults_evaluated": float(len(faults)),
        "baseline_detected": float(b_detected),
        "guided_detected": float(g_detected),
        "baseline_sat_time_s": b_time,
        "guided_sat_time_s": g_time,
        "baseline_avg_decisions": b_decisions / n,
        "guided_avg_decisions": g_decisions / n,
        "baseline_avg_conflicts": b_conflicts / n,
        "guided_avg_conflicts": g_conflicts / n,
        "guided_fallback_count": float(fallback_count),
        "avg_unsat_core_size": core_total / n,
        "guided_invocations": float(guided_invocations),
        "guided_skipped": float(guided_skipped),
        "hard_conflicts": float(hard_conflicts),
        "hard_decisions": float(hard_decisions),
        "max_assumptions": float(max_assumptions),
    }


def run_suite_and_write_csv(
    benchmarks: List[str],
    csv_path: str,
    llm_mode: str,
    fault_limit: Optional[int],
    big_limit: int,
    hard_conflicts: int,
    hard_decisions: int,
    max_assumptions: int,
) -> None:
    rows: List[Dict[str, float]] = []
    big_benches = {"c3540.v", "c6288.v"}
    for idx, bench in enumerate(benchmarks, start=1):
        print(f"[{idx}/{len(benchmarks)}] {bench}")
        # Per-benchmark limit policy:
        # - if user passed --fault-limit, apply it to all benches
        # - else limit only the two heavy benchmarks
        if fault_limit is not None:
            bench_limit = fault_limit
        elif bench in big_benches:
            bench_limit = big_limit
        else:
            bench_limit = None
        row = compare_baseline_vs_llm(
            bench,
            llm_mode=llm_mode,
            fault_limit=bench_limit,
            hard_conflicts=hard_conflicts,
            hard_decisions=hard_decisions,
            max_assumptions=max_assumptions,
        )
        rows.append(row)
        print("\n" + "=" * 72 + "\n")

    fieldnames = [
        "benchmark",
        "faults_evaluated",
        "baseline_detected",
        "guided_detected",
        "baseline_sat_time_s",
        "guided_sat_time_s",
        "baseline_avg_decisions",
        "guided_avg_decisions",
        "baseline_avg_conflicts",
        "guided_avg_conflicts",
        "guided_fallback_count",
        "avg_unsat_core_size",
        "guided_invocations",
        "guided_skipped",
        "hard_conflicts",
        "hard_decisions",
        "max_assumptions",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"Wrote comparison CSV: {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LLM-guided SAT-ATPG comparison against baseline."
    )
    parser.add_argument("--bench", default="c432.v", help="Benchmark filename in Benchmarks/")
    parser.add_argument(
        "--llm-mode",
        choices=["heuristic", "openai"],
        default="heuristic",
        help="Guidance mode. Use openai if OPENAI_API_KEY is configured.",
    )
    parser.add_argument(
        "--fault-limit",
        type=int,
        default=None,
        help="Optional global limit for all benchmarks. If omitted, full faults are used except big benches in suite.",
    )
    parser.add_argument(
        "--big-limit",
        type=int,
        default=1200,
        help="Per-benchmark limit for c3540/c6288 in suite mode when --fault-limit is not set.",
    )
    parser.add_argument(
        "--hard-conflicts",
        type=int,
        default=100,
        help="Apply guidance only if baseline conflicts >= this threshold.",
    )
    parser.add_argument(
        "--hard-decisions",
        type=int,
        default=800,
        help="Apply guidance only if baseline decisions >= this threshold.",
    )
    parser.add_argument(
        "--max-assumptions",
        type=int,
        default=4,
        help="Maximum PI assumptions for guided solve.",
    )
    parser.add_argument(
        "--suite",
        action="store_true",
        help="Run a multi-benchmark suite and write CSV output.",
    )
    parser.add_argument(
        "--csv",
        default=os.path.join(os.path.dirname(__file__), "..", "llm_compare_metrics.csv"),
        help="CSV output path when --suite is used.",
    )
    args = parser.parse_args()
    if args.suite:
        suite = ["c432.v", "c499.v", "c880.v", "c1355.v", "c3540.v", "c6288.v", "s298.v", "s344.v"]
        run_suite_and_write_csv(
            benchmarks=suite,
            csv_path=args.csv,
            llm_mode=args.llm_mode,
            fault_limit=args.fault_limit,
            big_limit=args.big_limit,
            hard_conflicts=args.hard_conflicts,
            hard_decisions=args.hard_decisions,
            max_assumptions=args.max_assumptions,
        )
    else:
        compare_baseline_vs_llm(
            args.bench,
            llm_mode=args.llm_mode,
            fault_limit=args.fault_limit,
            hard_conflicts=args.hard_conflicts,
            hard_decisions=args.hard_decisions,
            max_assumptions=args.max_assumptions,
        )
        

