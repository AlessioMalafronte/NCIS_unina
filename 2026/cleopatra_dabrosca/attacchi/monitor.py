import subprocess
import time
import csv
import sys

target = sys.argv[1] if len(sys.argv) > 1 else '10.0.0.2'
duration = int(sys.argv[2]) if len(sys.argv) > 2 else 60
outfile = sys.argv[3] if len(sys.argv) > 3 else 'ping_log.csv'

with open(outfile, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['elapsed_s', 'rtt_ms', 'lost'])
    start = time.time()
    while time.time() - start < duration:
        t0 = time.time()
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '1', target],
            capture_output=True, text=True
        )
        rtt = None
        for line in result.stdout.splitlines():
            if 'time=' in line:
                rtt = float(line.split('time=')[1].split(' ')[0])
        lost = 1 if rtt is None else 0
        writer.writerow([round(time.time() - start, 2), rtt if rtt else '', lost])
        f.flush()
        elapsed = time.time() - t0
        time.sleep(max(0, 1 - elapsed))

print(f"Log salvato in {outfile}")
