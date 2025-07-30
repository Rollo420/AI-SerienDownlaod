import asyncio
import json
import random

def load_json_data(filename):
    print("Loading JSON data...")
    with open(filename, 'r') as file:
        data = json.load(file)
    print("JSON data loaded successfully.")
    return data

async def get_series_data(serien):
    for serie in serien:
        # Serien Title
        title = serie["series_name"]
        for season in serie["seasons"]:
            # all Seasons
            season_number = season["season_number"]
            for episode in season["episode_links"]:
                # all Episodes
                episode_links = episode
                #await asyncio.sleep(random.uniform(2,5))  # Simulate processing time
                yield {
                    "title": title,
                    "season_number": season_number,
                    "episode_links": episode_links
                }

async def print_series_data(serien):
    print(json.dumps(serien, indent=4))
    await asyncio.sleep(random.uniform(1, 10))  # Simulate processing time
    
semaphore = asyncio.Semaphore(3)  # Limit concurrent tasks to 3
async def main():
    filename = "./UnitTest/Subprocess/all_series_data.json"
    serien = load_json_data(filename)

    print("Starting to process series data...\n")

    process_id = 0
    async for serie in get_series_data(serien):
        tasks = [print_series_data(episode) for key, episode in serie.items()]
        print(f"Task: {process_id}, Wait for semaphore to limit concurrent tasks...")
        async with semaphore:
            print(f"Task: {process_id}, Semaphore started")
            process_id += 1
            await asyncio.gather(*tasks)
            print(f"Task {process_id}, Semaphore finished")


if __name__ == "__main__":
    asyncio.run(main())
