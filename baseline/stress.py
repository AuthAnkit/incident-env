import multiprocessing
import time
import psutil

def burn_cpu():
    print("🔥 Real Chaos Engineering Agent started: Burning CPU")
    while True:
        # Intense math to max out a core
        _ = [x**2 for x in range(10000)]

def start_chaos():
    cores_to_burn = max(1, psutil.cpu_count() // 2)
    processes = []
    
    for _ in range(cores_to_burn):
        p = multiprocessing.Process(target=burn_cpu)
        p.start()
        processes.append(p)
        
    print(f"Started {cores_to_burn} processes consuming CPU...")
    try:
        while True:
            time.sleep(1)
            print(f"Current System CPU: {psutil.cpu_percent()}%")
    except KeyboardInterrupt:
        print("Stopping chaos...")
        for p in processes:
            p.terminate()

if __name__ == "__main__":
    start_chaos()
