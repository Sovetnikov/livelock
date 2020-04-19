import asyncio
import time
from collections import defaultdict
from multiprocessing.pool import Pool
from random import randint
from statistics import mean

stats = defaultdict(int)


async def runner(seconds):
    await asyncio.wait([client(seconds) for n in range(tasks_per_process)])


async def client(seconds):
    end_time = time.time() + seconds

    reader, writer = await asyncio.open_connection('127.0.0.1', 7873)
    writer.write(b'CONN\r\n')
    data = await reader.read(3000)
    if data[0] != 43:
        raise Exception('Error')
    while True:
        lock_id = str(randint(111111111, 999999999))
        writer.write(b'AQ ' + lock_id.encode() + b'\r\n')
        data = await reader.read(3000)
        if data[0] != 43:
            raise Exception('Error')
        writer.write(b'RELEASE ' + lock_id.encode() + b'\r\n')
        data = await reader.read(3000)
        if data[0] != 43:
            raise Exception('Error')
        stats[int(time.time())] += 1
        if time.time() >= end_time:
            break
    writer.close()


def f(seconds):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(runner(seconds))
    loop.close()
    return dict(stats)


processes = 2
tasks_per_process = 10
seconds_to_test = 10

if __name__ == '__main__':
    with Pool(processes=processes) as pool:
        results = []
        for n in range(processes):
            results.append(pool.apply_async(f, args=(seconds_to_test,)))
        data = [x.get() for x in results]
        pool.close()
        pool.join()

    counter = defaultdict(int)
    for runner_data in data:
        for s, v in runner_data.items():
            counter[s] += v
    counter.pop(min(counter.keys()))
    counter.pop(max(counter.keys()))
    mn = min(counter.values())
    mx = max(counter.values())
    mean_value = mean(counter.values())

    print(f'min={mn}, max={mx}, mean={mean_value}')
