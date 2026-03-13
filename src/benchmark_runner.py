import csv
from typing import List, Dict

from sat_atpg_demo import run_atpg_all_faults


BENCHMARKS: List[str] = [
    "c432.v",
    "c499.v",
    "c880.v",
    "c1355.v",
    "c3540.v",
    "c6288.v",
    "s298.v",
    "s344.v",
]


def run_all_benchmarks() -> List[Dict[str, float]]:
    results: List[Dict[str, float]] = []
    for bench in BENCHMARKS:
        res = run_atpg_all_faults(bench)
        results.append(res)
    return results


def print_results_table(results: List[Dict[str, float]]) -> None:
    headers = [
        "Benchmark",
        "Detected/Total",
        "Coverage (%)",
        "Wall Time (s)",
        "SAT Time (s)",
        "Avg Decisions",
        "Avg Conflicts",
    ]

    rows: List[List[str]] = []
    for r in results:
        detected = int(r["detected_faults"])
        total = int(r["total_faults"])
        coverage = 100.0 * detected / total if total else 0.0
        rows.append(
            [
                r["benchmark"],
                f"{detected}/{total}",
                f"{coverage:.2f}",
                f"{r['wall_time']:.3f}",
                f"{r['sat_time']:.3f}",
                f"{r['avg_decisions']:.1f}",
                f"{r['avg_conflicts']:.1f}",
            ]
        )

    # Compute column widths
    cols = list(zip(headers, *rows))
    widths = [max(len(str(cell)) for cell in col) for col in cols]

    def fmt_row(cells: List[str]) -> str:
        return " | ".join(str(c).ljust(w) for c, w in zip(cells, widths))

    print()
    print(fmt_row(headers))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(fmt_row(row))
    print()


def write_csv(results: List[Dict[str, float]], path: str = "benchmark_metrics.csv") -> None:
    fieldnames = [
        "benchmark",
        "detected_faults",
        "total_faults",
        "coverage_percent",
        "wall_time",
        "sat_time",
        "avg_decisions",
        "avg_conflicts",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            detected = int(r["detected_faults"])
            total = int(r["total_faults"])
            coverage = 100.0 * detected / total if total else 0.0
            row = {
                "benchmark": r["benchmark"],
                "detected_faults": detected,
                "total_faults": total,
                "coverage_percent": coverage,
                "wall_time": r["wall_time"],
                "sat_time": r["sat_time"],
                "avg_decisions": r["avg_decisions"],
                "avg_conflicts": r["avg_conflicts"],
            }
            writer.writerow(row)


if __name__ == "__main__":
    results = run_all_benchmarks()
    print_results_table(results)
    write_csv(results)

