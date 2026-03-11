from typing import Dict, List, Tuple
import os

from pysat.solvers import Minisat22

from cnf_encoder import (
    CNF,
    Gate,
    encode_circuit_copy,
    add_stuck_at_fault,
    add_miter,
    new_var,
)


def parse_c432_verilog(path: str) -> Tuple[List[Gate], List[str], List[str]]:
    gates: List[Gate] = []
    primary_inputs: List[str] = []
    primary_outputs: List[str] = []

    with open(path, "r") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue

            if line.startswith("input "):
                # input N1,N4,...;
                line = line[len("input ") :]
                if line.endswith(";"):
                    line = line[:-1]
                names = [n.strip() for n in line.split(",") if n.strip()]
                primary_inputs.extend(names)
                continue

            if line.startswith("output "):
                line = line[len("output ") :]
                if line.endswith(";"):
                    line = line[:-1]
                names = [n.strip() for n in line.split(",") if n.strip()]
                primary_outputs.extend(names)
                continue

            # Gate instance lines: e.g. "nand NAND2_19 (N154, N118, N4);"
            if line.startswith(("not ", "nand ", "nor ", "and ", "xor ")):
                # Strip trailing semicolon
                if line.endswith(";"):
                    line = line[:-1]
                # gate keyword is first token
                first_space = line.find(" ")
                gate_kw = line[:first_space]
                rest = line[first_space + 1 :].strip()

                # Extract signal list inside parentheses
                lpar = rest.find("(")
                rpar = rest.rfind(")")
                if lpar == -1 or rpar == -1:
                    continue
                sigs_str = rest[lpar + 1 : rpar]
                sigs = [s.strip() for s in sigs_str.split(",") if s.strip()]
                if len(sigs) < 2:
                    continue

                out_name = sigs[0]
                in_names = sigs[1:]

                if gate_kw == "not":
                    gtype = "NOT"
                elif gate_kw == "nand":
                    gtype = "NAND"
                elif gate_kw == "nor":
                    gtype = "NOR"
                elif gate_kw == "and":
                    gtype = "AND"
                elif gate_kw == "xor":
                    gtype = "XOR"
                else:
                    continue

                gates.append((gtype, out_name, in_names))

    return gates, primary_inputs, primary_outputs


def build_cnf_for_fault(
    gates: List[Gate],
    primary_inputs: List[str],
    primary_outputs: List[str],
    fault_signal: str,
    sa_val: int,
) -> tuple[CNF, Dict[str, int]]:
    var_map: Dict[str, int] = {}

    cnf: CNF = []
    cnf += encode_circuit_copy(gates, var_map, suffix="_g")
    cnf += encode_circuit_copy(gates, var_map, suffix="_f")

    # Tie good and faulty primary inputs together so they see the same stimulus.
    for name in primary_inputs:
        g = new_var(var_map, name + "_g")
        f = new_var(var_map, name + "_f")
        cnf.append([-g, f])
        cnf.append([g, -f])

    add_stuck_at_fault(cnf, var_map, fault_signal, suffix="_f", sa_val=sa_val)
    add_miter(cnf, var_map, primary_outputs, good_suffix="_g", faulty_suffix="_f")

    return cnf, var_map


def solve_single_fault_on_c432() -> None:
    this_dir = os.path.dirname(os.path.abspath(__file__))
    bench_path = os.path.join(this_dir, "..", "Benchmarks", "c432.v")

    gates, primary_inputs, primary_outputs = parse_c432_verilog(bench_path)

    # Example: test N223 stuck-at-0
    fault_signal = "N223"
    sa_val = 0

    cnf, var_map = build_cnf_for_fault(
        gates,
        primary_inputs,
        [primary_outputs[0]],
        fault_signal,
        sa_val,
    )

    solver = Minisat22()
    for clause in cnf:
        solver.add_clause(clause)

    sat = solver.solve()
    if not sat:
        print("UNSAT — fault not detectable for", fault_signal)
        return

    model = solver.get_model()
    print("SAT — test vector exists for", fault_signal)
    print("Test Vector (primary inputs):")

    for name in primary_inputs:
        v = new_var(var_map, name + "_g")
        val = model[v - 1] > 0
        print(f"{name} = {int(val)}")


def generate_fault_list(gates: List[Gate]) -> List[Tuple[str, int]]:
    signals = set()
    for gtype, out_name, in_names in gates:
        signals.add(out_name)
        for n in in_names:
            signals.add(n)

    faults: List[Tuple[str, int]] = []
    for s in sorted(signals):
        faults.append((s, 0))
        faults.append((s, 1))
    return faults


def run_atpg_all_faults_on_c432() -> None:
    this_dir = os.path.dirname(os.path.abspath(__file__))
    bench_path = os.path.join(this_dir, "..", "Benchmarks", "c432.v")

    gates, primary_inputs, primary_outputs = parse_c432_verilog(bench_path)
    faults = generate_fault_list(gates)

    detected = 0

    for signal, sa_val in faults:
        cnf, var_map = build_cnf_for_fault(
            gates,
            primary_inputs,
            [primary_outputs[0]],
            signal,
            sa_val,
        )

        solver = Minisat22()
        for clause in cnf:
            solver.add_clause(clause)

        sat = solver.solve()
        if sat:
            detected += 1

    print("Detected faults:", detected)
    print("Total faults:", len(faults))


if __name__ == "__main__":
    #solve_single_fault_on_c432()
    run_atpg_all_faults_on_c432()

#solve_single_fault_on_c432() gives you a test pattern for one chosen fault.
#run_atpg_all_faults_on_c432() can give you fault coverage statistics, which you’ll later compare against the LLM‑guided version.