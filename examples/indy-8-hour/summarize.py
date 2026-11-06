"""Summarize the published sample from the Indianapolis setup sessions."""

from collections import defaultdict
import csv
from math import fsum, isclose
from pathlib import Path
from statistics import median


def read_csv(filename):
    with Path(__file__).with_name(filename).open(encoding='utf-8', newline='') as file:
        return list(csv.DictReader(file))


def main():
    times = defaultdict(list)
    matched_laps = {}
    for row in read_csv('setup-laps.csv'):
        if row['included_in_pace'] != 'true':
            continue
        setup = row['setup']
        times[setup].append(float(row['lap_time_s']))
        if row['lap_number'] == '7' and setup in {'Baseline', 'v1'}:
            matched_laps[setup] = row

    print(f'{"Setup":<10} {"Laps":>4} {"Best (s)":>9} {"Median (s)":>10}')
    for setup, laps in times.items():
        print(f'{setup:<10} {len(laps):>4} {min(laps):>9.3f} {median(laps):>10.3f}')

    baseline, v1 = matched_laps['Baseline'], matched_laps['v1']
    print('\nCloser-matched lap 7 (baseline / v1)')
    for field in ('lap_time_s', 'fuel_start_l', 'track_temp_c'):
        print(f'{field}: {baseline[field]} / {v1[field]}')

    segments = read_csv('best-lap-segments.csv')
    delta = fsum(float(row['v5_minus_baseline_s']) for row in segments)
    best_lap_delta = min(times['v5']) - min(times['Baseline'])
    if not isclose(delta, best_lap_delta, abs_tol=.0001):
        raise ValueError(f'Segment total {delta:.6f} s differs from lap delta {best_lap_delta:.6f} s.')
    print(f'\nSegment total (v5 minus baseline): {delta:+.3f} s')
    corners = sorted((row for row in segments if row['kind'] == 'corner'),
                     key=lambda row: float(row['v5_minus_baseline_s']))
    for row in corners[:3]:
        print(f'{row["segment"]}: {float(row["v5_minus_baseline_s"]):+.3f} s')


if __name__ == '__main__':
    main()
