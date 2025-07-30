import asyncio


async def aufgabe_a():
    print("Start aufgabeb A")
    await asyncio.sleep(2)
    print("Beende aufgabe A")
    return f"Ergebnis A"


async def aufgabe_b():
    print("start aufgabe B")
    await asyncio.sleep(1)
    print("beende aufgabe b")
    return f"Ergebnis B"

async def main():
    task02 = asyncio.create_task(aufgabe_b())
    task01 = asyncio.create_task(aufgabe_a())
    
    print(f"Hier kann noch was kommen aber ich weiß noch nciht was")
    
    print(f"Results ...\n")
    result01 = await task01
    result02 = await task02
    
    print(f"Result01: {result01}")
    print(f"Result02: {result02}")


if __name__ == "__main__":
    asyncio.run(main())
