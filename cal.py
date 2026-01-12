#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
cal.py
Compute Dw and fw for Nishi-Waseda classroom utilization.

Directory structure (example):
current/
  cal.py
  data/
    room_capacity.csv
    period_room_fall/
      mon.csv ... fri.csv
    period_room_winter/
      mon.csv ... fri.csv

Outputs (default):
  data/out/dw_fw_all.csv
  data/out/topk_by_day.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd


DAYS = ["mon", "tue", "wed", "thu", "fri"]


def _count_occupied_periods(df: pd.DataFrame) -> pd.Series:
    """
    df: timetable dataframe whose columns are: period, <classroom1>, <classroom2>, ...
    Return: occupied periods count per classroom column.
    """
    if "period" not in df.columns:
        raise ValueError("CSV must have a 'period' column as the first column.")

    rooms = [c for c in df.columns if c != "period"]

    # Convert to string carefully: keep NaN as NaN then fill with ""
    occ = (
        df[rooms]
        .applymap(lambda x: "" if pd.isna(x) else str(x).strip())
        .ne("")  # True if occupied
    )

    return occ.sum(axis=0)  # per room column


def compute_for_semester(
    semester_name: str,
    period_dir: Path,
    cap_map: Dict[str, int],
    n_total: int = 14,
) -> pd.DataFrame:
    """
    Build a long-form dataframe with columns:
    semester, day, classroom, capacity, occupied_periods, Nheld, Dw, fw
    """
    rows: List[Dict] = []

    for day in DAYS:
        csv_path = period_dir / f"{day}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"Missing file: {csv_path}")

        df = pd.read_csv(csv_path)
        occ_counts = _count_occupied_periods(df)

        for room, occupied_periods in occ_counts.items():
            cap = cap_map.get(room)
            if cap is None:
                cap = None  # capacity unknown -> fw cannot be computed reliably

            # Slide definition:
            # Dw(X) = Nheld(X) / Ntotal, with Ntotal fixed at 14.
            # timetable is weekly schedule per period -> Nheld = occupied_periods * 14 weeks
            nheld = int(occupied_periods) * n_total
            dw = nheld / n_total  # equals occupied_periods

            fw = None
            if cap is not None and cap != 0:
                fw = ((dw + 0.001) / cap)*100000

            rows.append(
                {
                    "semester": semester_name,
                    "day": day,
                    "classroom": str(room),
                    "capacity": cap,
                    "occupied_periods": int(occupied_periods),
                    "Nheld": nheld,
                    "Dw": float(dw),
                    "fw": (float(fw) if fw is not None else None),
                }
            )

    out = pd.DataFrame(rows)
    out["day"] = pd.Categorical(out["day"], categories=DAYS, ordered=True)

    # Helpful ordering (not the final ranking, just stable sorting)
    out = out.sort_values(["semester", "day", "fw", "Dw", "classroom"], na_position="last")
    return out


def topk_by_day(df_all: pd.DataFrame, k: int = 5) -> pd.DataFrame:
    """
    Pick top-k least-busy classrooms per semester/day by minimum fw.
    Tie-breakers: smaller Dw, then larger capacity, then classroom name.
    Output includes rank (1..k) per group.
    """
    df = df_all.copy()
    df = df[df["fw"].notna()].copy()

    df = df.sort_values(
        ["semester", "day", "fw", "Dw", "capacity", "classroom"],
        ascending=[True, True, True, True, False, True],
    )

    # rank within each (semester, day)
    df["rank"] = df.groupby(["semester", "day"]).cumcount() + 1

    # keep only top-k
    df = df[df["rank"] <= int(k)].copy()

    # Nice column order
    cols = ["semester", "day", "rank", "classroom", "capacity", "occupied_periods", "Dw", "fw"]
    return df[cols].reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_dir", type=str, default="data", help="data directory")
    ap.add_argument("--out_dir", type=str, default="data/out", help="output directory")
    ap.add_argument("--n_total", type=int, default=14, help="Ntotal (weeks), default 14")
    ap.add_argument("--topk", type=int, default=10, help="Top-K least-busy rooms per semester/day")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cap_path = data_dir / "room_capacity.csv"
    if not cap_path.exists():
        raise FileNotFoundError(f"Missing file: {cap_path}")

    cap_df = pd.read_csv(cap_path)
    if "classroom" not in cap_df.columns or "capacity" not in cap_df.columns:
        raise ValueError("room_capacity.csv must have columns: classroom, capacity")

    cap_map = dict(zip(cap_df["classroom"].astype(str), cap_df["capacity"].astype(int)))

    sem_dirs: List[Tuple[str, Path]] = [
        ("fall", data_dir / "period_room_fall"),
        ("winter", data_dir / "period_room_winter"),
    ]
    for name, p in sem_dirs:
        if not p.exists():
            raise FileNotFoundError(f"Missing directory: {p}")

    all_frames: List[pd.DataFrame] = []
    for sem_name, sem_path in sem_dirs:
        all_frames.append(
            compute_for_semester(
                semester_name=sem_name,
                period_dir=sem_path,
                cap_map=cap_map,
                n_total=args.n_total,
            )
        )

    df_all = pd.concat(all_frames, ignore_index=True)

    # Merge fall and winter: compute average fw per (day, classroom)
    df_merged = (
        df_all.groupby(["day", "classroom"], as_index=False)
        .agg({
            "capacity": "first",
            "occupied_periods": "mean",
            "Nheld": "mean",
            "Dw": "mean",
            "fw": "mean",
        })
    )
    df_merged["day"] = pd.Categorical(df_merged["day"], categories=DAYS, ordered=True)

    # Rank by average fw (per day)
    df_merged = df_merged.sort_values(
        ["day", "fw", "Dw", "capacity", "classroom"],
        ascending=[True, True, True, False, True],
        na_position="last",
    )
    df_merged["rank"] = df_merged.groupby(["day"]).cumcount() + 1

    # Top-k from merged data
    df_topk = df_merged[df_merged["fw"].notna()].copy()
    df_topk = df_topk[df_topk["rank"] <= args.topk]
    cols = ["day", "rank", "classroom", "capacity", "occupied_periods", "Dw", "fw"]
    df_topk = df_topk[cols].reset_index(drop=True)

    # Write outputs
    all_csv = out_dir / "dw_fw_all.csv"
    topk_csv = out_dir / "topk_by_day.csv"

    df_merged.to_csv(all_csv, index=False, encoding="utf-8")
    df_topk.to_csv(topk_csv, index=False, encoding="utf-8")

    # Console summary
    print(f"[OK] wrote: {all_csv}")
    print(f"[OK] wrote: {topk_csv}")
    print(f"\n=== Least-busy classroom per day (avg fw of fall+winter) TOP {args.topk} ===")

    # Print grouped view (dayごとに見やすく)
    for day, g in df_topk.groupby(["day"], sort=False):
        print(f"\n[{day}]")
        print(
            g[["rank", "classroom", "capacity", "occupied_periods", "Dw", "fw"]]
            .to_string(index=False)
        )


if __name__ == "__main__":
    main()
