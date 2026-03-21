from typing import Dict, List, Tuple, Any
import os
import time
import json
import re

from pysat.solvers import Minisat22

from cnf_encoder import (
    CNF,
    Gate,
    encode_circuit_copy,
    add_stuck_at_fault,
    add_miter,
    new_var,
)


def parse_iscas_verilog(path: str) -> Tuple[List[Gate], List[str], List[str]]:
    gates: List[Gate] = []
    primary_inputs: List[str] = []
    primary_outputs: List[str] = []

    pending_decl_kind = None  # "input" or "output"
    pending_decl_buf = ""

    def _flush_decl(kind: str, buf: str) -> None:
        names = [n.strip() for n in buf.split(",") if n.strip()]
        if kind == "input":
            primary_inputs.extend(names)
        elif kind == "output":
            primary_outputs.extend(names)

    with open(path, "r") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue

            # Continue parsing a multiline input/output declaration until ';'
            if pending_decl_kind is not None:
                done = line.endswith(";")
                chunk = line[:-1] if done else line
                pending_decl_buf += " " + chunk
                if done:
                    _flush_decl(pending_decl_kind, pending_decl_buf)
                    pending_decl_kind = None
                    pending_decl_buf = ""
                continue

            if line.startswith("input "):
                buf = line[len("input ") :]
                done = buf.endswith(";")
                if done:
                    _flush_decl("input", buf[:-1])
                else:
                    pending_decl_kind = "input"
                    pending_decl_buf = buf
                continue

            if line.startswith("output "):
                buf = line[len("output ") :]
                done = buf.endswith(";")
                if done:
                    _flush_decl("output", buf[:-1])
                else:
                    pending_decl_kind = "output"
                    pending_decl_buf = buf
                continue

            # Gate instance lines: e.g. "nand NAND2_19 (N154, N118, N4);"
            if line.startswith(
                (
                    "not ",
                    "nand ",
                    "nor ",
                    "and ",
                    "xor ",
                    "buf ",
                    "or ",
                )
            ):
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
                elif gate_kw == "buf":
                    gtype = "BUF"
                elif gate_kw == "or":
                    gtype = "OR"
                else:
                    continue

                gates.append((gtype, out_name, in_names))

    # Deduplicate while preserving declaration order.
    primary_inputs = list(dict.fromkeys(primary_inputs))
    primary_outputs = list(dict.fromkeys(primary_outputs))
    return gates, primary_inputs, primary_outputs


def _yosys_bit_name(bit: Any, bit_to_name: Dict[int, str]) -> str:
    if isinstance(bit, int):
        if bit not in bit_to_name:
            bit_to_name[bit] = f"n{bit}"
        return bit_to_name[bit]
    # Constants may appear as strings like "0" / "1" in JSON.
    if bit == "0":
        return "__const0"
    if bit == "1":
        return "__const1"
    raise ValueError(f"Unsupported bit token in Yosys JSON: {bit}")


def _normalize_cell_type(cell_type: str) -> str:
    # Handle Yosys internal forms like $_AND_ / $_NOT_ first
    direct_map = {
        "$_AND_": "AND",
        "$_OR_": "OR",
        "$_NAND_": "NAND",
        "$_NOR_": "NOR",
        "$_XOR_": "XOR",
        "$_NOT_": "NOT",
        "$_BUF_": "BUF",
    }
    if cell_type in direct_map:
        return direct_map[cell_type]

    # Heuristic mapping for common tech-mapped library names (e.g. NAND2X1, INVX1)
    utype = cell_type.upper()
    if "XNOR" in utype:
        raise NotImplementedError(f"Cell type {cell_type} uses XNOR, unsupported currently")
    if "NAND" in utype:
        return "NAND"
    if "NOR" in utype:
        return "NOR"
    if "XOR" in utype:
        return "XOR"
    if "AND" in utype:
        return "AND"
    if re.search(r"(^|_)OR", utype) or utype.startswith("OR"):
        return "OR"
    if "INV" in utype or "NOT" in utype:
        return "NOT"
    if "BUF" in utype:
        return "BUF"

    raise NotImplementedError(f"Unsupported cell type in Yosys JSON: {cell_type}")


def parse_yosys_json(path: str) -> Tuple[List[Gate], List[str], List[str]]:
    with open(path, "r") as f:
        data = json.load(f)

    modules = data.get("modules", {})
    if not modules:
        raise ValueError("No modules found in Yosys JSON")

    # Pick the first module for now
    module_name = next(iter(modules))
    mod = modules[module_name]

    bit_to_name: Dict[int, str] = {}

    # Build readable names for bit IDs from netnames
    for net_name, net_obj in mod.get("netnames", {}).items():
        bits = net_obj.get("bits", [])
        if len(bits) == 1 and isinstance(bits[0], int):
            bit_to_name.setdefault(bits[0], net_name)
        else:
            for idx, b in enumerate(bits):
                if isinstance(b, int):
                    bit_to_name.setdefault(b, f"{net_name}[{idx}]")

    primary_inputs: List[str] = []
    primary_outputs: List[str] = []

    for port_name, port_obj in mod.get("ports", {}).items():
        direction = port_obj.get("direction")
        bits = port_obj.get("bits", [])
        expanded_names = [_yosys_bit_name(b, bit_to_name) for b in bits]
        if direction == "input":
            primary_inputs.extend(expanded_names)
        elif direction == "output":
            primary_outputs.extend(expanded_names)

    gates: List[Gate] = []
    output_pin_priority = ["Y", "ZN", "Z", "Q", "QN", "OUT", "O", "X"]

    for _cell_name, cell_obj in mod.get("cells", {}).items():
        cell_type = cell_obj.get("type", "")
        gtype = _normalize_cell_type(cell_type)
        conns = cell_obj.get("connections", {})
        if not conns:
            continue

        out_pin = None
        for p in output_pin_priority:
            if p in conns:
                out_pin = p
                break
        if out_pin is None:
            # Fallback: choose last pin alphabetically as output candidate.
            out_pin = sorted(conns.keys())[-1]

        out_bits = conns.get(out_pin, [])
        if len(out_bits) != 1:
            raise ValueError(f"Cell output {out_pin} is not single-bit in {cell_type}")
        out_name = _yosys_bit_name(out_bits[0], bit_to_name)

        in_names: List[str] = []
        for pin, bits in conns.items():
            if pin == out_pin:
                continue
            for b in bits:
                in_names.append(_yosys_bit_name(b, bit_to_name))

        gates.append((gtype, out_name, in_names))

    # Ensure deterministic ordering and remove duplicates while preserving order.
    primary_inputs = list(dict.fromkeys(primary_inputs))
    primary_outputs = list(dict.fromkeys(primary_outputs))
    return gates, primary_inputs, primary_outputs


def parse_netlist(path: str) -> Tuple[List[Gate], List[str], List[str]]:
    if path.lower().endswith(".json"):
        return parse_yosys_json(path)
    return parse_iscas_verilog(path)


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
    # In the faulty copy, disconnect the fault site from its original driver.
    cnf += encode_circuit_copy(gates, var_map, suffix="_f", skip_outputs=[fault_signal])

    # If Yosys JSON includes literal constants, force them.
    for suffix in ("_g", "_f"):
        c0 = new_var(var_map, "__const0" + suffix)
        c1 = new_var(var_map, "__const1" + suffix)
        cnf.append([-c0])
        cnf.append([c1])

    # Tie good and faulty primary inputs together so they see the same stimulus,
    # except at the fault site itself if the fault is on a primary input.
    for name in primary_inputs:
        if name == fault_signal:
            continue
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

    gates, primary_inputs, primary_outputs = parse_netlist(bench_path)

    # Example: test N1 stuck-at-1 on c17
    fault_signal = "N112"
    sa_val = 1

    cnf, var_map = build_cnf_for_fault(
        gates,
        primary_inputs,
        primary_outputs,
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
    print("SAT — test vector exists for", fault_signal, f"sa{sa_val}")
    print("Test Vector (primary inputs):")

    for name in primary_inputs:
        v = new_var(var_map, name + "_g")
        val = model[v - 1] > 0
        print(f"  {name} = {int(val)}")

    print("Outputs (good vs faulty):")
    for y in primary_outputs:
        yg = new_var(var_map, y + "_g")
        yf = new_var(var_map, y + "_f")
        yg_val = model[yg - 1] > 0
        yf_val = model[yf - 1] > 0
        print(f"  {y}: good={int(yg_val)} faulty={int(yf_val)}")


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


def run_atpg_all_faults(bench_filename: str) -> Dict[str, float]:
    this_dir = os.path.dirname(os.path.abspath(__file__))
    bench_path = os.path.join(this_dir, "..", "Benchmarks", bench_filename)

    gates, primary_inputs, primary_outputs = parse_netlist(bench_path)
    faults = generate_fault_list(gates)

    detected = 0
    total_decisions = 0
    total_conflicts = 0
    total_sat_time = 0.0

    start_all = time.time()

    for signal, sa_val in faults:
        cnf, var_map = build_cnf_for_fault(
            gates,
            primary_inputs,
            primary_outputs,
            signal,
            sa_val,
        )

        solver = Minisat22()
        for clause in cnf:
            solver.add_clause(clause)

        t0 = time.time()
        sat = solver.solve()
        t1 = time.time()

        stats = solver.accum_stats()
        decisions = stats.get("decisions", 0)
        conflicts = stats.get("conflicts", 0)

        total_sat_time += (t1 - t0)
        total_decisions += decisions
        total_conflicts += conflicts

        if sat:
            detected += 1

    end_all = time.time()

    result: Dict[str, float] = {
        "benchmark": bench_filename,
        "detected_faults": float(detected),
        "total_faults": float(len(faults)),
        "wall_time": end_all - start_all,
        "sat_time": total_sat_time,
        "avg_decisions": (total_decisions / len(faults)) if faults else 0.0,
        "avg_conflicts": (total_conflicts / len(faults)) if faults else 0.0,
    }

    # Lightweight per-benchmark summary
    print(
        f"{bench_filename}: detected {detected}/{len(faults)} "
        f"(wall {result['wall_time']:.3f}s, SAT {result['sat_time']:.3f}s)"
    )

    return result


if __name__ == "__main__":
    solve_single_fault_on_c432()
    # run_atpg_all_faults("c17.v")