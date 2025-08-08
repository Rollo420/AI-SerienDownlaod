import asyncio
import random

semaphore = asyncio.Semaphore(3)

async def fetch_data(user_id):
    print(f"Fetch data for user {user_id}")
    print(f"User {user_id}: Waiting for semaphore...")
    async with semaphore:
        print(f"Task {user_id}: Semaphor erworben, führe Arbeit aus.")
        await asyncio.sleep(random.uniform(0.2, 2))
        print(f"Task {user_id}: Arbeit beendet, Semaphor wird freigegeben.")

    print(f"Finished fetching data for user {user_id}.")
    return f"Data for user {user_id}."

async def main():
    print(f"start Programm\nCreate Tasks...")
    task =  [fetch_data(i) for i in range(1,10)]
    results = await asyncio.gather(*task)
    print(f"Results: {results}")

if __name__ == '__main__':
    asyncio.run(main())
