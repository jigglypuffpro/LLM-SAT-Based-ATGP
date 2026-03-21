from typing import Dict, List, Tuple

Clause = List[int]
CNF = List[Clause]

Gate = Tuple[str, str, List[str]]  # (type, output, [inputs])


def new_var(var_map: Dict[str, int], name: str) -> int:
    if name not in var_map:
        var_map[name] = len(var_map) + 1
    return var_map[name]


def encode_and_n(cnf: CNF, out: int, inputs: List[int]) -> None:
    # out <-> AND over all inputs
    # (¬x1 ∨ ... ∨ ¬xn ∨ out) ∧ ∧i(¬out ∨ xi)
    if len(inputs) == 0:
        return
    cnf.append([-(x) for x in inputs] + [out])
    for x in inputs:
        cnf.append([-out, x])


def encode_or_n(cnf: CNF, out: int, inputs: List[int]) -> None:
    # out <-> OR over all inputs
    # (¬xi ∨ out) for all i, and (¬out ∨ x1 ∨ ... ∨ xn)
    if len(inputs) == 0:
        return
    for x in inputs:
        cnf.append([-x, out])
    cnf.append([-out] + inputs)


def encode_and(cnf: CNF, out: int, a: int, b: int) -> None:
    encode_and_n(cnf, out, [a, b])


def encode_or(cnf: CNF, out: int, a: int, b: int) -> None:
    encode_or_n(cnf, out, [a, b])


def encode_not(cnf: CNF, out: int, a: int) -> None:
    # out = NOT a
    cnf.append([-a, -out])
    cnf.append([a, out])


def encode_buf(cnf: CNF, out: int, a: int) -> None:
    # out = a
    cnf.append([-a, out])
    cnf.append([a, -out])


def encode_nand_n(cnf: CNF, out: int, inputs: List[int]) -> None:
    # out <-> NAND over inputs = NOT(AND)
    # For y = ¬(x1 ∧ ... ∧ xn), correct CNF is:
    # (¬y ∨ ¬x1 ∨ ... ∨ ¬xn) ∧ ∧i (xi ∨ y)
    if len(inputs) == 0:
        return
    # (¬y ∨ ¬x1 ∨ ... ∨ ¬xn)
    cnf.append([-out] + [-(x) for x in inputs])
    # (xi ∨ y) for all i
    for x in inputs:
        cnf.append([x, out])


def encode_nor_n(cnf: CNF, out: int, inputs: List[int]) -> None:
    # out <-> NOR over inputs = NOT(OR)
    # Constraints for y = ¬(x1 ∨ ... ∨ xn):
    # (¬y ∨ ¬xi) for all i, and (y ∨ x1 ∨ ... ∨ xn)
    if len(inputs) == 0:
        return
    for x in inputs:
        cnf.append([-out, -x])
    cnf.append([out] + inputs)


def encode_xor2(cnf: CNF, out: int, a: int, b: int) -> None:
    # out <-> a XOR b
    cnf.append([-a, -b, -out])
    cnf.append([-a, b, out])
    cnf.append([a, -b, out])
    cnf.append([a, b, -out])


def encode_gate(
    cnf: CNF,
    gate: Gate,
    var_map: Dict[str, int],
    suffix: str,
) -> None:
    """
    Encode a single gate into CNF for either the good or faulty copy.
    suffix: e.g. "_g" for good, "_f" for faulty, or "" for single-copy.
    """
    gtype, out_name, in_names = gate
    out_var = new_var(var_map, out_name + suffix)
    in_vars = [new_var(var_map, n + suffix) for n in in_names]

    if gtype == "AND":
        encode_and_n(cnf, out_var, in_vars)
    elif gtype == "OR":
        encode_or_n(cnf, out_var, in_vars)
    elif gtype == "NOT":
        assert len(in_vars) == 1
        encode_not(cnf, out_var, in_vars[0])
    elif gtype == "BUF":
        assert len(in_vars) == 1
        encode_buf(cnf, out_var, in_vars[0])
    elif gtype == "NAND":
        encode_nand_n(cnf, out_var, in_vars)
    elif gtype == "NOR":
        encode_nor_n(cnf, out_var, in_vars)
    elif gtype == "XOR":
        assert len(in_vars) == 2
        encode_xor2(cnf, out_var, in_vars[0], in_vars[1])
    else:
        raise NotImplementedError(f"Gate type {gtype} not supported")


def encode_circuit_copy(
    gates: List[Gate],
    var_map: Dict[str, int],
    suffix: str,
    skip_outputs: List[str] | None = None,
) -> CNF:
    cnf: CNF = []
    skip_set = set(skip_outputs or [])
    for gate in gates:
        _gtype, out_name, _in_names = gate
        if out_name in skip_set:
            continue
        encode_gate(cnf, gate, var_map, suffix)
    return cnf


def add_stuck_at_fault(
    cnf: CNF,
    var_map: Dict[str, int],
    signal_name: str,
    suffix: str,
    sa_val: int,
) -> None:
    v = new_var(var_map, signal_name + suffix)
    if sa_val == 0:
        cnf.append([-v])
    else:
        cnf.append([v])


def add_miter(
    cnf: CNF,
    var_map: Dict[str, int],
    outputs: List[str],
    good_suffix: str = "_g",
    faulty_suffix: str = "_f",
) -> None:
    """
    Enforce output divergence between good and faulty copies.

    For a single output Y:
      encode Y_g XOR Y_f directly.

    For multiple outputs Y1..Yn:
      introduce diff_i ↔ (Yi_g XOR Yi_f) and require OR_i diff_i.
    """
    if not outputs:
        return

    if len(outputs) == 1:
        y = outputs[0]
        yg = new_var(var_map, y + good_suffix)
        yf = new_var(var_map, y + faulty_suffix)

        # yg XOR yf
        cnf.append([yg, yf])
        cnf.append([-yg, -yf])
        return

    # Multiple outputs: ensure at least one output differs
    diff_vars: List[int] = []

    for y in outputs:
        yg = new_var(var_map, y + good_suffix)
        yf = new_var(var_map, y + faulty_suffix)
        d = new_var(var_map, f"diff_{y}")
        diff_vars.append(d)

        # d <-> (yg XOR yf)
        # Correct CNF:
        # ( yg ∨  yf ∨ ¬d) ∧ (¬yg ∨ ¬yf ∨ ¬d)
        # ( yg ∨ ¬yf ∨  d) ∧ (¬yg ∨  yf ∨  d)
        cnf.append([yg, yf, -d])
        cnf.append([-yg, -yf, -d])
        cnf.append([yg, -yf, d])
        cnf.append([-yg, yf, d])

    # At least one diff_i must be true
    cnf.append(diff_vars)

