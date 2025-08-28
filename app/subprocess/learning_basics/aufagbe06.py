import asyncio
import random

async def fetch_simpel_data(name, delay):
    print(f"[{name}] Starting fetch...")
    await asyncio.sleep(delay)
    print(f"[{name}] finished fetch.")
    return f"Data from {name}"

def manual_loop_example():
    loop = asyncio.new_event_loop()

    loop.run_until_complete(fetch_simpel_data("jan", random.uniform(0.2, 2)))
    loop.run_until_complete(fetch_simpel_data("lars", random.uniform(0.2, 2)))
    
    loop.close()


if __name__ == '__main__':
    manual_loop_example()