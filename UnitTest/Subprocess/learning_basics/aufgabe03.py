import asyncio

async def long_task():
    print(f"Long task started...")
    await asyncio.sleep(5)
    print(f"Loing task finished")
    return f"Long Task Result"

async def short_task():
    print(f"Short task started...")
    await asyncio.sleep(2)
    print(f"Short task finished")
    return f"Short Task Result"

async def main():
    
    try:
       result01 = await asyncio.wait_for(long_task(), 2)
    except asyncio.TimeoutError as ATE:
        print(f"TimeoutError in Long Task: {ATE}")
        result01 = f"Long Task get a Timeout Error"
        
    result02 = await short_task()
    
    print(f"All results: \n{result01}\n{result02}")

if __name__ =='__main__':
    asyncio.run(main())
