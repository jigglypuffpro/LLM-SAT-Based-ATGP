from pysat.solvers import Minisat22


def run_toy_demo() -> None:
    """
    Very small self-contained demo circuit to show SAT-ATPG working.

    Circuit:
      C = A AND B

    Fault:
      C stuck-at-0

    Goal:
      Find inputs (A, B) such that good C != faulty C.
    """

    # Variable mapping (single-copy toy example)
    A = 1
    B = 2
    C_good = 3
    C_faulty = 4

    cnf = []

    # AND gate: C_good = A AND B
    cnf.append([-A, -B, C_good])
    cnf.append([A, -C_good])
    cnf.append([B, -C_good])

    # Fault model: C_faulty = 0 (stuck-at-0)
    cnf.append([-C_faulty])

    # Miter: good output != faulty output  (C_good XOR C_faulty)
    cnf.append([C_good, C_faulty])
    cnf.append([-C_good, -C_faulty])

    solver = Minisat22()
    for clause in cnf:
        solver.add_clause(clause)

    sat = solver.solve()
    if not sat:
        print("UNSAT — toy fault not detectable")
        return

    model = solver.get_model()
    print("Toy demo: SAT — test vector exists")

    A_val = model[A - 1] > 0
    B_val = model[B - 1] > 0

    print("Test Vector:")
    print("A =", int(A_val))
    print("B =", int(B_val))


if __name__ == "__main__":
    run_toy_demo()

