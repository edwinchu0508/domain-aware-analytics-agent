"""Recompute public headline statistics from published aggregate measurements."""
import json
from math import sqrt
from pathlib import Path

path=Path(__file__).resolve().parents[1]/'results/aggregate_results.json'
data=json.loads(path.read_text(encoding='utf-8'))
for arm in data['variants']:
    n,k=arm['assigned'],arm['correct_main']
    p=k/n;z=1.959963984540054;d=1+z*z/n
    center=(p+z*z/(2*n))/d
    half=z*sqrt((p*(1-p)+z*z/(4*n))/n)/d
    print(json.dumps(dict(variant=arm['variant'],correct=k,assigned=n,accuracy=p,
        wilson_95=[center-half,center+half],mean_cost_usd=arm['estimated_cost_usd']/n,
        mean_latency_seconds=arm['mean_latency_seconds'],cost_per_correct_usd=arm['estimated_cost_usd']/k)))
