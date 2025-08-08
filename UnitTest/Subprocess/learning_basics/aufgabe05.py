import asyncio

semaphore = asyncio.Semaphore(2)
async def process_item(itme_id):

    print(f"wait for Semaphore")
    async with semaphore:
        print(f"Task: {itme_id} get the Semaphore")
        if itme_id % 2:
            await asyncio.sleep(0.5)
            raise ValueError(f"Error processing item {itme_id}: Odd ID!")
        await asyncio.sleep(1)
        print(f"Semaphore completed for Task: {itme_id}")
        return f"Item {itme_id} processed successsfully"


async def main():
    tasks = [process_item(i) for i in range(1, 20)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results, 1):
        if isinstance(result, Exception):
            print(f"Task {i} FEHLER: {result}")
        else:
            print(f"Task {i} OK: {result}")

    print("Programm ist finished!")


if __name__ == '__main__':
    asyncio.run(main())
