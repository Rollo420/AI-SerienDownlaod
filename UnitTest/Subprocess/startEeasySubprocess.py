import asyncio
import json
import random
import subprocess
import sys

semaphore = asyncio.Semaphore(4)  # Limit concurrent tasks to 4




async def create_task(agent_name, data):
    print(
        f"Creating task for {data['title']} Season {data['season_number']} Episode {data['episode_links']['episode_number']} with {agent_name}..."
    )
    serienTitle = data["title"].replace(" ", "_").replace("/", "_")
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "/app/src/downloader/VOE.py",
        agent_name,
        data["episode_links"]["primary_link"],
        f"/app//serien/{serienTitle}/Season-{data['season_number']}/",
    )
async def start_task(agent_name, task):
    print(f"{agent_name}: Waiting for semaphore...")  # Klarere Ausgabe
    async with semaphore:
        print(f"{agent_name}: Acquired semaphore, starting task...")
        await asyncio.create_task(task)
        print(f"{agent_name}: Task completed.")
    
    return f"Task completed by {agent_name}."


async def main():
    filename = "/app/src/UnitTest/Subprocess/all_series_data.json"
    serien = load_json_data(filename)

    print("Starting to process series data...\n")

    tasks = []
    process_id = 0
    print(f"Creating tasks for {len(serien)} series...")
    async for serie in get_series_data(serien):
        process_id += 1
        agent_name = f"Agent-{process_id}"

        print(f"[{agent_name}] - Processing {serie['title']} Season {serie['season_number']} Episode {serie['episode_links']['episode_number']}...")
        tasks.append(await asyncio.create_task(create_task(agent_name, serie)))

        print(tasks)
        
        await asyncio.sleep(10)
        
        if process_id == 1:
            break
        
    await tasks[0].wait()  # Wait for the first task to complete


if __name__ == "__main__":
    asyncio.run(main())
