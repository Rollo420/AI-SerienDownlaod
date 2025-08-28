import asyncio

async def aufgabe_a():
    print('Start aufgabeb A')
    await asyncio.sleep(2)
    print('Beende aufgabe A')
    
async def aufgabe_b():
    print("start aufgabe B")
    await asyncio.sleep(1)
    print("beende aufgabe b")
    
    
async def main():
    await asyncio.gather(aufgabe_a(), aufgabe_b())
    
if __name__ == "__main__":
    asyncio.run(main())